"""Server-side trading policy.

The gateway's own unlock state answers "is this user allowed to trade?". This
policy answers a different question: "is this deployment allowed to issue
writes at all?". A read-only analysis deployment and a live-trading deployment
run the same code and can hold the same credentials, so the distinction has to
be configured explicitly rather than inferred from whether a password happens
to be present.

Enforcement lives in the service layer, before any gateway request, so it
applies to direct Python use of ``TradeService`` and not only to MCP calls.
"""

import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from moomoo_mcp.services.validation import FIXED_LIMIT_TYPES, NO_FIXED_LIMIT_TYPES

logger = logging.getLogger(__name__)

ENV_VAR = "MOOMOO_TRADING_MODE"
ENV_MAX_ORDER_QTY = "MOOMOO_MAX_ORDER_QTY"
ENV_MAX_ORDER_NOTIONAL = "MOOMOO_MAX_ORDER_NOTIONAL"
ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY = "MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY"
ENV_REAL_ACC_IDS = "MOOMOO_REAL_ACC_IDS"

# Markets whose instruments have been verified to quote in one known currency.
#
# A market prefix is a venue, not a currency. Instruments on one venue can quote
# in more than one -- HK dual-counter (HKD/RMB) is the standing example -- so a
# prefix only tells us the currency for a venue somebody has actually checked.
# An instrument outside this table is refused while a cap is configured, rather
# than valued at a guessed currency.
#
# `US` is provisional: task 1.1 of harden-trading-safeguards confirms it against
# the live gateway. Adding a market here is a deliberate act backed by
# verification.
VERIFIED_MARKET_CURRENCIES: Mapping[str, str] = {"US": "USD"}

# Classifications this server can value, and the monetary multiplier each uses.
# Equities quote in money per share, so one share is one unit of money times the
# price. Options quote per unit of the underlying while trading in contracts, so
# their multiplier is broker-reported and comes from the instrument facts.
EQUITY_CLASSIFICATIONS = frozenset({"STOCK", "ETF"})
OPTION_CLASSIFICATION = "DRVT"


class TradingMode(str, Enum):
    """What kind of writes a deployment is permitted to issue."""

    READ_ONLY = "READ_ONLY"
    SIMULATE = "SIMULATE"
    REAL = "REAL"


class TradingPolicyError(RuntimeError):
    """A write or unlock was refused by the configured trading mode or guardrails."""


class TradingModeConfigError(ValueError):
    """A trading configuration variable was set to an invalid value."""


# Which order environments each mode may write to. READ_ONLY writes nowhere.
_WRITABLE_ENVIRONMENTS: dict[TradingMode, frozenset[str]] = {
    TradingMode.READ_ONLY: frozenset(),
    TradingMode.SIMULATE: frozenset({"SIMULATE"}),
    TradingMode.REAL: frozenset({"SIMULATE", "REAL"}),
}


def _require_positive_finite(variable: str, label: str, value: float) -> float:
    """Require a configured limit to be a real, usable number.

    Raises:
        TradingModeConfigError: If the value is not finite or not above zero.
    """
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise TradingModeConfigError(
            f"{variable}: the {label} must be a finite number greater than 0, "
            f"got {value!r}."
        )
    return number


def _parse_notional_caps(raw: str) -> dict[str, float]:
    """Parse ``CURRENCY:AMOUNT[,CURRENCY:AMOUNT...]``.

    Done with ``split`` and ``partition`` rather than a regular expression, so
    each rejection names the entry that broke and the reason it broke, instead
    of reporting that the whole string did not match a pattern.

    Raises:
        TradingModeConfigError: If any entry is malformed, duplicated, or not a
            finite amount above zero.
    """
    caps: dict[str, float] = {}
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        currency, separator, amount = entry.partition(":")
        currency = currency.strip().upper()
        if not separator:
            raise TradingModeConfigError(
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: entry {entry!r} has no "
                "currency. Use CURRENCY:AMOUNT, for example USD:25000."
            )
        if len(currency) != 3 or not currency.isalpha():
            raise TradingModeConfigError(
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: {currency!r} is not a "
                "three-letter currency code."
            )
        if currency in caps:
            raise TradingModeConfigError(
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: {currency} appears more "
                "than once. A currency can have only one cap."
            )
        try:
            parsed = float(amount.strip())
        except ValueError as exc:
            raise TradingModeConfigError(
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: {amount.strip()!r} is not "
                f"a number for {currency}."
            ) from exc
        caps[currency] = _require_positive_finite(
            ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY, f"{currency} cap", parsed
        )
    if not caps:
        raise TradingModeConfigError(
            f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY} is set but holds no cap. "
            "Use CURRENCY:AMOUNT, for example USD:25000, or unset it."
        )
    return caps


