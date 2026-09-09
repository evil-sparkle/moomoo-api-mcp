"""Observation timestamps.

Every response that reports something observed — a health probe, a market
state, an account-impact preview — carries the moment it was observed, in UTC.
Market data is stamped in market-local dates and the server can run anywhere, so
mixing the two silently is how a caller ends up reasoning about the wrong day.
"""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
