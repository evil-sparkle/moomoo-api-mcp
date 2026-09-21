"""Input validation shared by the market-data services.

Validating here means a malformed request fails with a message naming the field
and the accepted values, instead of travelling to the gateway and coming back as
an opaque protocol error — or, worse, being silently normalized into a query the
caller did not ask for.
"""

import math
from datetime import date, datetime

DATE_FORMAT = "%Y-%m-%d"


def parse_date(field: str, value: str) -> date:
    """Parse a 'YYYY-MM-DD' date, or explain why it is not one.

    Args:
        field: Parameter name, used in the error message.
        value: The supplied value.

    Returns:
        The parsed date.

    Raises:
        ValueError: If the value is not a string in 'YYYY-MM-DD' form.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a 'YYYY-MM-DD' string, got {type(value).__name__}."
        )
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a date in 'YYYY-MM-DD' format, got {value!r}."
        ) from exc


def validate_date_range(
    start: str | None,
    end: str | None,
    max_span_days: int | None = None,
    span_label: str = "",
) -> tuple[date | None, date | None]:
    """Validate an optional start/end pair and its span.

    Args:
        start: Start date string, or None.
        end: End date string, or None.
        max_span_days: Largest permitted ``end - start`` in days, when the
            provider caps the range. None means unbounded.
        span_label: Human phrasing of the cap for the error message.

    Returns:
        The parsed ``(start, end)`` pair, with None preserved.

    Raises:
        ValueError: If a date is malformed, start follows end, or the span
            exceeds the provider's limit. Exceeding the limit is an error and
            not a silent truncation: a caller that asked for six months of
            expirations must not be handed one month and believe it is all of
            them.
    """
    parsed_start = parse_date("start", start) if start is not None else None
    parsed_end = parse_date("end", end) if end is not None else None

    if parsed_start is not None and parsed_end is not None:
        if parsed_start > parsed_end:
            raise ValueError(
                f"start ({parsed_start.isoformat()}) is after end "
                f"({parsed_end.isoformat()}); the range is empty."
            )
        if max_span_days is not None:
            span = (parsed_end - parsed_start).days
            if span > max_span_days:
                raise ValueError(
                    f"The requested range spans {span + 1} days, but the "
                    f"provider accepts at most {span_label or max_span_days + 1}. "
                    "Narrow the range and request the remainder separately."
                )

    return parsed_start, parsed_end


def validate_choice(field: str, value: str, allowed: tuple[str, ...]) -> str:
    """Normalize ``value`` to one of ``allowed``, or reject it.

    An unsupported value is refused rather than replaced with a default: a
    caller who asked for calls should never silently receive puts as well.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be one of {list(allowed)}, got {type(value).__name__}."
        )
    candidate = value.strip().upper()
    if candidate not in allowed:
        raise ValueError(f"{field} must be one of {list(allowed)}, got {value!r}.")
    return candidate


# Order-type classes. "Fixed-limit" means the order carries a price that bounds
# the fill: the gateway will not fill it outside that price. Every other
# submission type leaves the fill price open, so valuing it needs the market.
#
# TRAILING_STOP_LIMIT sits in the second set deliberately. It does have a limit
# price, but that price follows the trail rather than being fixed at submission,
# so a zero price is meaningful and the order cannot be valued from it.
#
# The SDK also defines TWAP, TWAP_LIMIT, VWAP and VWAP_LIMIT. Moomoo documents
# those algorithmic variants as query-only, so they are not submission types and
# are deliberately in neither set: an order type in neither set is refused while
# a notional cap is configured, which is what makes a future SDK addition
# somebody's decision rather than a silent pass.
FIXED_LIMIT_TYPES = frozenset(
    {
        "NORMAL",
        "ABSOLUTE_LIMIT",
        "SPECIAL_LIMIT",
        "SPECIAL_LIMIT_ALL",
        "AUCTION_LIMIT",
        "STOP_LIMIT",
        "LIMIT_IF_TOUCHED",
    }
)

NO_FIXED_LIMIT_TYPES = frozenset(
    {
        "MARKET",
        "AUCTION",
        "STOP",
        "MARKET_IF_TOUCHED",
        "TRAILING_STOP",
        "TRAILING_STOP_LIMIT",
    }
)

