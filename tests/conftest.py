"""Shared service fixtures for MCP dispatch and HTTP transport tests."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from moomoo_mcp.server import AppContext
from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from tests.mcp_helpers import McpToolResult, call_mcp_tool
from tests.mcp_helpers import gateway_client as gateway_client
from tests.trade_helpers import trade_service_factory as trade_service_factory

# Importing the tool modules registers every tool on the shared ``mcp`` server.
import moomoo_mcp.tools.account  # noqa: F401,E402  isort:skip
import moomoo_mcp.tools.market_data  # noqa: F401,E402  isort:skip
import moomoo_mcp.tools.system  # noqa: F401,E402  isort:skip
import moomoo_mcp.tools.trading  # noqa: F401,E402  isort:skip


@pytest.fixture
def mock_moomoo_service() -> MagicMock:
    """Mock quote/health service."""
    return MagicMock(spec=MoomooService)


@pytest.fixture
def mock_trade_service() -> MagicMock:
    """Mock trade service."""
    return MagicMock(spec=TradeService)


@pytest.fixture
def mock_market_data_service() -> MagicMock:
    """Mock market data service."""
    return MagicMock(spec=MarketDataService)


@pytest.fixture
def mcp_app_context(
    mock_moomoo_service, mock_trade_service, mock_market_data_service
) -> AppContext:
    """Lifespan context wired to mock services."""
    return AppContext(
        moomoo_service=mock_moomoo_service,
        trade_service=mock_trade_service,
        market_data_service=mock_market_data_service,
    )


@pytest.fixture
def call_tool(mcp_app_context):
    """Call an MCP tool by name against the mock-backed lifespan context."""

    async def _call(
        name: str, arguments: dict[str, Any] | None = None
    ) -> McpToolResult:
        return await call_mcp_tool(mcp_app_context, name, arguments)

    return _call
