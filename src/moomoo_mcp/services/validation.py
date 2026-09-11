"""Input validation shared by the market-data services.

Validating here means a malformed request fails with a message naming the field
and the accepted values, instead of travelling to the gateway and coming back as
an opaque protocol error — or, worse, being silently normalized into a query the
caller did not ask for.
"""

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