# Order types whose trigger price the gateway requires.
STOP_ORDER_TYPES = frozenset(
    {"STOP", "STOP_LIMIT", "MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"}
)

# Order types whose trail parameters the gateway requires.
TRAILING_ORDER_TYPES = frozenset({"TRAILING_STOP", "TRAILING_STOP_LIMIT"})


def _finite_non_negative(operation: str, field: str, value: object) -> float:
    """Require ``value`` to be a finite number that is not negative.

    A limit of ``nan`` silently disables every comparison it takes part in —
    ``nan > cap`` is false — so an order value that is not a real number has to
    be refused here rather than compared later.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{operation}: {field} must be a number, got {type(value).__name__}."
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(
            f"{operation}: {field} must be a finite number, got {value!r}."
        )
    if number < 0:
        raise ValueError(f"{operation}: {field} must not be negative, got {value!r}.")
    return number


def validate_order_values(
    operation: str,
    *,
    order_type: str,
    qty: object = None,
    price: object = None,
    aux_price: object = None,
    trail_value: object = None,
    trail_spread: object = None,
    combo_price: object = None,
) -> None:
    """Check an order's numeric values before anything reaches the gateway.

    This is the numeric half of the pre-dispatch sequence. It knows nothing
    about instruments or limits: it establishes only that the numbers are the
    kind of numbers an order can be built from, so that everything downstream —
    the notional assessment in particular — is arithmetic over real values.

    Args:
        operation: Name of the operation, used in every error message.
        order_type: The order type, which decides whether a price is required.
        qty: Quantity. Checked when supplied.
        price: Single-leg price. Checked when supplied.
        aux_price: Trigger price. Checked when supplied.
        trail_value: Trailing value. Checked when supplied.
        trail_spread: Trailing spread. Checked when supplied.
        combo_price: Net package price. Checked when supplied, and its sign is
            left alone: moomoo documents no debit/credit convention, so a
            negative net price is a legitimate package, not an error.

    Raises:
        ValueError: If any supplied value is not usable as an order value, or a
            required field for this order type is missing.
    """
    requested_type = str(order_type).strip().upper()

    if qty is not None:
        # bool is an int subclass, and True would otherwise pass as a quantity
        # of one. An order quantity that came from a boolean is a caller bug,
        # not a one-share order.
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise ValueError(
                f"{operation}: qty must be a positive integer, got "
                f"{type(qty).__name__}."
            )
        if qty <= 0:
            raise ValueError(f"{operation}: qty must be greater than 0, got {qty!r}.")

    checked_price: float | None = None
    for field, value in (
        ("price", price),
        ("aux_price", aux_price),
        ("trail_value", trail_value),
        ("trail_spread", trail_spread),
    ):
        if value is not None:
            number = _finite_non_negative(operation, field, value)
            if field == "price":
                checked_price = number

    if combo_price is not None:
        if isinstance(combo_price, bool) or not isinstance(combo_price, (int, float)):
            raise ValueError(
                f"{operation}: price must be a number, got "
                f"{type(combo_price).__name__}."
            )
        if not math.isfinite(float(combo_price)):
            raise ValueError(
                f"{operation}: price must be a finite number, got {combo_price!r}."
            )

    needs_positive_price = (
        checked_price is not None
        and requested_type in FIXED_LIMIT_TYPES
        and checked_price <= 0
    )
    if needs_positive_price:
        raise ValueError(
            f"{operation}: {requested_type} is a limit order type and needs a "
            f"price greater than 0, got {price!r}."
        )


def validate_required_order_fields(
    operation: str,
    *,
    order_type: str,
    aux_price: object = None,
    trail_type: object = None,
    trail_value: object = None,
) -> None:
    """Require the fields the gateway demands for this order type.

    Absorbed from ``place_order``, where the same two checks lived inline.

    Raises:
        ValueError: If a field this order type requires is missing.
    """
    requested_type = str(order_type).strip().upper()
    if requested_type in STOP_ORDER_TYPES and aux_price is None:
        raise ValueError(
            f"{operation}: aux_price is required for stop/if-touched order types"
        )
    if requested_type in TRAILING_ORDER_TYPES and (
        trail_type is None or trail_value is None
    ):
        raise ValueError(
            f"{operation}: trail_type and trail_value are required for trailing "
            "stop order types"
        )
