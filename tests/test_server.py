"""Server startup: configuration, connections and the transport it serves."""

import os
from unittest.mock import MagicMock, patch

import pytest

import moomoo_mcp.server as server
from moomoo_mcp.server import app_lifespan, close_services
from moomoo_mcp.services.trading_policy import (
    ENV_VAR,
    TradingMode,
    TradingModeConfigError,
    TradingPolicy,
)

REAL_POLICY = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123}))
# REAL mode requires its account allowlist, so every REAL environment here
# carries one.
REAL_ENV = {ENV_VAR: "REAL", "MOOMOO_REAL_ACC_IDS": "123"}


@pytest.fixture(autouse=True)
def fresh_process_services():
    """Give each test a process that has not built its services yet.

    They are deliberately built once and cached for the life of the process, so
    without this a test would be handed whatever the previous one connected.
    """
    server._services = None
    yield
    server._services = None


class TestNoStartupUnlock:
    """Startup auto-unlock is gone, and nothing replaced it.

    It was a second unlock path, and the SDK replays a cached unlock after
    every reconnect, so it left the gateway unlocked for the life of the
    process. REAL writes now unlock just in time for one order instead.
    """

    def test_the_server_module_has_no_auto_unlock(self) -> None:
        assert not hasattr(server, "_auto_unlock_trade")


