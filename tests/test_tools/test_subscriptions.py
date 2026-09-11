"""Subscription tools through actual MCP dispatch (R8)."""

from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import OpenQuoteContext

from moomoo_mcp.services.market_data_service import MarketDataService
from tests.conftest import call_mcp_tool

FULL_REPORT = {
    "total_used": 12,
    "remain": 488,
    "option_used_quota": 10,
    "option_remain_quota": 90,
    "own_used": 3,
    "own_security_firm": "FUTUSECURITIES",
    "own_option_used_quota": 1,
    "sub_list": {"QUOTE": ["US.AAPL", "HK.00700"], "ORDER_BOOK": ["HK.00700"]},
}


class FakeGateway:
    """Two connections sharing one gateway, so isolation is observable."""

    def __init__(self):
        # code -> set of subtypes, per connection.
        self.connections = {"ours": {}, "theirs": {"US.AAPL": {"QUOTE"}}}

    def subscribe(self, codes, subtypes, subscribe_push=False):  # noqa: ARG002
        for code in codes:
            self.connections["ours"].setdefault(code, set()).update(subtypes)
        return (0, None)

    def unsubscribe(self, code_list, subtype_list):
        for code in code_list:
            held = self.connections["ours"].get(code)
            if held:
                held.difference_update(subtype_list)
                if not held:
                    del self.connections["ours"][code]
        return (0, None)

    def query_subscription(self, is_all_conn=True):
        scope = "ours" if not is_all_conn else None
        source = (
            self.connections[scope]
            if scope
            else {**self.connections["ours"], **self.connections["theirs"]}
        )
        sub_list: dict[str, list[str]] = {}
        for code, subtypes in source.items():
            for subtype in subtypes:
                sub_list.setdefault(subtype, []).append(code)
        return (0, {"own_used": len(source), "total_used": 2, "sub_list": sub_list})

    def get_stock_quote(self, codes):
        return (0, pd.DataFrame([{"code": code, "last_price": 1.0} for code in codes]))


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.query_subscription.return_value = (0, dict(FULL_REPORT))
    ctx.unsubscribe.return_value = (0, None)
    ctx.subscribe.return_value = (0, None)
    return ctx


@pytest.fixture
def subs_context(mcp_app_context, quote_ctx):
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)
    return mcp_app_context


class TestInspectionTool:
    @pytest.mark.asyncio
    async def test_separates_connection_scope_from_provider_scope(self, subs_context):
        result = await call_mcp_tool(subs_context, "get_subscriptions")

        payload = result.structured
        assert payload["checked_at"].endswith("Z")
        assert payload["connection"]["subscriptions"]["QUOTE"] == [
            "US.AAPL",
            "HK.00700",
        ]
        assert payload["connection"]["used_quota"] == 3
        assert payload["connection"]["option_used_quota"] == 1
        assert payload["connection"]["security_firm"] == "FUTUSECURITIES"
        assert payload["provider"] == {
            "total_used": 12,
            "remain": 488,
            "option_used_quota": 10,
            "option_remain_quota": 90,
        }

    @pytest.mark.asyncio
    async def test_absent_quota_fields_are_omitted_not_invented(
        self, subs_context, quote_ctx
    ):
        quote_ctx.query_subscription.return_value = (
            0,
            {"sub_list": {"QUOTE": ["US.AAPL"]}},
        )

        result = await call_mcp_tool(subs_context, "get_subscriptions")

        assert result.structured["provider"] == {}
        assert result.structured["connection"] == {
            "subscriptions": {"QUOTE": ["US.AAPL"]}
        }

    @pytest.mark.asyncio
    async def test_provider_error_surfaces(self, subs_context, quote_ctx):
        quote_ctx.query_subscription.return_value = (-1, "quote server unavailable")

        with pytest.raises(Exception, match="quote server unavailable"):
            await call_mcp_tool(subs_context, "get_subscriptions")


