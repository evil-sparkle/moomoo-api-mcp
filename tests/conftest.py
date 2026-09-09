"""Shared fixtures and an in-process MCP dispatch harness.

Several requirements are about what crosses the MCP boundary, not about what a
Python function returns. Calling the decorated tool function directly skips
FastMCP's argument validation and result conversion, so it cannot prove that a
tool actually serializes an identifier as a string or forwards an argument to
the SDK. ``call_mcp_tool`` dispatches by tool name through the real server and
returns both the text content and the structured content a client would see.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService

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


class McpToolResult:
    """The two views of a tool result that a client receives."""

    def __init__(self, content: Any, structured: Any):
        self.content = content
        self.structured = structured

    @property
    def text(self) -> str:
        """Concatenated text blocks, exactly as serialized for the client."""
        return "".join(
            block.text
            for block in self.content
            if getattr(block, "type", None) == "text"
        )

    @property
    def json(self) -> Any:
        """The text content parsed back as JSON."""
        return json.loads(self.text)


async def call_mcp_tool(
    app_context: AppContext, name: str, arguments: dict[str, Any] | None = None
) -> McpToolResult:
    """Dispatch a tool by name through the real FastMCP server."""
    session = MagicMock()
    session.send_log_message = AsyncMock()
    token = request_ctx.set(
        RequestContext(
            request_id="test-req",
            meta=None,
            session=session,
            lifespan_context=app_context,
        )
    )
    try:
        result = await mcp.call_tool(name, arguments or {})
    finally:
        request_ctx.reset(token)
    # FastMCP returns unstructured content alone for tools without an output
    # schema, and a (content, structured) pair for tools that have one.
    if isinstance(result, tuple):
        return McpToolResult(*result)
    return McpToolResult(result, None)


@pytest.fixture
def call_tool(mcp_app_context):
    """Call an MCP tool by name against the mock-backed lifespan context."""

    async def _call(
        name: str, arguments: dict[str, Any] | None = None
    ) -> McpToolResult:
        return await call_mcp_tool(mcp_app_context, name, arguments)

    return _call