def _parse_real_acc_ids(raw: str) -> frozenset[int]:
    """Parse a comma-separated list of decimal REAL account identifiers.

    Raises:
        TradingModeConfigError: If the list is empty or any entry is not a
            decimal identifier.
    """
    ids: set[int] = set()
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        if not entry.isdigit():
            raise TradingModeConfigError(
                f"{ENV_REAL_ACC_IDS}: {entry!r} is not a decimal account "
                "identifier. Get the identifiers from get_accounts."
            )
        ids.add(int(entry))
    if not ids:
        raise TradingModeConfigError(
            f"{ENV_REAL_ACC_IDS} is set but names no account. REAL mode requires "
            "the accounts REAL writes may target."
        )
    return frozenset(ids)


def currency_for_code(code: str | None) -> str | None:
    """The instrument's quote currency, from the verified-market table.

    Returns None for a market nobody has verified, which the assessment turns
    into a refusal rather than a guess.
    """
    if not code or "." not in code:
        return None
    market = code.split(".", 1)[0].strip().upper()
    return VERIFIED_MARKET_CURRENCIES.get(market)


@dataclass(frozen=True)
class InstrumentFacts:
    """What the policy needs to know about one instrument to value an order.

    Every numeric field is already normalized: the adapter that builds these has
    reduced each quote field to a number or to ``None``, so nothing here is the
    SDK's ``'N/A'`` sentinel, a non-finite float, or a non-positive price
    masquerading as one. That is what lets :meth:`TradingPolicy.assess_order`
    stay pure arithmetic with no gateway-shaped edge cases in it.

    ``currency`` is normally ``None``: the snapshot carries no currency field, so
    the assessment falls back to the verified-market table. The field exists so
    that a future SDK that does report a currency takes precedence without any
    other change.
    """

    code: str
    classification: str | None = None
    monetary_multiplier: float | None = None
    contract_size: float | None = None
    last_price: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    currency: str | None = None

    def market_reference(self) -> float | None:
        """`M`: the largest usable quoted price, or None when there is none.

        Normalization has already discarded everything that is not a positive
        finite number, so this is a maximum over whatever survived. No staleness
        threshold applies in this version.
        """
        candidates = [
            price
            for price in (self.last_price, self.bid_price, self.ask_price)
            if price is not None
        ]
        return max(candidates) if candidates else None


@dataclass(frozen=True)
class LegFacts:
    """One leg of an order: how much of it there is, and what it is."""

    instrument: InstrumentFacts
    qty_ratio: int = 1


@dataclass(frozen=True)
class OrderFacts:
    """An order reduced to the facts the limit assessment needs.

    A single-leg order carries exactly one leg with a ratio of 1. A combo
    carries one leg per leg and sets ``is_combo``, because the two are valued by
    different rules: a combo's price is the net package premium, not a per-share
    price.
    """

    order_type: str
    trd_side: str
    qty: int
    legs: Sequence[LegFacts]
    price: float | None = None
    aux_price: float | None = None
    is_combo: bool = False

    def largest_leg_qty(self) -> int:
        """The quantity the quantity cap applies to.

        For a combo this is the largest leg quantity, not the package count: a
        1:2:1 butterfly for 3 packages puts 6 contracts on its middle leg, and a
        cap of 5 is meant to stop that.
        """
        ratios = [leg.qty_ratio for leg in self.legs] or [1]
        return self.qty * max(ratios)