class TestReleaseTool:
    @pytest.mark.asyncio
    async def test_only_requested_selections_are_submitted(
        self, subs_context, quote_ctx
    ):
        result = await call_mcp_tool(
            subs_context,
            "unsubscribe_market_data",
            {"codes": ["US.AAPL"], "sub_types": ["QUOTE"]},
        )

        quote_ctx.unsubscribe.assert_called_once_with(
            code_list=["US.AAPL"], subtype_list=["QUOTE"]
        )
        payload = result.structured
        assert payload["codes"] == ["US.AAPL"]
        assert payload["sub_types"] == ["QUOTE"]
        assert payload["scope"] == "this_connection"

    @pytest.mark.asyncio
    async def test_result_claims_acknowledgement_not_completion(self, subs_context):
        result = await call_mcp_tool(
            subs_context,
            "unsubscribe_market_data",
            {"codes": ["US.AAPL"], "sub_types": ["QUOTE"]},
        )

        assert result.structured["status"] == "acknowledged"
        assert "removed" not in result.text.lower()

    @pytest.mark.asyncio
    async def test_provider_refusal_is_reported_without_retrying(
        self, subs_context, quote_ctx
    ):
        quote_ctx.unsubscribe.return_value = (-1, "minimum subscription duration")

        with pytest.raises(Exception, match="minimum subscription duration"):
            await call_mcp_tool(
                subs_context,
                "unsubscribe_market_data",
                {"codes": ["US.AAPL"], "sub_types": ["QUOTE"]},
            )

        assert quote_ctx.unsubscribe.call_count == 1

    @pytest.mark.asyncio
    async def test_unsupported_subtype_fails_before_the_gateway(
        self, subs_context, quote_ctx
    ):
        with pytest.raises(Exception, match="sub_types must be one of"):
            await call_mcp_tool(
                subs_context,
                "unsubscribe_market_data",
                {"codes": ["US.AAPL"], "sub_types": ["HEARTBEAT"]},
            )

        quote_ctx.unsubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_selection_fails_before_the_gateway(
        self, subs_context, quote_ctx
    ):
        with pytest.raises(Exception, match="at least one"):
            await call_mcp_tool(
                subs_context,
                "unsubscribe_market_data",
                {"codes": ["US.AAPL"], "sub_types": []},
            )

        quote_ctx.unsubscribe.assert_not_called()


class TestWorkflowAgainstTwoConnections:
    """Inspect, select, release, inspect — without touching another client."""

    @pytest.fixture
    def gateway_context(self, mcp_app_context):
        gateway = FakeGateway()
        mcp_app_context.market_data_service = MarketDataService(
            quote_ctx=cast(OpenQuoteContext, gateway)
        )
        return mcp_app_context, gateway

    @pytest.mark.asyncio
    async def test_full_inspect_release_inspect_cycle(self, gateway_context):
        context, gateway = gateway_context

        # A quote read creates the subscription on our connection.
        await call_mcp_tool(context, "get_stock_quote", {"codes": ["US.AAPL"]})
        before = await call_mcp_tool(context, "get_subscriptions")
        assert before.structured["connection"]["subscriptions"]["QUOTE"] == ["US.AAPL"]

        await call_mcp_tool(
            context,
            "unsubscribe_market_data",
            {"codes": ["US.AAPL"], "sub_types": ["QUOTE"]},
        )

        after = await call_mcp_tool(context, "get_subscriptions")
        assert after.structured["connection"]["subscriptions"] == {}

        # The other client's identical subscription is untouched.
        assert gateway.connections["theirs"] == {"US.AAPL": {"QUOTE"}}

    @pytest.mark.asyncio
    async def test_reading_again_after_release_resubscribes(self, gateway_context):
        context, gateway = gateway_context

        await call_mcp_tool(context, "get_stock_quote", {"codes": ["US.AAPL"]})
        await call_mcp_tool(
            context,
            "unsubscribe_market_data",
            {"codes": ["US.AAPL"], "sub_types": ["QUOTE"]},
        )
        quotes = await call_mcp_tool(context, "get_stock_quote", {"codes": ["US.AAPL"]})

        assert quotes.structured["result"][0]["code"] == "US.AAPL"
        assert gateway.connections["ours"] == {"US.AAPL": {"QUOTE"}}

    @pytest.mark.asyncio
    async def test_inspection_reports_only_this_connection(self, gateway_context):
        context, _ = gateway_context

        await call_mcp_tool(context, "get_stock_quote", {"codes": ["HK.00700"]})
        result = await call_mcp_tool(context, "get_subscriptions")

        # 'theirs' holds US.AAPL on the same gateway; it must not appear here.
        assert result.structured["connection"]["subscriptions"] == {
            "QUOTE": ["HK.00700"]
        }
