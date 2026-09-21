"""The three outcomes, and the wording an agent has to act on.

These assert on message text, which normally deserves suspicion. Here the text
is the interface: an agent reading "no order was sent" retries, and one reading
"may have been sent" goes and looks first. Getting a phrase wrong turns a safe
retry into a duplicate order, so the phrases are pinned.
"""

import pytest

from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderOutcomeUnknownError,
    OrderReceiptUnreadableError,
    not_sent,
    not_sent_message,
    outcome_unknown_message,
    receipt_unreadable_message,
)
from moomoo_mcp.services.trading_policy import TradingPolicyError

ALL_MESSAGES = [
    not_sent_message("place_order", "refused"),
    outcome_unknown_message("place_order", "timeout"),
    receipt_unreadable_message("place_order", "ValueError"),
]


class TestMessages:
    def test_not_sent_says_nothing_was_sent(self):
        message = not_sent_message("place_order", "over the cap")

        assert "no order was sent" in message
        assert "over the cap" in message

    def test_outcome_unknown_does_not_claim_a_rejection(self):
        message = outcome_unknown_message("place_order", "connection reset")

        assert "may have been sent" in message
        assert "outcome is unknown" in message
        assert "check get_orders before retrying" in message.lower()
        assert "connection reset" in message

    def test_receipt_unreadable_forbids_a_resend(self):
        message = receipt_unreadable_message("place_order", "KeyError: order_id")

        assert "acknowledged" in message
        assert "do not resend" in message.lower()
        assert "get_orders" in message

    @pytest.mark.parametrize("message", ALL_MESSAGES)
    def test_no_message_claims_the_request_reached_anything(self, message):
        """Only an acknowledgement establishes that, and these are the cases
        where there is none to rely on."""
        assert "reached" not in message


class TestNotSentBoundary:
    """Every pre-dispatch failure becomes a refusal, whatever raised it."""

    @pytest.mark.parametrize(
        "exception",
        [
            TradingPolicyError("over the cap"),
            ValueError("qty must be positive"),
            TypeError("price must be a number"),
            RuntimeError("Trade context not connected"),
        ],
    )
    def test_pre_dispatch_failures_are_converted(self, exception):
        with pytest.raises(OrderNotSentError) as excinfo, not_sent("place_order"):
            raise exception

        assert "no order was sent" in str(excinfo.value)
        assert str(exception) in str(excinfo.value)

    def test_the_original_exception_is_kept_as_the_cause(self):
        """A caller that wants to branch on *why* still can."""
        cause = TradingPolicyError("over the cap")

        with pytest.raises(OrderNotSentError) as excinfo, not_sent("place_order"):
            raise cause

        assert excinfo.value.__cause__ is cause

    @pytest.mark.parametrize(
        "exception",
        [
            OrderOutcomeUnknownError("may have been sent"),
            OrderReceiptUnreadableError("acknowledged"),
        ],
    )
    def test_post_boundary_outcomes_pass_through_unchanged(self, exception):
        """A write that already classified itself must not be re-read as a refusal.

        Reclassifying an unknown outcome as "nothing was sent" would be the
        single most dangerous thing this boundary could do.
        """
        with pytest.raises(type(exception)) as excinfo, not_sent("place_order"):
            raise exception

        assert excinfo.value is exception

    def test_an_already_refused_write_is_not_wrapped_twice(self):
        original = OrderNotSentError("no order was sent")

        with pytest.raises(OrderNotSentError) as excinfo, not_sent("place_order"):
            raise original

        assert excinfo.value is original

    def test_nothing_raised_means_nothing_happens(self):
        with not_sent("place_order"):
            pass
