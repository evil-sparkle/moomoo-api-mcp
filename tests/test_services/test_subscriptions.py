"""Subscription inspection and explicit release (R8)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService

# Shape returned by query_subscription: connection-scoped sub_list, plus quota
# fields that mix per-connection ('own_*') and provider-wide counters.
SUBSCRIPTION_REPORT = {
    "total_used": 12,
    "remain": 488,
    "option_used_quota": 10,
    "option_remain_quota": 90,
    "own_used": 3,
    "own_security_firm": "FUTUSECURITIES",
    "own_option_used_quota": 1,
    "sub_list": {
        "QUOTE": ["US.AAPL", "HK.00700"],
        "ORDER_BOOK": ["HK.00700"],
    },
}


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.query_subscription.return_value = (0, dict(SUBSCRIPTION_REPORT))
    ctx.unsubscribe.return_value = (0, None)
    ctx.subscribe.return_value = (0, None)
    return ctx


@pytest.fixture
def service(quote_ctx):
    return MarketDataService(quote_ctx=quote_ctx)


class TestInspection:
    """Inspection is scoped to this connection."""

    def test_query_is_scoped_to_this_connection(self, service, quote_ctx):
        service.get_subscriptions()

        quote_ctx.query_subscription.assert_called_once_with(is_all_conn=False)

    def test_returns_the_provider_report(self, service):
        report = service.get_subscriptions()

        assert report["sub_list"]["QUOTE"] == ["US.AAPL", "HK.00700"]
        assert report["own_used"] == 3
        assert report["remain"] == 488

    def test_non_dict_payload_becomes_an_empty_report(self, service, quote_ctx):
        quote_ctx.query_subscription.return_value = (0, None)

        assert service.get_subscriptions() == {}

    def test_provider_error_raises(self, service, quote_ctx):
        quote_ctx.query_subscription.return_value = (-1, "not connected")

        with pytest.raises(RuntimeError, match="not connected"):
            service.get_subscriptions()

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).get_subscriptions()


class TestRelease:
    """Only the named selections, only on this connection."""

    def test_only_requested_selections_are_sent(self, service, quote_ctx):
        service.unsubscribe(codes=["US.AAPL"], sub_types=["QUOTE"])

        quote_ctx.unsubscribe.assert_called_once_with(
            code_list=["US.AAPL"], subtype_list=["QUOTE"]
        )

    def test_global_unsubscribe_is_never_used(self, service, quote_ctx):
        service.unsubscribe(codes=["US.AAPL"], sub_types=["QUOTE"])

        assert "unsubscribe_all" not in quote_ctx.unsubscribe.call_args.kwargs
        quote_ctx.unsubscribe_all.assert_not_called()

    def test_subscription_types_are_normalized(self, service, quote_ctx):
        service.unsubscribe(codes=["US.AAPL"], sub_types=["quote", "order_book"])

        assert quote_ctx.unsubscribe.call_args.kwargs["subtype_list"] == [
            "QUOTE",
            "ORDER_BOOK",
        ]

    def test_codes_are_trimmed(self, service, quote_ctx):
        service.unsubscribe(codes=[" US.AAPL "], sub_types=["QUOTE"])

        assert quote_ctx.unsubscribe.call_args.kwargs["code_list"] == ["US.AAPL"]

    def test_empty_codes_are_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="at least one security code"):
            service.unsubscribe(codes=[], sub_types=["QUOTE"])

        quote_ctx.unsubscribe.assert_not_called()

    def test_blank_code_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="empty security codes"):
            service.unsubscribe(codes=["US.AAPL", " "], sub_types=["QUOTE"])

        quote_ctx.unsubscribe.assert_not_called()

    def test_empty_sub_types_are_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="at least one subscription type"):
            service.unsubscribe(codes=["US.AAPL"], sub_types=[])

        quote_ctx.unsubscribe.assert_not_called()

    def test_unsupported_sub_type_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="sub_types must be one of"):
            service.unsubscribe(codes=["US.AAPL"], sub_types=["HEARTBEAT"])

        quote_ctx.unsubscribe.assert_not_called()

    def test_provider_refusal_raises_and_is_not_retried(self, service, quote_ctx):
        quote_ctx.unsubscribe.return_value = (
            -1,
            "the subscription must be held for at least 60 seconds",
        )

        with pytest.raises(RuntimeError, match="at least 60 seconds"):
            service.unsubscribe(codes=["US.AAPL"], sub_types=["QUOTE"])

        assert quote_ctx.unsubscribe.call_count == 1

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).unsubscribe(
                codes=["US.AAPL"], sub_types=["QUOTE"]
            )


class TestAutomaticSubscriptionsPreserved:
    """Reads keep subscribing for themselves."""

    def test_quotes_still_auto_subscribe(self, service, quote_ctx):
        quote_ctx.get_stock_quote.return_value = (
            0,
            pd.DataFrame([{"code": "US.AAPL", "last_price": 1.0}]),
        )

        service.get_stock_quote(["US.AAPL"])

        quote_ctx.subscribe.assert_called_once_with(
            ["US.AAPL"], ["QUOTE"], subscribe_push=False
        )

    def test_reading_after_release_resubscribes(self, service, quote_ctx):
        quote_ctx.get_stock_quote.return_value = (
            0,
            pd.DataFrame([{"code": "US.AAPL", "last_price": 1.0}]),
        )

        service.unsubscribe(codes=["US.AAPL"], sub_types=["QUOTE"])
        service.get_stock_quote(["US.AAPL"])

        quote_ctx.unsubscribe.assert_called_once()
        quote_ctx.subscribe.assert_called_once()

    def test_repeated_reads_create_no_service_side_tracking(self, service, quote_ctx):
        quote_ctx.get_stock_quote.return_value = (
            0,
            pd.DataFrame([{"code": "US.AAPL", "last_price": 1.0}]),
        )

        for _ in range(3):
            service.get_stock_quote(["US.AAPL"])

        # Subscription state lives in the SDK, not in a duplicate registry here.
        assert not hasattr(service, "_subscriptions")
        assert quote_ctx.subscribe.call_count == 3
