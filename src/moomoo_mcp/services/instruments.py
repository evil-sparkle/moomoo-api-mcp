"""The instrument facts the notional assessment needs, gathered from the gateway.

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
from collections.abc import Callable, Sequence
from typing import Any

from moomoo import RET_OK, OpenQuoteContext

from moomoo_mcp.services.trading_policy import (
    OPTION_CLASSIFICATION,
    InstrumentFacts,
)

# Which snapshot field carries an option's monetary multiplier — the number that
# turns a quoted price into the cash value of one contract.
#
# Deliberately unset. The SDK offers two candidates and its own field table
# describes them as:
#
#   option_contract_size        每份合约数                    units per contract
#   option_contract_multiplier  合约乘数，指数期权特有字段      index options only
#
# That points at `option_contract_size`, and says `option_contract_multiplier`
# would not even be populated for a US equity option. It is documentary evidence
# rather than the coincidence of a field equalling 100, which is what task 1.1
# of the harden-trading-safeguards change rules out as a basis for choosing.
#
# What it is not is the independent cross-check task 1.1 requires: confirming the
# field against a figure established another way, such as an option position's
# own market value. That needs an account, and until it is done an option is not
# assessable and is refused while a notional cap is configured.
#
# Setting this to "option_contract_size" is the whole change once that check
# lands. See openspec/changes/harden-trading-safeguards/verification.md.
OPTION_MONETARY_MULTIPLIER_FIELD: str | None = None

# The classifications this server can value. Not a query filter: with an
# explicit code_list the gateway returns each instrument's own stock_type in
# one call, and the policy decides what it will value.
VALUABLE_CLASSIFICATIONS = ("STOCK", "ETF", "DRVT")

InstrumentLookup = Callable[[Sequence[str]], list[InstrumentFacts]]


class InstrumentLookupError(RuntimeError):
    """A valuation fact could not be established for one of the order's codes."""


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

    def __init__(self, quote_ctx_provider: Callable[[], OpenQuoteContext | None]):
        """
        Args:
            quote_ctx_provider: Returns the shared quote context, or None when
                it is not connected. It is a callable rather than the context
                itself because the connection is opened lazily and replaced on
                reconnect; holding the object would pin the one that existed at
                wiring time.
        """
        self._quote_ctx_provider = quote_ctx_provider

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
        classifications = self._classifications(quote_ctx, wanted)

        facts: list[InstrumentFacts] = []
        for code in wanted:
            snapshot = snapshots.get(code)
            if snapshot is None:
                raise InstrumentLookupError(
                    f"get_market_snapshot did not return {code}, so its price "
                    "is unknown"
                )
            classification = classifications.get(code)
            if classification is None:
                raise InstrumentLookupError(
                    f"get_stock_basicinfo did not return {code}, so its security "
                    "classification is unknown and it cannot be valued"
                )
            facts.append(self._facts(code, classification, snapshot))
        return facts

    def _snapshots(
        self, quote_ctx: OpenQuoteContext, codes: list[str]
    ) -> dict[str, dict[str, Any]]:
        ret, data = quote_ctx.get_market_snapshot(codes)
        rows = _rows("get_market_snapshot", ret, data)
        return {str(row.get("code")): row for row in rows}

    def _classifications(
        self, quote_ctx: OpenQuoteContext, codes: list[str]
    ) -> dict[str, str]:
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

        found: dict[str, str] = {}
        for market, group in by_market.items():
            # stock_type is ignored while code_list is non-empty, as above. It
            # is passed only because the parameter is positional-ish in the SDK
            # signature; every returned row carries its own stock_type, which is
            # what gets read.
            ret, data = quote_ctx.get_stock_basicinfo(
                market=market, stock_type="STOCK", code_list=group
            )
            rows = _rows(f"get_stock_basicinfo({market})", ret, data)
            for row in rows:
                code = str(row.get("code"))
                reported = row.get("stock_type")
                if code in group and isinstance(reported, str) and reported:
                    found[code] = reported.strip().upper()
        return found

    def _facts(
        self, code: str, classification: str, snapshot: dict[str, Any]
    ) -> InstrumentFacts:
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
        )

    def _monetary_multiplier(
        self, code: str, classification: str, snapshot: dict[str, Any]
    ) -> float | None:
        """The multiplier that turns a quoted price into money, or a refusal.

        Equities quote in money per share, so the multiplier is one. Options do
        not, and which broker field carries their monetary semantics is not yet
        verified — see :data:`OPTION_MONETARY_MULTIPLIER_FIELD`.
        """
        if classification != OPTION_CLASSIFICATION:
            # Not an option: the policy decides whether this classification can
            # be valued at all, and refuses the ones it cannot.
            return None if classification not in {"STOCK", "ETF"} else 1.0

        if OPTION_MONETARY_MULTIPLIER_FIELD is None:
            raise InstrumentLookupError(
                f"{code} is an option, and which broker field carries an "
                "option's monetary multiplier has not been verified against an "
                "independent figure. Valuing it would mean guessing what one "
                "contract is worth, so it is refused while a notional cap is "
                "configured. Remove the cap to place this order."
            )

        multiplier = normalize_quote(snapshot.get(OPTION_MONETARY_MULTIPLIER_FIELD))
        if multiplier is None:
            raise InstrumentLookupError(
                f"{code}: the gateway reported no usable "
                f"{OPTION_MONETARY_MULTIPLIER_FIELD}, so one contract cannot be "
                "converted into money"
            )
        return multiplier
