"""Market data tools through actual MCP dispatch."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.server import mcp
from moomoo_mcp.services.market_data_service import MarketDataService


@pytest.mark.asyncio
async def test_get_stock_quote(call_tool, mock_market_data_service):
    """Test get_stock_quote tool through MCP dispatch."""
    mock_market_data_service.get_stock_quote.return_value = [
        {"code": "US.AAPL", "last_price": 150.0, "volume": 1000000}
    ]

    result = await call_tool("get_stock_quote", {"codes": ["US.AAPL"]})

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["code"] == "US.AAPL"
    assert result.structured["result"][0]["last_price"] == 150.0
    mock_market_data_service.get_stock_quote.assert_called_once_with(["US.AAPL"])


@pytest.mark.asyncio
async def test_get_historical_klines(call_tool, mock_market_data_service):
    """Test get_historical_klines tool through MCP dispatch."""
    mock_market_data_service.get_historical_klines.return_value = [
        {"time_key": "2025-01-01", "open": 150.0, "close": 151.0}
    ]

    result = await call_tool(
        "get_historical_klines",
        {
            "code": "US.TSLA",
            "ktype": "K_1M",
            "start": "2025-01-01",
            "end": "2025-01-15",
            "max_count": 50,
            "autype": "HFQ",
        },
    )

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["open"] == 150.0
    mock_market_data_service.get_historical_klines.assert_called_once_with(
        code="US.TSLA",
        ktype="K_1M",
        start="2025-01-01",
        end="2025-01-15",
        max_count=50,
        autype="HFQ",
    )


@pytest.mark.asyncio
async def test_get_market_snapshot(call_tool, mock_market_data_service):
    """Test get_market_snapshot tool through MCP dispatch."""
    rows = [
        {
            "code": "US.AAPL",
            "last_price": 150.0,
            "pe_ratio": 25.0,
            "pe_ttm_ratio": 22.5,
        },
        {"code": "US.TSLA", "last_price": 250.0, "pe_ratio": 35.0},
    ]
    mock_market_data_service.get_market_snapshot.return_value = rows

    result = await call_tool("get_market_snapshot", {"codes": ["US.AAPL", "US.TSLA"]})

    assert result.structured["result"] == rows
    assert result.json_blocks == rows
    mock_market_data_service.get_market_snapshot.assert_called_once_with(
        ["US.AAPL", "US.TSLA"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", ["get_historical_klines", "get_historical_klines_page"]
)
async def test_historical_pe_passes_through_mcp(call_tool, mcp_app_context, tool_name):
    """Preserve broker P/E through the real service and MCP result conversion."""
    rows = [{"time_key": "2024-02-02 00:00:00", "close": 185.85, "pe_ratio": 30.318}]
    quote_ctx = MagicMock()
    quote_ctx.request_history_kline.return_value = (0, pd.DataFrame(rows), None)
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)

    result = await call_tool(
        tool_name,
        {
            "code": "US.AAPL",
            "start": "2024-02-02",
            "end": "2024-02-02",
            "autype": "NONE",
        },
    )

    if tool_name == "get_historical_klines_page":
        expected = {"data": rows, "next_cursor": None, "has_more": False}
        assert result.structured == expected
        assert result.json == expected
    else:
        assert result.structured["result"] == rows
        assert result.json_blocks == rows


@pytest.mark.asyncio
async def test_snapshot_description_distinguishes_provider_pe_fields():
    """Check the guidance exposed to AI clients by tools/list."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools["get_market_snapshot"].description or ""

    assert "pe_ratio" in description
    assert (
        "pe_ttm_ratio: Provider's trailing-twelve-month (TTM) P/E ratio" in description
    )
    assert "separate from pe_ttm_ratio" in description
    assert "Missing TTM" in description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", ["get_historical_klines", "get_historical_klines_page"]
)
async def test_historical_description_explains_pe_basis(tool_name):
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools[tool_name].description or ""

    assert "pe_ratio" in description
    assert "passed through unchanged" in description
    assert "does not reconstruct point-in-time TTM P/E" in description
    assert "does not guarantee the earnings basis" in description
    assert "Missing TTM data is not synthesized" in description
    assert "a current snapshot cannot supply" in description


@pytest.mark.asyncio
async def test_get_order_book(call_tool, mock_market_data_service):
    """Test get_order_book tool through MCP dispatch."""
    mock_market_data_service.get_order_book.return_value = {
        "code": "HK.00700",
        "Bid": [(400.0, 1000, 5, {})],
        "Ask": [(400.2, 800, 4, {})],
    }

    result = await call_tool("get_order_book", {"code": "HK.00700", "num": 5})

    assert result.json["code"] == "HK.00700"
    assert len(result.json["Bid"]) == 1
    mock_market_data_service.get_order_book.assert_called_once_with("HK.00700", num=5)


@pytest.mark.asyncio
async def test_get_user_security_group(call_tool, mock_market_data_service):
    """Test get_user_security_group tool through MCP dispatch."""
    mock_market_data_service.get_user_security_group.return_value = [
        {"group_name": "Watchlist 1", "group_id": "1"}
    ]

    result = await call_tool("get_user_security_group", {"group_type": 1})

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["group_name"] == "Watchlist 1"
    mock_market_data_service.get_user_security_group.assert_called_once_with(
        group_type=1
    )


@pytest.mark.asyncio
async def test_get_user_security(call_tool, mock_market_data_service):
    """Test get_user_security tool through MCP dispatch."""
    mock_market_data_service.get_user_security.return_value = [
        {"code": "US.AAPL", "name": "Apple Inc."}
    ]

    result = await call_tool("get_user_security", {"group_name": "Watchlist 1"})

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["code"] == "US.AAPL"
    mock_market_data_service.get_user_security.assert_called_once_with("Watchlist 1")
