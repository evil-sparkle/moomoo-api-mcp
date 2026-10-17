"""Synthetic MCP dispatch and HTTP transports shared by tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from moomoo import OpenQuoteContext
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.market_data_service import MarketDataService
from scripts import private_chatgpt_preflight as preflight

TOKEN = "test-quote-permissions-token"
AUTHORIZATION = "Bearer fixture-mcp-credential"
ACCOUNT_ID = "9007199254740993"


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
        """The single text block parsed back as JSON."""
        return json.loads(self.text)

    @property
    def json_blocks(self) -> list[Any]:
        """Each text block parsed separately.

        FastMCP emits one text block per element for a list-returning tool, so a
        list result is only valid JSON block by block.
        """
        return [
            json.loads(block.text)
            for block in self.content
            if getattr(block, "type", None) == "text"
        ]


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
def gateway_client(monkeypatch, mcp_app_context):
    quote_ctx = MagicMock(spec=OpenQuoteContext)
    quote_ctx.subscribe.return_value = (0, None)
    mcp_app_context.market_data_service = MarketDataService(quote_ctx)
    monkeypatch.setattr(server, "get_services", lambda: mcp_app_context)
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    with TestClient(server.create_streamable_http_app(TOKEN)) as client:
        yield client, quote_ctx, mcp_app_context
    server.mcp._session_manager = None


def rpc(client, method, params):
    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    return body["result"]


def call(client, name, arguments):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments})


class McpFixture:
    """A strict authenticated MCP endpoint backed only by fixture data."""

    def __init__(
        self,
        *,
        trading_mode: str | None = "READ_ONLY",
        gateway_status: str = "connected",
        authorization: str = AUTHORIZATION,
        port: int = 0,
    ) -> None:
        self.trading_mode = trading_mode
        self.gateway_status = gateway_status
        self.authorization = authorization
        self.calls: list[dict[str, Any]] = []
        self.override: Any = None
        self.fail_account = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    request = json.loads(self.rfile.read(length))
                except ValueError:
                    request = {}
                fixture.calls.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "host": self.headers.get("Host"),
                        "mcp_session_id": self.headers.get("Mcp-Session-Id"),
                        "request": request,
                    }
                )
                if self.headers.get("Authorization") != fixture.authorization:
                    self._answer(401, {"detail": "Unauthorized"})
                    return
                if fixture.override is not None:
                    status, body, content_type = fixture.override(request)
                    self._answer(status, body, content_type)
                    return
                request_id = request.get("id")
                method = request.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": preflight.PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {"name": "check_health"},
                            {"name": "get_accounts"},
                            {"name": "get_positions"},
                        ]
                    }
                elif method == "tools/call":
                    params = request.get("params", {})
                    name = params.get("name")
                    if name == "check_health":
                        health = {"status": fixture.gateway_status}
                        if fixture.trading_mode is not None:
                            health["trading_mode"] = fixture.trading_mode
                        result = {
                            "content": [],
                            "structuredContent": health,
                            "isError": False,
                        }
                    elif name == "get_accounts":
                        if fixture.fail_account:
                            result = {"content": [], "isError": True}
                            self._answer(
                                200,
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "result": result,
                                },
                            )
                            return
                        result = {
                            "content": [],
                            "structuredContent": {
                                "result": [
                                    {
                                        "acc_id": ACCOUNT_ID,
                                        "trd_env": params["arguments"]["trd_env"],
                                    }
                                ]
                            },
                            "isError": False,
                        }
                    elif name == "get_positions":
                        result = {
                            "content": [],
                            "structuredContent": {"result": []},
                            "isError": False,
                        }
                    else:
                        result = {"content": [], "isError": True}
                else:
                    self._answer(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "unknown"},
                        },
                    )
                    return
                self._answer(
                    200,
                    {"jsonrpc": "2.0", "id": request_id, "result": result},
                )

            def _answer(
                self,
                status: int,
                body: Any,
                content_type: str = "application/json",
            ) -> None:
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:
                _ = (format, args)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> McpFixture:
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
