"""Documented representation rules, distinct from unknown product tick sizes."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from moomoo import Session

from moomoo_mcp.services.trading_policy import InstrumentFacts
from moomoo_mcp.services.validation import FIXED_LIMIT_TYPES, NO_FIXED_LIMIT_TYPES

EQUITY_CLASSIFICATIONS = frozenset({"STOCK", "ETF"})
ORDER_CLASSIFICATIONS = EQUITY_CLASSIFICATIONS | {"DRVT"}
DOCUMENTED_SESSIONS = (
    Session.NONE,
    Session.RTH,
    Session.ETH,
    Session.OVERNIGHT,
    Session.ALL,
)


def _number(operation: str, field: str, value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{operation}: {field} must be a finite number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{operation}: {field} must be a finite number") from exc
    if not number.is_finite():
        raise ValueError(f"{operation}: {field} must be a finite number")
    return number


def whole_quantity(operation: str, value: object) -> int:
    """Accept SDK whole-valued floats without truncating fractional quantities."""
    if isinstance(value, str):
        raise ValueError(f"{operation}: qty must be a positive whole number")
    number = _number(operation, "qty", value)
    if number <= 0 or number != number.to_integral_value():
        raise ValueError(f"{operation}: qty must be a positive whole number")
    return int(number)


def instrument_rule_summary(
    instrument: InstrumentFacts, price: object = None
) -> dict[str, Any]:
    """Describe exactly which representation and tick facts are established.

    OpenD's static lot_size means deliverable shares for an option, rather than
    contracts per order. It never substitutes for the premium multiplier. Its
    snapshot price_spread describes a current upward spread, not a tick schedule.
    """
    classification = instrument.classification
    if classification not in ORDER_CLASSIFICATIONS:
        raise ValueError(
            f"{instrument.code}: instrument classification cannot be verified "
            "as a supported stock, ETF or option"
        )
    market, separator, symbol = instrument.code.partition(".")
    if not separator or not symbol:
        raise ValueError(f"{instrument.code!r}: a market-prefixed code is required")
    market = market.upper()
    decimal_places: int | None = None
    tick: str | None = None
    if market == "US" and classification in EQUITY_CLASSIFICATIONS:
        if price is not None:
            number = _number("instrument rules", "price", price)
            decimal_places = 4 if number < 1 else 2
            tick = "0.0001" if number < 1 else "0.01"
    elif market == "US" and classification == "DRVT":
        decimal_places = 2
    elif market in {"SH", "SZ"} and classification in EQUITY_CLASSIFICATIONS:
        decimal_places = 2
        tick = "0.01"
    else:
        # OpenD documents three decimal places for other securities, but a
        # representation rule does not establish their exchange's valid grid.
        decimal_places = 3
    return {
        "code": instrument.code,
        "classification": classification,
        "quantity_unit": "contracts" if classification == "DRVT" else "shares",
        "whole_quantity_required": True,
        "lot_size": instrument.lot_size,
        "lot_size_meaning": "deliverable_shares_per_contract"
        if classification == "DRVT"
        else "shares_per_lot",
        "price_decimal_places": decimal_places,
        "price_tick": tick,
        "tick_validation": "VERIFIED" if tick is not None else "UNKNOWN",
    }


def validate_instrument_order(
    operation: str,
    *,
    instrument: InstrumentFacts,
    qty: object,
    order_type: str,
    price: object = None,
    aux_price: object = None,
) -> dict[str, Any]:
    """Refuse off-grid or over-precision prices without silently rounding them."""
    requested_type = str(order_type).strip().upper()
    if requested_type not in FIXED_LIMIT_TYPES | NO_FIXED_LIMIT_TYPES:
        raise ValueError(
            f"{operation}: {order_type!r} is not a documented submission order type"
        )
    whole_quantity(operation, qty)
    rules = instrument_rule_summary(instrument, price)
    for field, value in (("price", price), ("aux_price", aux_price)):
        if value is None:
            continue
        number = _number(operation, field, value)
        if number < 0:
            raise ValueError(f"{operation}: {field} must not be negative")
        # A market order's ignored price may be zero. Check nonzero values and
        # triggers while the ordinary order validator checks required limits.
        if number == 0:
            if field == "price" and requested_type in FIXED_LIMIT_TYPES:
                raise ValueError(f"{operation}: limit price must be greater than zero")
            continue
        field_rules = instrument_rule_summary(instrument, number)
        places = field_rules["price_decimal_places"]
        if places is None:
            continue
        step = Decimal(field_rules["price_tick"] or f"1e-{places}")
        decimal_tuple = number.as_tuple()
        exponent = decimal_tuple.exponent
        assert isinstance(exponent, int)  # finite Decimal, established above
        discarded_places = max(0, -places - exponent)
        # The documented grids here are powers of ten. Inspecting the exact
        # Decimal digits avoids both binary float error and unbounded arithmetic
        # contexts for a supplied exponent, without rounding a submitted price.
        off_grid = discarded_places > 0 and any(
            decimal_tuple.digits[-discarded_places:]
        )
        if off_grid:
            description = (
                f"a multiple of the documented {step} price tick"
                if field_rules["price_tick"] is not None
                else f"representable with at most {places} decimal places"
            )
            raise ValueError(f"{operation}: {field} must be {description}")
    return rules


def normalize_order_session(session: object, *, trd_env: str) -> str:
    if not isinstance(session, str):
        raise ValueError("session must be N/A, RTH, ETH, OVERNIGHT or ALL")
    selected = session.strip().upper()
    if selected == "NONE":
        selected = Session.NONE
    if selected not in DOCUMENTED_SESSIONS:
        raise ValueError("session must be N/A, RTH, ETH, OVERNIGHT or ALL")
    if str(trd_env).strip().upper() == "SIMULATE":
        if selected not in {Session.NONE, Session.RTH}:
            raise ValueError("Paper execution supports the RTH session only")
        return Session.RTH
    return selected


def validate_order_session(
    code: str, classification: str | None, order_type: str, session: str
) -> str:
    """Apply a conservative explicit-session scope without broadening defaults."""
    selected = normalize_order_session(session, trd_env="REAL")
    if selected == Session.NONE:
        return selected
    if not code.startswith("US.") or classification not in EQUITY_CLASSIFICATIONS:
        raise ValueError("Explicit sessions apply only to verified US stocks and ETFs")
    if selected != Session.RTH and str(order_type).strip().upper() != "NORMAL":
        raise ValueError(
            "ETH, OVERNIGHT and ALL sessions support NORMAL limit orders only"
        )
    return selected
