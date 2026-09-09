"""Opaque continuation cursors for paginated historical candles.

The SDK's continuation token is raw protobuf bytes. Those cannot cross a JSON
boundary, so the cursor is an envelope: base64url of a JSON document holding the
base64-encoded token alongside the query it belongs to.

Binding the token to its query is the point. A token means "the next page of
*that* request"; replaying it against a different symbol, interval, or
adjustment would return candles the caller did not ask for, silently mixed into
what looks like one continuous series. Rejecting the mismatch is the only way a
client finds out.

The cursor carries no credentials, account identifiers, or authorization: it
addresses data the caller could already request directly.
"""

import base64
import binascii
import json
from datetime import date, timedelta
from typing import Any

CURSOR_VERSION = 1

# Large enough for a protobuf continuation token plus the bound query, small
# enough that a malformed or hostile value is rejected before being parsed.
MAX_CURSOR_CHARS = 8192

# Filters that define which series a cursor belongs to. A change to any of them
# is a different query, not a continuation of this one.
BOUND_FILTERS = ("code", "ktype", "start", "end", "max_count", "autype")

# Matches the SDK's own default window when a bound is omitted.
DEFAULT_RANGE_DAYS = 365


class CursorError(ValueError):
    """A continuation cursor is malformed, unsupported, or from another query."""


def resolve_date_range(
    start: str | None, end: str | None, today: date | None = None
) -> tuple[str, str]:
    """Resolve an omitted date bound the way the SDK would, but only once.

    The resolved range is stored in the cursor and reused for every later page.
    Letting each page re-resolve "today" would move the window underneath a
    client that happened to paginate across midnight, quietly changing which
    history it was reading.

    Args:
        start: Requested start date (YYYY-MM-DD), or None.
        end: Requested end date (YYYY-MM-DD), or None.
        today: Current date, injectable for tests.

    Returns:
        The concrete ``(start, end)`` pair to send to the SDK.
    """
    span = timedelta(days=DEFAULT_RANGE_DAYS)
    current = today or date.today()

    if start and end:
        return start, end
    if end and not start:
        return (date.fromisoformat(end) - span).isoformat(), end
    if start and not end:
        return start, (date.fromisoformat(start) + span).isoformat()
    return (current - span).isoformat(), current.isoformat()


def encode_cursor(
    token: bytes, query: dict[str, Any], resolved: tuple[str, str]
) -> str:
    """Build an opaque cursor for ``token`` bound to ``query``.

    Args:
        token: The SDK's continuation token, encoded losslessly as base64 so a
            byte string survives JSON.
        query: The filters exactly as the caller supplied them, including any
            omitted date left as None.
        resolved: The concrete date range this query resolved to.

    Returns:
        A base64url string safe to hand back to a client.
    """
    envelope = {
        "v": CURSOR_VERSION,
        "q": {name: query.get(name) for name in BOUND_FILTERS},
        "r": {"start": resolved[0], "end": resolved[1]},
        "k": base64.b64encode(token).decode("ascii"),
    }
    payload = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, query: dict[str, Any]) -> tuple[bytes, tuple[str, str]]:
    """Validate ``cursor`` against ``query`` and return its token and range.

    Args:
        cursor: The opaque cursor from a previous page.
        query: The filters supplied with this call.

    Returns:
        The SDK continuation token and the resolved ``(start, end)`` range that
        the original query established.

    Raises:
        CursorError: If the cursor is not a valid envelope, uses an unknown
            version, or belongs to a different query. Nothing is sent to the
            gateway in any of those cases.
    """
    if not isinstance(cursor, str) or not cursor.strip():
        raise CursorError("cursor must be a non-empty string from a previous page.")
    if len(cursor) > MAX_CURSOR_CHARS:
        raise CursorError(
            f"cursor is {len(cursor)} characters, above the "
            f"{MAX_CURSOR_CHARS}-character limit; it was not produced by this "
            "server."
        )

    try:
        envelope = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise CursorError(
            "cursor is malformed. Pass back the next_cursor value from the "
            "previous page unchanged, or omit it to start a new query."
        ) from exc

    if not isinstance(envelope, dict):
        raise CursorError("cursor does not contain a continuation envelope.")

    version = envelope.get("v")
    if version != CURSOR_VERSION:
        raise CursorError(
            f"cursor has version {version!r}, but this server issues version "
            f"{CURSOR_VERSION}. Start the query again without a cursor."
        )

    bound = envelope.get("q")
    resolved = envelope.get("r")
    token = envelope.get("k")
    if not isinstance(bound, dict) or not isinstance(resolved, dict):
        raise CursorError("cursor is missing its bound query.")
    if not isinstance(token, str):
        raise CursorError("cursor is missing its continuation token.")

    mismatched = [
        name
        for name in BOUND_FILTERS
        if bound.get(name) != query.get(name)
    ]
    if mismatched:
        details = ", ".join(
            f"{name}={query.get(name)!r} (cursor: {bound.get(name)!r})"
            for name in mismatched
        )
        raise CursorError(
            "cursor belongs to a different query and cannot continue this one: "
            f"{details}. Changing a filter starts a new query, so omit the "
            "cursor."
        )

    start = resolved.get("start")
    end = resolved.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise CursorError("cursor is missing its resolved date range.")

    try:
        decoded_token = base64.b64decode(token.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeError) as exc:
        raise CursorError("cursor's continuation token is not decodable.") from exc

    return decoded_token, (start, end)