class TestLifespanResilience:
    """MCP must start, and stay diagnosable, when OpenD is unreachable (R1)."""

    @staticmethod
    def _patched_services(quote_error=None, trade_error=None):
        """Patch the service classes used by app_lifespan with mocks."""
        moomoo_service = MagicMock()
        trade_service = MagicMock()

        def connect_quote():
            if quote_error:
                raise quote_error
            moomoo_service.quote_ctx = MagicMock()

        def connect_trade():
            if trade_error:
                trade_service.trade_ctx = None
                raise trade_error
            trade_service.trade_ctx = MagicMock()

        moomoo_service.quote_ctx = None
        trade_service.trade_ctx = None
        moomoo_service.connect.side_effect = connect_quote
        trade_service.connect.side_effect = connect_trade
        return moomoo_service, trade_service

    @pytest.mark.asyncio
    async def test_startup_survives_total_gateway_failure(self) -> None:
        moomoo_service, trade_service = self._patched_services(
            quote_error=OSError("connection refused"),
            trade_error=OSError("connection refused"),
        )

        with (
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", return_value=trade_service),
            patch("moomoo_mcp.server.MarketDataService") as market_data_cls,
        ):
            async with app_lifespan(MagicMock()) as app_context:
                assert app_context.moomoo_service is moomoo_service
                assert app_context.trade_service is trade_service

        # A failed trade connection must not trigger an unlock attempt.
        trade_service.unlock_trade.assert_not_called()
        market_data_cls.assert_called_once_with(quote_ctx=None)

        # The session ending is not the process ending: the connections outlive
        # it, and only shutdown releases them.
        trade_service.close.assert_not_called()
        moomoo_service.close.assert_not_called()

        close_services()
        trade_service.close.assert_called_once()
        moomoo_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_startup_still_releases_the_quote_context(self) -> None:
        moomoo_service, trade_service = self._patched_services(
            trade_error=OSError("trade svr not ready")
        )

        with (
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", return_value=trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass

        assert moomoo_service.quote_ctx is not None

        close_services()
        moomoo_service.close.assert_called_once()
        trade_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_sessions_share_one_set_of_connections(self) -> None:
        """Every session after the first reuses what the first one opened.

        The MCP lifespan runs per session — per request once the server is
        stateless — so building connections in it meant a pair of OpenD sockets
        and a trade-connect wait for every client, and for every tool call.
        """
        moomoo_service, trade_service = self._patched_services()

        with (
            patch.dict(os.environ, {"MOOMOO_TRADING_MARKET": "US"}, clear=True),
            patch(
                "moomoo_mcp.server.MoomooService", return_value=moomoo_service
            ) as quote_cls,
            patch(
                "moomoo_mcp.server.TradeService", return_value=trade_service
            ) as trade_cls,
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()) as first:
                pass
            async with app_lifespan(MagicMock()) as second:
                pass

        assert first is second
        quote_cls.assert_called_once()
        trade_cls.assert_called_once()
        assert trade_cls.call_args.kwargs["trading_market"] == "US"
        moomoo_service.connect.assert_called_once()
        trade_service.connect.assert_called_once()
        trade_service.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self) -> None:
        """atexit may fire after an explicit shutdown has already run."""
        moomoo_service, trade_service = self._patched_services()

        with (
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", return_value=trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass

        close_services()
        close_services()

        trade_service.close.assert_called_once()
        moomoo_service.close.assert_called_once()


class TestStartupTradingMode:
    """MOOMOO_TRADING_MODE governs startup behavior (R3)."""

    @staticmethod
    def _mock_services():
        moomoo_service = MagicMock()
        trade_service = MagicMock()
        moomoo_service.connect.side_effect = lambda: setattr(
            moomoo_service, "quote_ctx", MagicMock()
        )
        trade_service.connect.side_effect = lambda: setattr(
            trade_service, "trade_ctx", MagicMock()
        )
        return moomoo_service, trade_service

    async def _start(self, env):
        moomoo_service, trade_service = self._mock_services()
        captured = {}

        def make_trade_service(**kwargs):
            captured.update(kwargs)
            trade_service.policy = kwargs["policy"]
            return trade_service

        with (
            patch.dict(os.environ, env, clear=True),
            patch("moomoo_mcp.server.MoomooService", return_value=moomoo_service),
            patch("moomoo_mcp.server.TradeService", side_effect=make_trade_service),
            patch("moomoo_mcp.server.MarketDataService"),
        ):
            async with app_lifespan(MagicMock()):
                pass
        return captured, trade_service

    @pytest.mark.asyncio
    async def test_absent_mode_configures_read_only(self) -> None:
        captured, _ = await self._start({})

        assert captured["policy"].mode is TradingMode.READ_ONLY
        assert captured["trading_market"] == "NONE"

    @pytest.mark.parametrize("market", ["NONE", "US", "HK"])
    @pytest.mark.asyncio
    async def test_configured_market_reaches_the_service(self, market: str) -> None:
        captured, _ = await self._start({"MOOMOO_TRADING_MARKET": market})

        assert captured["trading_market"] == market

    @pytest.mark.asyncio
    async def test_configured_mode_reaches_the_service(self) -> None:
        captured, _ = await self._start({ENV_VAR: "SIMULATE"})

        assert captured["policy"].mode is TradingMode.SIMULATE

    @pytest.mark.asyncio
    async def test_unknown_mode_fails_startup(self) -> None:
        with pytest.raises(TradingModeConfigError):
            await self._start({ENV_VAR: "PAPER"})

    @pytest.mark.asyncio
    async def test_password_does_not_unlock_in_read_only(self) -> None:
        _, trade_service = await self._start({"MOOMOO_TRADE_PASSWORD": "hunter2"})

        trade_service.unlock_trade.assert_not_called()
        assert trade_service.policy.mode is TradingMode.READ_ONLY

    @pytest.mark.asyncio
    async def test_password_does_not_unlock_in_simulate(self) -> None:
        _, trade_service = await self._start(
            {ENV_VAR: "SIMULATE", "MOOMOO_TRADE_PASSWORD": "hunter2"}
        )

        trade_service.unlock_trade.assert_not_called()
        assert trade_service.policy.mode is TradingMode.SIMULATE

    @pytest.mark.asyncio
    async def test_real_mode_with_a_password_does_not_unlock_at_startup(
        self,
    ) -> None:
        """The credential is handed to the service for just-in-time use."""
        captured, trade_service = await self._start(
            {**REAL_ENV, "MOOMOO_TRADE_PASSWORD": "hunter2"}
        )

        trade_service.unlock_trade.assert_not_called()
        assert captured["trade_password"] == "hunter2"

    @pytest.mark.asyncio
    async def test_real_mode_without_an_allowlist_fails_startup(self) -> None:
        with pytest.raises(TradingModeConfigError, match="MOOMOO_REAL_ACC_IDS"):
            await self._start({ENV_VAR: "REAL"})

    @pytest.mark.asyncio
    async def test_an_instrument_lookup_is_wired_to_the_quote_context(self) -> None:
        captured, _ = await self._start({})

        assert captured["instrument_lookup"] is not None


class TestStartupConfigurationFailure:
    """An invalid variable exits before anything listens."""

    @pytest.mark.parametrize(
        "env",
        [
            {ENV_VAR: "PAPER"},
            {ENV_VAR: "REAL"},  # no allowlist
            {"MOOMOO_MAX_ORDER_NOTIONAL": "25000"},  # legacy variable alone
            {"MOOMOO_SECURITY_FIRM": "NOTAFIRM"},
            {"MOOMOO_TRADING_MARKET": "USA"},
            {"MOOMOO_OPEND_PORT": "not-a-port"},
        ],
    )
    def test_main_exits_before_serving(self, env) -> None:
        """A server running with configuration it rejected would fail every
        request, quietly, one at a time. Exiting names the variable instead."""
        from moomoo_mcp.server import main

        with (
            patch.dict(os.environ, env, clear=True),
            patch("moomoo_mcp.server.mcp.run") as mock_mcp_run,
            patch("uvicorn.run") as mock_uvicorn_run,
            pytest.raises(SystemExit),
        ):
            main()

        mock_mcp_run.assert_not_called()
        mock_uvicorn_run.assert_not_called()

    def test_a_valid_configuration_serves(self) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(os.environ, {"MCP_TRANSPORT": "stdio"}, clear=True),
            patch("moomoo_mcp.server.mcp.run") as mock_mcp_run,
        ):
            main()

        mock_mcp_run.assert_called_once()


