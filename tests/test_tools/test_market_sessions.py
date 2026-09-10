"""Market state and calendar tools through actual MCP dispatch (R6)."""

import os
import time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService
from tests.conftest import call_mcp_tool

TRADING_DAYS = [
    {"time": "2026-01-02", "trade_date_type": "WHOLE"},
    {"time": "2026-01-05", "trade_date_type": "MORNING"},
]


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.get_market_state.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": "US.AAPL",
                    "stock_name": "Apple",
                    "market_state": "PRE_MARKET_BEGIN",
                },
                {"code": "HK.00700", "stock_name": "Tencent", "market_state": "REST"},
            ]
        ),
    )
    ctx.request_trading_days.return_value = (0, list(TRADING_DAYS))
    return ctx


@pytest.fixture
def sessions_context(mcp_app_context, quote_ctx):
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)
    return mcp_app_context


class TestMarketStateTool:
    @pytest.mark.asyncio
    async def test_state_carries_a_utc_observation_time(self, sessions_context):
        result = await call_mcp_tool(
            sessions_context, "get_market_state", {"codes": ["US.AAPL", "HK.00700"]}
        )

        payload = result.structured
        assert payload["checked_at"].endswith("Z")
        assert [row["market_state"] for row in payload["data"]] == [
            "PRE_MARKET_BEGIN",
            "REST",
        ]

    @pytest.mark.asyncio
    async def test_state_comes_from_the_provider_not_the_server_clock(
        self, sessions_context, quote_ctx
    ):
        result = await call_mcp_tool(
            sessions_context, "get_market_state", {"codes": ["US.AAPL"]}
        )

        quote_ctx.get_market_state.assert_called_once()
        assert result.structured["data"][0]["market_state"] == "PRE_MARKET_BEGIN"

    @pytest.mark.asyncio
    async def test_provider_error_surfaces(self, sessions_context, quote_ctx):
        quote_ctx.get_market_state.return_value = (-1, "unknown code")

        with pytest.raises(Exception, match="unknown code"):
            await call_mcp_tool(
                sessions_context, "get_market_state", {"codes": ["US.NOPE"]}
            )

    @pytest.mark.asyncio
    async def test_empty_codes_fail_before_the_gateway(
        self, sessions_context, quote_ctx
    ):
        with pytest.raises(Exception, match="at least one security code"):
            await call_mcp_tool(sessions_context, "get_market_state", {"codes": []})

        quote_ctx.get_market_state.assert_not_called()


class TestTradingDaysTool:
    @pytest.mark.asyncio
    async def test_calendar_dates_are_labelled_market_local(self, sessions_context):
        result = await call_mcp_tool(
            sessions_context,
            "get_trading_days",
            {"market": "US", "start": "2026-01-01", "end": "2026-01-07"},
        )

        payload = result.structured
        assert payload["market"] == "US"
        assert payload["date_basis"] == "market_local"
        assert payload["start"] == "2026-01-01"
        assert payload["end"] == "2026-01-07"
        assert payload["data"] == TRADING_DAYS

    @pytest.mark.asyncio
    async def test_dates_do_not_shift_with_the_server_timezone(
        self, sessions_context
    ):
        """The provider's market-local dates must survive a server in Auckland."""
        result_utc = await call_mcp_tool(
            sessions_context, "get_trading_days", {"market": "US"}
        )

        with patch.dict(os.environ, {"TZ": "Pacific/Auckland"}):
            if hasattr(time, "tzset"):
                time.tzset()
            try:
                result_nz = await call_mcp_tool(
                    sessions_context, "get_trading_days", {"market": "US"}
                )
            finally:
                if hasattr(time, "tzset"):
                    time.tzset()

        assert result_utc.structured["data"] == result_nz.structured["data"]
        assert result_nz.structured["data"][0]["time"] == "2026-01-02"

    @pytest.mark.asyncio
    async def test_partial_session_is_preserved_without_assuming_a_full_day(
        self, sessions_context
    ):
        result = await call_mcp_tool(
            sessions_context, "get_trading_days", {"market": "HK"}
        )

        half_day = result.structured["data"][1]
        assert half_day["trade_date_type"] == "MORNING"
        assert "open_time" not in half_day
        assert "close_time" not in half_day

    @pytest.mark.asyncio
    async def test_unknown_market_fails_before_the_gateway(
        self, sessions_context, quote_ctx
    ):
        with pytest.raises(Exception, match="market must be one of"):
            await call_mcp_tool(
                sessions_context, "get_trading_days", {"market": "ATLANTIS"}
            )

        quote_ctx.request_trading_days.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_error_surfaces_without_a_guessed_calendar(
        self, sessions_context, quote_ctx
    ):
        quote_ctx.request_trading_days.return_value = (-1, "calendar unavailable")

        with pytest.raises(Exception, match="calendar unavailable"):
            await call_mcp_tool(sessions_context, "get_trading_days", {"market": "US"})

    @pytest.mark.asyncio
    async def test_omitted_dates_are_echoed_as_null(self, sessions_context):
        result = await call_mcp_tool(
            sessions_context, "get_trading_days", {"market": "US"}
        )

        assert result.structured["start"] is None
        assert result.structured["end"] is None
