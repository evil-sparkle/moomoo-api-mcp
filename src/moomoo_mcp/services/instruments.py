"""Instrument validation and valuation facts gathered from the gateway.

:meth:`TradingPolicy.assess_order` is pure arithmetic over numbers. This is what
turns a gateway into those numbers, and it is deliberately the only place that
knows how untidy the gateway's answers are:

- the snapshot carries no security classification and no currency, so the
  classification comes from a second call and the currency from the policy's
  verified-market table;
- a price the gateway omitted arrives as the string ``'N/A'``, not as a number
  or a null;
- an option's contract field arrives as ``0.0`` when it was never sent, because
  the SDK copies it out of an optional protobuf field without checking whether
  it was populated.

Everything above is normalized here, so a missing fact reaches the policy as
``None`` and is refused, rather than reaching arithmetic as a string or as a
zero that would value an option at nothing.

This sits inside the pre-dispatch boundary. Every failure here — either call
failing, a code the gateway did not return, a fact that cannot be established —
is a refusal before the SDK write, reported as *not sent*. No path through this
module can produce an unknown outcome.
"""

from __future__ import annotations

import math
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from moomoo import RET_OK, OpenQuoteContext

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.trading_policy import (
    OPTION_CLASSIFICATION,
    InstrumentFacts,
)

# OpenD documents the monetary multiplier separately from deliverable size.
# Official definitions and independent live position arithmetic are recorded in
# openspec/changes/archive/2026-09-24-harden-trading-safeguards/verification.md
# (observations dated 2026-09-23).
# Never substitute option_contract_size or a hardcoded 100 for a missing value.
OPTION_MONETARY_MULTIPLIER_FIELD = "option_contract_multiplier"

# The classifications this server can value. Not a query filter: with an
# explicit code_list the gateway returns each instrument's own stock_type in
# one call, and the policy decides what it will value.
VALUABLE_CLASSIFICATIONS = ("STOCK", "ETF", "DRVT")

InstrumentLookup = Callable[[Sequence[str]], list[InstrumentFacts]]


class InstrumentLookupError(RuntimeError):
    """A valuation fact could not be established for one of the order's codes."""


@dataclass(frozen=True)
class _StaticFacts:
    classification: str
    lot_size: float | None
    expires_at: float
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: str | None = None