class TestFastMCPSecurity:
    """Tests for FastMCP HTTP/SSE authentication and transport security."""

    def test_sse_app_rejects_missing_auth_token(self) -> None:
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_sse_app

        app = create_sse_app(auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get("/sse")
        assert response.status_code == 401
        assert "Unauthorized" in response.text

    def test_sse_app_rejects_invalid_bearer_token(self) -> None:
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_sse_app

        app = create_sse_app(auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get(
            "/sse",
            headers={"Authorization": "Bearer wrong_token_abc"},
        )
        assert response.status_code == 401
        assert "Unauthorized" in response.text

    def test_bearer_auth_middleware_accepts_valid_token(self) -> None:
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from moomoo_mcp.server import BearerAuthMiddleware

        async def endpoint(_request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/test", endpoint)])
        app.add_middleware(BearerAuthMiddleware, auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get(
            "/test",
            headers={"Authorization": "Bearer test_secure_token_123"},
        )
        assert response.status_code == 200
        assert response.text == "ok"

    def test_bearer_auth_middleware_rejects_invalid_token(self) -> None:
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from moomoo_mcp.server import BearerAuthMiddleware

        async def endpoint(_request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/test", endpoint)])
        app.add_middleware(BearerAuthMiddleware, auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get(
            "/test",
            headers={"Authorization": "Bearer wrong_token"},
        )
        assert response.status_code == 401

    def test_streamable_http_app_rejects_missing_auth_token(self) -> None:
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 401
        assert "Unauthorized" in response.text

    def test_streamable_http_app_rejects_invalid_bearer_token(self) -> None:
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_secure_token_123")
        client = TestClient(app)

        response = client.get(
            "/",
            headers={"Authorization": "Bearer wrong_token_abc"},
        )
        assert response.status_code == 401
        assert "Unauthorized" in response.text

    def test_main_rejects_invalid_transport(self) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(os.environ, {"MCP_TRANSPORT": "unsupported-mode"}, clear=True),
            pytest.raises(SystemExit),
        ):
            main()

    def test_main_starts_streamable_http_with_auth(self) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(
                os.environ,
                {
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_AUTH_TOKEN": "my-secret-token",
                },
            ),
            patch("moomoo_mcp.server.create_streamable_http_app") as mock_create,
            patch("uvicorn.run") as mock_uvicorn_run,
        ):
            mock_app = MagicMock()
            mock_create.return_value = mock_app

            main()

            mock_create.assert_called_once_with(auth_token="my-secret-token")
            mock_uvicorn_run.assert_called_once_with(
                mock_app, host="127.0.0.1", port=8000
            )

    def test_main_starts_sse_with_auth(self) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(
                os.environ,
                {
                    "MCP_TRANSPORT": "sse",
                    "MCP_AUTH_TOKEN": "my-secret-token",
                },
            ),
            patch("moomoo_mcp.server.create_sse_app") as mock_create,
            patch("uvicorn.run") as mock_uvicorn_run,
        ):
            mock_app = MagicMock()
            mock_create.return_value = mock_app

            main()

            mock_create.assert_called_once_with(auth_token="my-secret-token")
            mock_uvicorn_run.assert_called_once_with(
                mock_app, host="127.0.0.1", port=8000
            )

    def test_main_logs_streamable_http_endpoint_with_auth(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from moomoo_mcp.server import main

        with (
            caplog.at_level(logging.INFO),
            patch.dict(
                os.environ,
                {
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_AUTH_TOKEN": "my-secret-token",
                },
            ),
            patch("moomoo_mcp.server.create_streamable_http_app"),
            patch("uvicorn.run"),
        ):
            main()
            assert (
                "Serving MCP streamable-http endpoint at http://127.0.0.1:8000/mcp"
                in caplog.text
            )

    @pytest.mark.parametrize("transport", ["sse", "streamable-http"])
    def test_main_refuses_http_without_a_token(self, transport) -> None:
        """An unauthenticated HTTP endpoint exposes every tool this server has,
        including the order-mutating ones, to anything that can reach the port.
        """
        from moomoo_mcp.server import main

        with (
            patch.dict(os.environ, {"MCP_TRANSPORT": transport}, clear=True),
            patch("moomoo_mcp.server.mcp.run") as mock_mcp_run,
            patch("uvicorn.run") as mock_uvicorn_run,
            pytest.raises(SystemExit),
        ):
            main()

        mock_mcp_run.assert_not_called()
        mock_uvicorn_run.assert_not_called()

    def test_the_opt_out_serves_unauthenticated_in_read_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from moomoo_mcp.server import main

        with (
            caplog.at_level(logging.WARNING),
            patch.dict(
                os.environ,
                {
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_ALLOW_UNAUTHENTICATED_HTTP": "1",
                    ENV_VAR: "READ_ONLY",
                },
                clear=True,
            ),
            patch("moomoo_mcp.server.create_streamable_http_app"),
            patch("uvicorn.run") as mock_uvicorn_run,
        ):
            main()

        mock_uvicorn_run.assert_called_once()
        assert "without authentication" in caplog.text

    @pytest.mark.parametrize("env", [REAL_ENV, {ENV_VAR: "SIMULATE"}])
    def test_the_opt_out_is_refused_in_a_writing_mode(self, env) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(
                os.environ,
                {
                    **env,
                    "MCP_TRANSPORT": "streamable-http",
                    "MCP_ALLOW_UNAUTHENTICATED_HTTP": "1",
                },
                clear=True,
            ),
            patch("uvicorn.run") as mock_uvicorn_run,
            pytest.raises(SystemExit),
        ):
            main()

        mock_uvicorn_run.assert_not_called()

    def test_stdio_starts_without_a_token(self) -> None:
        from moomoo_mcp.server import main

        with (
            patch.dict(os.environ, {"MCP_TRANSPORT": "stdio"}, clear=True),
            patch("moomoo_mcp.server.mcp.run") as mock_mcp_run,
        ):
            main()

        mock_mcp_run.assert_called_once()


class TestStatelessStreamableHTTP:
    """Tests pinning stateless_http and json_response under Streamable HTTP."""

    @pytest.fixture(autouse=True)
    def reset_streamable_session_manager(self):
        """Reset FastMCP session manager and mock services for each test."""
        server.mcp._session_manager = None
        mock_services = MagicMock()
        mock_check = MagicMock()
        mock_check.futures = ()
        mock_check.remaining.return_value = 1.0
        mock_check.result.return_value = {"status": "ok", "trading_mode": "READ_ONLY"}
        mock_services.moomoo_service.start_health_check.return_value = mock_check
        with patch("moomoo_mcp.server.get_services", return_value=mock_services):
            yield
        server.mcp._session_manager = None

    def test_pinned_fastmcp_settings(self) -> None:
        """FastMCP settings must explicitly pin stateless_http and json_response."""
        assert server.mcp.settings.stateless_http is True
        assert server.mcp.settings.json_response is True

    def test_initialize_issues_no_session_id(self) -> None:
        """initialize returns a JSON response without an mcp-session-id header."""
        import mcp.types as types
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_token")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": types.LATEST_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={
                    "Authorization": "Bearer test_token",
                    "Accept": "application/json",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("content-type") == "application/json"
            assert "mcp-session-id" not in resp.headers
            body = resp.json()
            assert body.get("id") == 1
            assert "protocolVersion" in body.get("result", {})

    def test_request_served_without_prior_initialize(self) -> None:
        """tools/list is served without a prior initialize request."""
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_token")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Authorization": "Bearer test_token",
                    "Accept": "application/json",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("content-type") == "application/json"
            assert "mcp-session-id" not in resp.headers
            body = resp.json()
            assert body.get("id") == 2
            assert "tools" in body.get("result", {})

    def test_foreign_session_id_is_ignored_and_request_succeeds(self) -> None:
        """A foreign mcp-session-id is ignored rather than rejected as unknown."""
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_token")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Authorization": "Bearer test_token",
                    "Accept": "application/json",
                    "mcp-session-id": "foreign-session-uuid-from-previous-run",
                },
            )
            assert resp.status_code == 200
            assert resp.headers.get("content-type") == "application/json"
            assert "mcp-session-id" not in resp.headers
            body = resp.json()
            assert body.get("id") == 3
            assert "tools" in body.get("result", {})

    def test_session_id_does_not_stand_in_for_authentication(self) -> None:
        """A request with mcp-session-id without valid auth is rejected with 401."""
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        app = create_streamable_http_app(auth_token="test_token")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "Accept": "application/json",
                    "mcp-session-id": "foreign-session-uuid-from-previous-run",
                },
            )
            assert resp.status_code == 401
            assert "Unauthorized" in resp.text

    def test_call_tool_answered_with_single_json_response_and_notifications_dropped(
        self,
    ) -> None:
        """Tool call is answered with single JSON result, dropping notifications."""
        from typing import Any

        from mcp.server.fastmcp import Context
        from mcp.server.session import ServerSession
        from starlette.testclient import TestClient

        from moomoo_mcp.server import create_streamable_http_app

        test_tool_name = "_test_stateless_notification_drop"

        @server.mcp.tool(name=test_tool_name)
        async def _test_stateless_notification_tool(ctx: Context) -> str:
            await ctx.info("sentinel-notification-dropped")
            return "sentinel-tool-result"

        try:
            orig_send_log = ServerSession.send_log_message
            captured_logs: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

            async def spy_send_log(
                session_self: ServerSession, *args: Any, **kwargs: Any
            ) -> None:
                captured_logs.append((args, kwargs))
                await orig_send_log(session_self, *args, **kwargs)

            with patch.object(ServerSession, "send_log_message", new=spy_send_log):
                app = create_streamable_http_app(auth_token="test_token")
                with TestClient(app) as client:
                    resp = client.post(
                        "/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id": 5,
                            "method": "tools/call",
                            "params": {
                                "name": test_tool_name,
                                "arguments": {},
                            },
                        },
                        headers={
                            "Authorization": "Bearer test_token",
                            "Accept": "application/json",
                        },
                    )
                    assert resp.status_code == 200
                    assert resp.headers.get("content-type") == "application/json"
                    body = resp.json()
                    assert body.get("id") == 5
                    assert body.get("result", {}).get("isError") is False
                    assert "structuredContent" in body.get("result", {})

                    # 1. Prove a logging notification was emitted during tool execution
                    assert len(captured_logs) == 1
                    _, log_kwargs = captured_logs[0]
                    assert log_kwargs.get("data") == "sentinel-notification-dropped"

                    # 2. Prove response carries result alone and omits notifications
                    assert "sentinel-tool-result" in resp.text
                    assert "sentinel-notification-dropped" not in resp.text
        finally:
            server.mcp._tool_manager.remove_tool(test_tool_name)
