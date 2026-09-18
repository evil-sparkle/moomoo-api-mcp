"""Market data tools through actual MCP dispatch."""

import pytest


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
    mock_market_data_service.get_market_snapshot.return_value = [
        {"code": "US.AAPL", "last_price": 150.0, "pe_ratio": 25.0},
        {"code": "US.TSLA", "last_price": 250.0},
    ]

    result = await call_tool("get_market_snapshot", {"codes": ["US.AAPL", "US.TSLA"]})

    assert len(result.structured["result"]) == 2
    assert result.structured["result"][0]["code"] == "US.AAPL"
    mock_market_data_service.get_market_snapshot.assert_called_once_with(
        ["US.AAPL", "US.TSLA"]
    )


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