def normalize_quote(value: Any) -> float | None:
    """Reduce a gateway quote field to a positive finite number, or to None.

    Absent, non-numeric (the SDK's ``'N/A'`` sentinel included), non-finite and
    non-positive values all become None, which the policy treats as "this fact
    is not available" and refuses on when it needs it.

    Zero is folded in with the rest on purpose. A zero price is not a price, and
    a zero multiplier is very likely a field the gateway never sent: the SDK
    reads ``option_contract_size`` out of an optional protobuf field with no
    presence check, so "not sent" and "zero" arrive as the same ``0.0``. Valuing
    an option at a zero multiplier would price every contract at nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _rows(operation: str, ret: Any, data: Any) -> list[dict[str, Any]]:
    """Turn one SDK response into rows, or refuse."""
    if ret != RET_OK:
        raise InstrumentLookupError(f"{operation} failed: {data}")
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(row) for row in data]
    to_dict = getattr(data, "to_dict", None)
    if to_dict is None:
        raise InstrumentLookupError(
            f"{operation} reported success but returned "
            f"{type(data).__name__}, which carries no rows"
        )
    return [dict(row) for row in to_dict("records")]


class InstrumentAdapter:
    """Builds :class:`InstrumentFacts` for the codes an order names."""

    def __init__(
        self,
        quote_ctx_provider: Callable[[], OpenQuoteContext | None],
        *,
        dispatcher: BrokerRequestDispatcher | None = None,
        metadata_ttl: float = 300.0,
        metadata_capacity: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ):
        """
        Args:
            quote_ctx_provider: Returns the shared quote context, or None when
                it is not connected. It is a callable rather than the context
                itself because the connection is opened lazily and replaced on
                reconnect; holding the object would pin the one that existed at
                wiring time.
            metadata_ttl: Maximum age of static classification/lot metadata in
                seconds. Zero disables reuse. Price and multiplier snapshots
                are requested anew on every lookup regardless of this value.
            metadata_capacity: Maximum number of cached static instruments.
            clock: Monotonic clock used only for metadata expiry.
        """
        self._quote_ctx_provider = quote_ctx_provider
        self.dispatcher = dispatcher or BrokerRequestDispatcher()
        if (
            isinstance(metadata_ttl, bool)
            or not math.isfinite(metadata_ttl)
            or metadata_ttl < 0
        ):
            raise ValueError("metadata_ttl must be a finite non-negative number")
        if (
            isinstance(metadata_capacity, bool)
            or not isinstance(metadata_capacity, int)
            or metadata_capacity < 1
        ):
            raise ValueError("metadata_capacity must be positive")
        self._metadata_ttl = metadata_ttl
        self._metadata_capacity = metadata_capacity
        self._clock = clock
        self._metadata_lock = threading.Lock()
        self._metadata_context: weakref.ReferenceType[OpenQuoteContext] | None = None
        self._metadata: OrderedDict[str, _StaticFacts] = OrderedDict()

    def __call__(self, codes: Sequence[str]) -> list[InstrumentFacts]:
        """Gather the valuation facts for ``codes``, in the order given.

        Raises:
            InstrumentLookupError: If either gateway call fails, a requested
                code is not returned, or a required fact cannot be established.
        """
        wanted = list(dict.fromkeys(codes))
        if not wanted:
            raise InstrumentLookupError("no instrument codes were supplied")

        quote_ctx = self._quote_ctx_provider()
        if quote_ctx is None:
            raise InstrumentLookupError(
                "the quote connection is not available, so the order cannot be "
                "valued against its configured notional cap"
            )

        snapshots = self._snapshots(quote_ctx, wanted)
        static = self._static_facts(quote_ctx, wanted)

        facts: list[InstrumentFacts] = []
        for code in wanted:
            snapshot = snapshots.get(code)
            if snapshot is None:
                raise InstrumentLookupError(
                    f"get_market_snapshot did not return {code}, so its price "
                    "is unknown"
                )
            metadata = static.get(code)
            if metadata is None:
                raise InstrumentLookupError(
                    f"get_stock_basicinfo did not return {code}, so its security "
                    "classification is unknown and it cannot be valued"
                )
            facts.append(self._facts(code, metadata, snapshot))
        return facts

    def _snapshots(
        self, quote_ctx: OpenQuoteContext, codes: list[str], *, strict: bool = False
    ) -> dict[str, dict[str, Any]]:
        ret, data = self.dispatcher.call(
            "get_market_snapshot", quote_ctx.get_market_snapshot, codes
        )
        rows = _rows("get_market_snapshot", ret, data)
        returned = [str(row.get("code")) for row in rows]
        if strict and (
            len(set(returned)) != len(returned) or set(returned) - set(codes)
        ):
            raise InstrumentLookupError("Snapshot has duplicate/unexpected codes")
        return {str(row.get("code")): row for row in rows}

    def read_facts(self, codes: list[str]) -> list[dict[str, Any]]:
        """Expose reference facts using the same static cache and fresh snapshots.

        This observational path permits incomplete metadata; valuation remains
        fail-closed in __call__. A fact's source and age are retained separately.
        No lifecycle assumption is reconstructed from the contract symbol.
        """
        from moomoo_mcp.services.data_evidence import validate_codes

        validate_codes(codes)
        context = self._quote_ctx_provider()
        if context is None:
            raise InstrumentLookupError("Quote context not connected")
        snapshot_started = utc_now_iso()
        snapshots = self._snapshots(context, codes, strict=True)
        snapshot_received = utc_now_iso()
        static = self._static_facts(context, codes)
        fields = {
            "is_option": "option_valid",
            "option_type": "option_type",
            "underlying": "stock_owner",
            "expiry": "strike_time",
            "strike": "option_strike_price",
            "classification": "stock_type",
            "lot_size": "lot_size",
            "currency": "currency",
            "exercise_style": "option_area_type",
            "settlement_mode": "option_settlement_mode",
            "settlement_cutoff": "settlement_cutoff",
            "deliverable_size": "option_contract_size",
            "deliverable_description": "deliverable_description",
            "monetary_multiplier": OPTION_MONETARY_MULTIPLIER_FIELD,
            "standard_status": "option_standard_type",
        }
        result = []
        for code in codes:
            metadata = static.get(code)
            snapshot = snapshots.get(code, {})
            reference = metadata.raw if metadata else {}
            facts = {}
            for name, key in fields.items():
                observations = []
                for method, record, observed_at in (
                    (
                        "get_stock_basicinfo",
                        reference,
                        metadata.observed_at if metadata else None,
                    ),
                    ("get_market_snapshot", snapshot, snapshot_received),
                ):
                    value = record.get(key)
                    if isinstance(value, float) and not math.isfinite(value):
                        continue
                    if value is None or (
                        isinstance(value, str) and value in {"", "N/A"}
                    ):
                        continue
                    if name in {"lot_size", "deliverable_size", "monetary_multiplier"}:
                        value = normalize_quote(value)
                        if value is None:
                            continue
                    observations.append(
                        {
                            "source_method": method,
                            "value": value,
                            "observed_at": observed_at,
                        }
                    )
                conflict = any(
                    item["value"] != observations[0]["value"]
                    for item in observations[1:]
                )
                facts[name] = {
                    "state": "CONFLICT"
                    if conflict
                    else "KNOWN"
                    if observations
                    else "UNKNOWN",
                    "value": observations[0]["value"]
                    if observations and not conflict
                    else None,
                    "evidence": observations,
                }
            result.append(
                {
                    "code": code,
                    "facts": facts,
                    "snapshot_requested_at": snapshot_started,
                    "snapshot_received_at": snapshot_received,
                    "snapshot_present": code in snapshots,
                    "reference_present": code in static,
                    "static_cache_ttl_seconds": self._metadata_ttl,
                    "compatibility_verified": False,
                }
            )
        return result

    def _static_facts(
        self, quote_ctx: OpenQuoteContext, codes: list[str]
    ) -> dict[str, _StaticFacts]:
        """Cache only basic classification/lot metadata, never market quotes.

        The weak reference neither pins a retired context nor mistakes a reused
        object ID for the old connection. A static miss is serialized so several
        simultaneous orders cannot fan out identical reference-data requests.
        Missing rows and failures are never cached as successful metadata.
        """
        with self._metadata_lock:
            previous = self._metadata_context() if self._metadata_context else None
            cacheable = True
            if previous is not quote_ctx:
                self._metadata.clear()
                try:
                    self._metadata_context = weakref.ref(quote_ctx)
                except TypeError:
                    self._metadata_context = None
                    cacheable = False
            now = self._clock()
            expired = [
                code for code, item in self._metadata.items() if item.expires_at <= now
            ]
            for code in expired:
                del self._metadata[code]
            found = {
                code: self._metadata[code] for code in codes if code in self._metadata
            }
            for code in found:
                self._metadata.move_to_end(code)
            missing = [code for code in codes if code not in found]
            if missing:
                fresh = self._classifications(quote_ctx, missing)
                found.update(fresh)
                if cacheable and self._metadata_ttl > 0:
                    for code, item in fresh.items():
                        self._metadata[code] = item
                        self._metadata.move_to_end(code)
                    while len(self._metadata) > self._metadata_capacity:
                        self._metadata.popitem(last=False)
            return found

    def _classifications(
        self, quote_ctx: OpenQuoteContext, codes: list[str]
    ) -> dict[str, _StaticFacts]:
        """Ask for the classification of exactly these codes, in one call.

        One call per market, not one per candidate type. With a non-empty
        ``code_list`` the SDK's request packer sets ``market = 0`` and
        ``secType = 0`` and lets the explicit security list drive the query, so
        the ``market`` and ``stock_type`` arguments are ignored and asking three
        times under three types sent the identical request three times. That
        tripled the latency on the order path and tripled the chance of a
        fail-closed refusal, for nothing.

        ``verify_sdk_facts.py`` checks that packer behaviour, so an SDK change
        that starts honouring ``stock_type`` fails loudly rather than silently
        narrowing what comes back here.

        The explicit ``code_list`` is still what keeps this a targeted read
        rather than an enumeration of every instrument on a venue.
        """
        by_market: dict[str, list[str]] = {}
        for code in codes:
            market, _, rest = code.partition(".")
            if not rest:
                raise InstrumentLookupError(
                    f"{code!r} carries no market prefix, so its classification "
                    "cannot be requested"
                )
            by_market.setdefault(market.strip().upper(), []).append(code)

        found: dict[str, _StaticFacts] = {}
        for market, group in by_market.items():
            # stock_type is ignored while code_list is non-empty, as above. It
            # is passed only because the parameter is positional-ish in the SDK
            # signature; every returned row carries its own stock_type, which is
            # what gets read.
            ret, data = self.dispatcher.call(
                "get_stock_basicinfo",
                quote_ctx.get_stock_basicinfo,
                market=market,
                stock_type="STOCK",
                code_list=group,
            )
            rows = _rows(f"get_stock_basicinfo({market})", ret, data)
            for row in rows:
                code = str(row.get("code"))
                reported = row.get("stock_type")
                if code in group and isinstance(reported, str) and reported.strip():
                    found[code] = _StaticFacts(
                        classification=reported.strip().upper(),
                        lot_size=normalize_quote(row.get("lot_size")),
                        expires_at=self._clock() + self._metadata_ttl,
                        raw=dict(row),
                        observed_at=utc_now_iso(),
                    )
        return found

    def _facts(
        self, code: str, metadata: _StaticFacts, snapshot: dict[str, Any]
    ) -> InstrumentFacts:
        classification = metadata.classification
        multiplier = self._monetary_multiplier(code, classification, snapshot)
        return InstrumentFacts(
            code=code,
            classification=classification,
            monetary_multiplier=multiplier,
            contract_size=normalize_quote(snapshot.get("option_contract_size")),
            last_price=normalize_quote(snapshot.get("last_price")),
            bid_price=normalize_quote(snapshot.get("bid_price")),
            ask_price=normalize_quote(snapshot.get("ask_price")),
            # The snapshot has no currency field; the policy's verified-market
            # table supplies it. Left as None so that a future SDK which does
            # report one takes precedence without any other change.
            currency=None,
            lot_size=metadata.lot_size,
        )

    def _monetary_multiplier(
        self, code: str, classification: str, snapshot: dict[str, Any]
    ) -> float | None:
        """The multiplier that turns a quoted price into money, or a refusal.

        Equities quote in money per share, so the multiplier is one. Options do
        not; use their documented premium multiplier, keeping deliverable size
        separate for combo compatibility checks.
        """
        if classification != OPTION_CLASSIFICATION:
            # Not an option: the policy decides whether this classification can
            # be valued at all, and refuses the ones it cannot.
            return None if classification not in {"STOCK", "ETF"} else 1.0

        multiplier = normalize_quote(snapshot.get(OPTION_MONETARY_MULTIPLIER_FIELD))
        if multiplier is None:
            raise InstrumentLookupError(
                f"{code}: the gateway reported no usable "
                f"{OPTION_MONETARY_MULTIPLIER_FIELD}, so one contract cannot be "
                "converted into money"
            )
        return multiplier
