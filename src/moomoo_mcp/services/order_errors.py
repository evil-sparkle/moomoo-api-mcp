"""The three outcomes an order mutation can report, as exception types.

An agent that gets an error back from `place_order` has exactly one question:
*is there an order out there now?* The old `RuntimeError("place_order failed:
…")` answered it for none of the cases — a policy refusal, a gateway timeout and
an unreadable success response all arrived looking the same — so the safe
reading was always "assume nothing and go look", and the unsafe one, retrying,
was the easy one.

These three types answer it:

- :class:`OrderNotSentError` — nothing was sent. Safe to fix and retry.
- :class:`OrderOutcomeUnknownError` — the call started and nothing came back.
  An order may exist. Check `get_orders` before any retry.
- :class:`OrderReceiptUnreadableError` — the gateway acknowledged it. An order
  exists and its identifier is lost. Never resend.

The boundary between the first and the other two is the moment the SDK write
call starts. Everything before it is "not sent", by construction: the write
methods wrap their whole pre-dispatch phase in :func:`not_sent`, so a check that
raises its own natural exception type still surfaces as a refusal.

No message in this module says a request "reached" the gateway or the broker.
Only an acknowledgement establishes that, and the two post-boundary types are
precisely the cases where there is no acknowledgement to rely on.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from moomoo_mcp.services.trading_policy import TradingPolicyError


class OrderNotSentError(RuntimeError):
    """The mutation was refused before the gateway call started.

    Policy, validation, limits, account resolution, the execution halt, a
    missing connection, a failed just-in-time unlock: every pre-dispatch failure
    arrives here, with the original exception kept as ``__cause__``.
    """


class OrderOutcomeUnknownError(RuntimeError):
    """The call started and no acknowledgement came back.

    A gateway error code lands here alongside a timeout and a raised SDK call.
    They are not distinguishable: matching the gateway's error text to tell a
    broker rejection from a transport failure would be guesswork, and guessing
    wrong in the permissive direction means telling a caller that nothing was
    sent when something was.
    """


class OrderReceiptUnreadableError(RuntimeError):
    """The gateway acknowledged the request, and its response could not be read.

    Distinct from an unknown outcome because it calls for a different action: an
    order exists, so the task is to find it, not to establish whether there is
    one.
    """


def not_sent_message(operation: str, reason: str) -> str:
    return (
        f"{operation} was refused before any request was sent to the gateway, "
        f"so no order was sent. {reason}"
    )


def outcome_unknown_message(operation: str, gateway_message: str) -> str:
    return (
        f"{operation} may have been sent: the request was dispatched to the "
        f"gateway and no acknowledgement came back, so its outcome is unknown. "
        f"The gateway said: {gateway_message}. Check get_orders before retrying; "
        "this request was not resent automatically."
    )


def receipt_unreadable_message(operation: str, reason: str) -> str:
    return (
        f"{operation}: the gateway acknowledged the request, but its receipt "
        f"could not be read, so the order identifier is unknown ({reason}). Do "
        "not resend this request. Find the order with get_orders."
    )


@contextmanager
def not_sent(operation: str) -> Iterator[None]:
    """Convert every pre-dispatch failure into :class:`OrderNotSentError`.

    Wrapping the phase rather than each check is deliberate. Individual checks
    keep raising the type that fits them — ``TradingPolicyError`` for a refusal,
    ``ValueError`` for a malformed value — and the boundary does the translating,
    so a check added later cannot forget to say that nothing was sent.

    The three outcome types pass through untouched: a nested write that already
    classified itself must not be reclassified as a refusal.

    Args:
        operation: Name of the operation, used in the message.

    Raises:
        OrderNotSentError: For any pre-dispatch failure, chained to its cause.
    """
    try:
        yield
    except (
        OrderNotSentError,
        OrderOutcomeUnknownError,
        OrderReceiptUnreadableError,
    ):
        raise
    except (TradingPolicyError, ValueError, TypeError, RuntimeError) as exc:
        raise OrderNotSentError(not_sent_message(operation, str(exc))) from exc