@dataclass(frozen=True)
class TradingPolicy:
    """The configured trading mode, safety limits, and checks that enforce them."""

    # Read-only by default, including for direct construction in scripts and
    # tests: a deployment that never states its intent should not be able to
    # send an order.
    mode: TradingMode = TradingMode.READ_ONLY
    max_order_qty: float | None = None
    # Currency -> cap. Empty means no notional cap is configured, which is what
    # switches instrument lookup off entirely.
    max_order_notional: Mapping[str, float] = field(default_factory=dict)
    # REAL accounts this deployment may write to. Empty outside REAL mode.
    real_acc_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        """Validate the limits, however the policy was built, and freeze them.

        Direct construction is covered too, not only ``from_env``. A limit of
        ``nan`` is the reason: every comparison against it is false, so an
        unvalidated ``nan`` does not raise anywhere -- it silently switches the
        limit off, which is the opposite of what configuring a limit means.

        The currency keys get the same check ``from_env`` applies, so a policy
        built in code cannot hold a cap under a key no instrument will ever be
        valued in -- a cap that silently applies to nothing.

        The mapping is then replaced with an immutable snapshot. ``frozen=True``
        stops the field being reassigned but not the dict behind it being
        mutated, so without this a caller could insert ``nan`` after validation
        and reintroduce exactly the comparison bypass above.
        """
        if self.max_order_qty is not None:
            _require_positive_finite(ENV_MAX_ORDER_QTY, "limit", self.max_order_qty)

        validated: dict[str, float] = {}
        for currency, amount in self.max_order_notional.items():
            code = str(currency).strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise TradingModeConfigError(
                    f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: {currency!r} is not a "
                    "three-letter currency code."
                )
            if code in validated:
                raise TradingModeConfigError(
                    f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}: {code} appears more "
                    "than once. A currency can have only one cap."
                )
            validated[code] = _require_positive_finite(
                ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY, f"{code} cap", amount
            )
        object.__setattr__(self, "max_order_notional", MappingProxyType(validated))

    @property
    def notional_cap_configured(self) -> bool:
        """Whether any notional cap is set.

        When nothing is set, no instrument data is fetched for limit assessment
        at all -- the design's rule that an unconfigured cap costs no latency
        and adds no quote dependency.
        """
        return bool(self.max_order_notional)

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "TradingPolicy":
        """Build a policy from environment variables.

        Reads ``MOOMOO_TRADING_MODE``, ``MOOMOO_MAX_ORDER_QTY``,
        ``MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY``, ``MOOMOO_REAL_ACC_IDS``, and
        the legacy ``MOOMOO_MAX_ORDER_NOTIONAL``.

        Args:
            environ: Environment mapping to read. Defaults to ``os.environ``.

        Returns:
            The configured policy, defaulting to READ_ONLY mode.

        Raises:
            TradingModeConfigError: If any configuration variable holds an unknown
                or invalid value.
        """
        env = environ if environ is not None else os.environ

        # 1. Parse trading mode
        raw_mode = env.get(ENV_VAR)
        mode = TradingMode.READ_ONLY
        if raw_mode is not None and raw_mode.strip():
            candidate = raw_mode.strip().upper()
            try:
                mode = TradingMode(candidate)
            except ValueError as exc:
                valid = ", ".join(m.value for m in TradingMode)
                raise TradingModeConfigError(
                    f"{ENV_VAR} is set to {raw_mode!r}, which is not a trading mode. "
                    f"Valid values: {valid}."
                ) from exc

        # 2. Parse optional max order quantity limit
        raw_qty = env.get(ENV_MAX_ORDER_QTY)
        max_order_qty: float | None = None
        if raw_qty is not None and raw_qty.strip():
            try:
                max_order_qty = float(raw_qty.strip())
            except ValueError as exc:
                raise TradingModeConfigError(
                    f"{ENV_MAX_ORDER_QTY} must be a positive number, got {raw_qty!r}."
                ) from exc
            _require_positive_finite(ENV_MAX_ORDER_QTY, "limit", max_order_qty)

        # 3. Parse the currency-qualified notional caps, and classify the legacy
        #    variable. The legacy value is never parsed as a limit: a cap with no
        #    unit cannot be applied to an instrument whose currency we now know.
        raw_by_currency = (env.get(ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY) or "").strip()
        raw_legacy = (env.get(ENV_MAX_ORDER_NOTIONAL) or "").strip()
        max_order_notional: dict[str, float] = {}
        if raw_by_currency:
            max_order_notional = _parse_notional_caps(raw_by_currency)
            if raw_legacy:
                # Both set is the rollback-friendly arrangement: the old image
                # reads the legacy variable, this one reads the new one, and one
                # environment file serves both.
                logger.info(
                    f"{ENV_MAX_ORDER_NOTIONAL} is set and ignored; "
                    f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY} is in effect."
                )
        elif raw_legacy:
            raise TradingModeConfigError(
                f"{ENV_MAX_ORDER_NOTIONAL} is set on its own. A notional cap "
                "without a currency cannot be applied, because an order's value "
                "is measured in the instrument's own currency. Set "
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY} instead, for example "
                "USD:25000. Leaving the legacy variable in place alongside it is "
                "supported, and is what makes a rollback need no environment edit."
            )

        # 4. The REAL account allowlist. Required in REAL mode, ignored otherwise:
        #    a SIMULATE deployment listing REAL accounts would be stating an
        #    intent it cannot act on.
        raw_acc_ids = (env.get(ENV_REAL_ACC_IDS) or "").strip()
        real_acc_ids: frozenset[int] = frozenset()
        if mode is TradingMode.REAL:
            if not raw_acc_ids:
                raise TradingModeConfigError(
                    f"{ENV_REAL_ACC_IDS} is required when {ENV_VAR} is REAL. It "
                    "names the accounts REAL writes may target, as a "
                    "comma-separated list of the identifiers get_accounts "
                    "reports."
                )
            real_acc_ids = _parse_real_acc_ids(raw_acc_ids)

        return cls(
            mode=mode,
            max_order_qty=max_order_qty,
            max_order_notional=max_order_notional,
            real_acc_ids=real_acc_ids,
        )

    def check_write(self, operation: str, trd_env: str) -> None:
        """Authorize an order mutation in ``trd_env``, or refuse it.

        The requested environment is never rewritten to one the policy would
        allow: rerouting a REAL order into a simulated account would silently
        do something other than what the caller asked for.

        Args:
            operation: Name of the operation, used in the error message.
            trd_env: Requested trading environment, 'REAL' or 'SIMULATE'.

        Raises:
            TradingPolicyError: If the mode does not permit this write.
        """
        requested = str(trd_env).strip().upper()
        if requested in _WRITABLE_ENVIRONMENTS[self.mode]:
            return

        if self.mode is TradingMode.READ_ONLY:
            detail = (
                "This server is configured read-only, which blocks placing, "
                "modifying, and cancelling orders in every environment."
            )
        elif requested in {"REAL", "SIMULATE"}:
            detail = (
                f"This server is configured for {self.mode.value} trading, "
                f"which does not permit {requested} writes."
            )
        else:
            detail = (
                f"{requested!r} is not a recognized trading environment; "
                "use 'REAL' or 'SIMULATE'."
            )

        raise TradingPolicyError(
            f"{operation} is not permitted: {detail} Set {ENV_VAR} to the "
            "intended mode and restart the server to change this."
        )

    def assess_order(self, operation: str, facts: OrderFacts) -> None:
        """Enforce the quantity and notional guardrails on one order.

        Pure: it does arithmetic over the numbers it is handed and reaches
        nothing. Gathering the facts -- the snapshot, the classification, the
        existing order behind a modification -- is the trade service's job, so
        that this stays unit-testable and has no gateway in it.

        It fails closed. When a configured cap cannot be evaluated for an order,
        the order is refused rather than permitted: a guardrail that waves an
        order through because it could not price it is not a guardrail.

        Args:
            operation: Name of the operation, used in every refusal message.
            facts: The order reduced to what the assessment needs.

        Raises:
            TradingPolicyError: If the order exceeds a limit, or if a configured
                notional cap cannot be evaluated for it.
        """
        if self.max_order_qty is not None:
            checked_qty = facts.largest_leg_qty()
            if checked_qty > self.max_order_qty:
                what = "largest leg quantity" if facts.is_combo else "order quantity"
                raise TradingPolicyError(
                    f"{operation} rejected by trading guardrail: {what} "
                    f"{checked_qty} exceeds the configured maximum of "
                    f"{self.max_order_qty:g}."
                )

        if not self.notional_cap_configured:
            return

        if facts.is_combo:
            value, currency, label = self._assess_combo_premium(operation, facts)
        else:
            value, currency, label = self._assess_single_leg(operation, facts)

        cap = self.max_order_notional.get(currency)
        if cap is None:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the instrument is "
                f"valued in {currency}, which has no configured cap in "
                f"{ENV_MAX_ORDER_NOTIONAL_BY_CURRENCY}. While any notional cap "
                "is configured, an order in an uncapped currency is refused."
            )
        if value > cap:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: {label} "
                f"{value:,.2f} {currency} exceeds the configured maximum of "
                f"{cap:,.2f} {currency}."
            )

    def _instrument_currency(self, operation: str, instrument: InstrumentFacts) -> str:
        """The instrument's quote currency, or a refusal.

        The snapshot carries no currency field, so this falls back to the
        verified-market table. ``instrument.currency`` wins when a source that
        actually knows has filled it in.
        """
        currency = instrument.currency or currency_for_code(instrument.code)
        if currency is None:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: cannot establish the "
                f"currency {instrument.code} quotes in. A market prefix is a "
                "venue, not a currency, so only verified markets "
                f"({', '.join(sorted(VERIFIED_MARKET_CURRENCIES))}) can be "
                "valued. Remove the notional cap to place this order."
            )
        return currency

    def _monetary_multiplier(
        self, operation: str, instrument: InstrumentFacts
    ) -> float:
        """How much money one unit of quantity is worth per unit of price.

        One for equities, which quote in money per share. Broker-reported for
        options, which quote per unit of the underlying while trading in
        contracts.

        Raises:
            TradingPolicyError: If the classification is missing or unsupported,
                or an option's multiplier has not been established.
        """
        classification = instrument.classification
        if classification is None:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the security "
                f"classification of {instrument.code} could not be established, "
                "so its value cannot be computed."
            )
        if classification in EQUITY_CLASSIFICATIONS:
            return 1.0
        if classification == OPTION_CLASSIFICATION:
            multiplier = instrument.monetary_multiplier
            if multiplier is None:
                raise TradingPolicyError(
                    f"{operation} rejected by trading guardrail: no verified "
                    f"monetary multiplier for {instrument.code}, so one contract "
                    "cannot be converted into money. Remove the notional cap to "
                    "place this order."
                )
            return multiplier
        raise TradingPolicyError(
            f"{operation} rejected by trading guardrail: {instrument.code} is "
            f"classified {classification}, which this server does not value. "
            f"Supported: {', '.join(sorted(EQUITY_CLASSIFICATIONS))}, "
            f"{OPTION_CLASSIFICATION}."
        )

    def _reference_price(self, operation: str, facts: OrderFacts) -> float:
        """The price a single-leg order's value is measured at.

        | Side | Class          | Reference                                  |
        | ---- | -------------- | ------------------------------------------ |
        | BUY  | fixed-limit    | the limit price                            |
        | SELL | fixed-limit    | max(price, M)                              |
        | any  | no-fixed-limit | max(M, aux_price, price), M required       |

        A BUY limit is the only row that needs no market data, because only
        there does the limit bound the fill price from above: the order cannot
        cost more than it asks to. A SELL limit bounds the fill from below, so
        the market supplies the upper side. An order with no fixed limit has no
        bound at all, so the estimate takes the market and whatever price or
        trigger the caller supplied -- adding a candidate can only raise it,
        never lower it below `M`.

        Raises:
            TradingPolicyError: If the order type is in neither class, or the
                rule needs a market reference and none is available.
        """
        order_type = str(facts.order_type).strip().upper()
        side = str(facts.trd_side).strip().upper()
        instrument = facts.legs[0].instrument
        market_reference = instrument.market_reference()

        if order_type in FIXED_LIMIT_TYPES:
            price = facts.price
            if price is None or price <= 0:
                raise TradingPolicyError(
                    f"{operation} rejected by trading guardrail: {order_type} is a "
                    "limit order type, so its value is measured at its limit "
                    f"price, and none was supplied ({price!r})."
                )
            if side == "BUY":
                return float(price)
            return max(
                float(price),
                self._require_market_reference(operation, instrument, market_reference),
            )

        if order_type in NO_FIXED_LIMIT_TYPES:
            reference = self._require_market_reference(
                operation, instrument, market_reference
            )
            candidates = [reference]
            for candidate in (facts.aux_price, facts.price):
                if candidate is not None and candidate > 0:
                    candidates.append(float(candidate))
            return max(candidates)

        raise TradingPolicyError(
            f"{operation} rejected by trading guardrail: order type {order_type} "
            "is not classified as either a fixed-limit or a no-fixed-limit type, "
            "so its value cannot be computed. Remove the notional cap to place "
            "this order."
        )

    def _require_market_reference(
        self, operation: str, instrument: InstrumentFacts, reference: float | None
    ) -> float:
        """The market reference, or the refusal that says it is missing."""
        if reference is None:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: no usable market "
                f"price for {instrument.code}. None of last, bid or ask is a "
                "positive number, so this order's value cannot be estimated. "
                "Remove the notional cap to place this order."
            )
        return reference

    def _assess_single_leg(
        self, operation: str, facts: OrderFacts
    ) -> tuple[float, str, str]:
        """Value a single-leg order: reference price x quantity x multiplier."""
        if not facts.legs:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: no instrument facts "
                "were available for the order, so its value cannot be computed."
            )
        instrument = facts.legs[0].instrument
        currency = self._instrument_currency(operation, instrument)
        multiplier = self._monetary_multiplier(operation, instrument)
        reference = self._reference_price(operation, facts)
        return reference * facts.qty * multiplier, currency, "order notional"

    def _assess_combo_premium(
        self, operation: str, facts: OrderFacts
    ) -> tuple[float, str, str]:
        """Value a combo as its package premium.

        This is premium, not maximum loss. A short option package can lose far
        more than the premium it collects; capping premium bounds what the
        package costs to open, not what it can cost to hold. Maximum-loss
        modelling is a later change, and the error text says "package premium"
        so nobody reads this as more than it is.
        """
        order_type = str(facts.order_type).strip().upper()
        if order_type not in FIXED_LIMIT_TYPES:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: a combo with order "
                f"type {order_type} has no fixed net limit price, so its package "
                "premium cannot be computed. Remove the notional cap to place "
                "this order."
            )
        if not facts.legs:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: no instrument facts "
                "were available for the combo's legs."
            )
        if facts.price is None:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the combo's net "
                "price is required to compute its package premium."
            )

        currencies = set()
        multipliers = set()
        contract_sizes = set()
        for leg in facts.legs:
            instrument = leg.instrument
            if instrument.classification != OPTION_CLASSIFICATION:
                raise TradingPolicyError(
                    f"{operation} rejected by trading guardrail: leg "
                    f"{instrument.code} is classified "
                    f"{instrument.classification!r}, and a combo priced as a "
                    "package premium must be options only. A stock leg makes the "
                    "package premium meaningless."
                )
            currencies.add(self._instrument_currency(operation, instrument))
            multipliers.add(self._monetary_multiplier(operation, instrument))
            contract_sizes.add(instrument.contract_size)

        if len(currencies) > 1:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the combo's legs "
                f"are valued in more than one currency ({sorted(currencies)}), "
                "so one package premium cannot be stated."
            )
        if len(multipliers) > 1:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the combo's legs "
                f"have differing monetary multipliers ({sorted(multipliers)}), "
                "so the net price does not describe one package."
            )
        if len(contract_sizes) > 1:
            raise TradingPolicyError(
                f"{operation} rejected by trading guardrail: the combo's legs "
                f"have differing contract sizes ({sorted(contract_sizes)}), so "
                "they are not compatible in one package."
            )

        multiplier = next(iter(multipliers))
        currency = next(iter(currencies))
        premium = abs(float(facts.price)) * facts.qty * multiplier
        return premium, currency, "package premium"

    def check_unlock(self, operation: str = "unlock_trade") -> None:
        """Authorize unlocking trade, or refuse it.

        Only REAL mode may unlock. Holding a password does not promote a mode:
        credentials say who the operator is, not what this deployment is for.

        Raises:
            TradingPolicyError: If the mode does not permit unlocking.
        """
        if self.mode is TradingMode.REAL:
            return
        raise TradingPolicyError(
            f"{operation} is not permitted: this server is configured for "
            f"{self.mode.value}, and only REAL mode may unlock trading. A "
            f"configured trade password does not change the mode. Set {ENV_VAR} "
            "to REAL and restart the server to change this."
        )
