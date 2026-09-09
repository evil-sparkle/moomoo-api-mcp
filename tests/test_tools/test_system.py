"""Tests for the check_health MCP tool (R1)."""

import threading
from unittest.mock import MagicMock

import pytest

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.trade_service import TradeService

HEALTHY = {
    "status": "connected",
    "host": "127.0.0.1:11111",
    "checked_at": "2026-09-10T12:00:00Z",
    "quote": {"status": "ok", "logged_in": True},
    "trade": {"status": "ok", "account_count": 2},
    "gateway_version": "9.2.5208",
}


class TestCheckHealthTool:
    """The tool forwards both services and returns the observed status."""

    @pytest.mark.asyncio
    async def test_returns_health_through_mcp(
        self, call_tool, mock_moomoo_service, mock_trade_service
    ):
        mock_moomoo_service.check_health.return_value = HEALTHY

        result = await call_tool("check_health")

        assert result.structured == HEALTHY
        assert result.json["status"] == "connected"
        assert result.json["gateway_version"] == "9.2.5208"
        mock_moomoo_service.check_health.assert_called_once_with(
            trade_service=mock_trade_service
        )

    @pytest.mark.asyncio
    async def test_degraded_status_identifies_failing_service(
        self, call_tool, mock_moomoo_service
    ):
        mock_moomoo_service.check_health.return_value = {
            **HEALTHY,
            "status": "degraded",
            "trade": {
                "status": "error",
                "reason": "gateway_error",
                "error": "trade svr not ready",
            },
        }

        result = await call_tool("check_health")

        assert result.structured["status"] == "degraded"
        assert result.structured["trade"]["error"] == "trade svr not ready"
        assert result.structured["quote"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_disconnected_status_is_reported(
        self, call_tool, mock_moomoo_service
    ):
        mock_moomoo_service.check_health.return_value = {
            **HEALTHY,
            "status": "disconnected",
            "quote": {"status": "error", "reason": "gateway_error", "error": "boom"},
            "trade": {"status": "error", "reason": "gateway_error", "error": "boom"},
            "gateway_version": None,
        }

        result = await call_tool("check_health")

        assert result.structured["status"] == "disconnected"
        assert result.structured["gateway_version"] is None


class TestCheckHealthEndToEnd:
    """Real services with mocked SDK contexts, dispatched through MCP."""

    @pytest.fixture
    def live_context(self, mcp_app_context):
        moomoo_service = MoomooService(host="10.0.0.5", port=22222)
        moomoo_service.quote_ctx = MagicMock()
        moomoo_service.quote_ctx.get_global_state.return_value = (
            0,
            {"server_ver": "9.2.5208", "qot_logined": "1"},
        )
        trade_service = TradeService()
        trade_service.trade_ctx = MagicMock()
        trade_service.trade_ctx.get_acc_list.return_value = (0, [{"acc_id": 1}])

        mcp_app_context.moomoo_service = moomoo_service
        mcp_app_context.trade_service = trade_service
        yield mcp_app_context
        moomoo_service.close()
        trade_service.close()

    @pytest.mark.asyncio
    async def test_connected_shape_crosses_the_mcp_boundary(
        self, call_tool, live_context
    ):
        result = await call_tool("check_health")

        quote_service = live_context.moomoo_service
        payload = result.json
        assert payload["status"] == "connected"
        assert payload["host"] == f"{quote_service.host}:{quote_service.port}"
        assert payload["checked_at"].endswith("Z")
        assert payload["quote"]["status"] == "ok"
        assert payload["trade"] == {"status": "ok", "account_count": 1}
        assert payload["gateway_version"] == "9.2.5208"

    @pytest.mark.asyncio
    async def test_stuck_gateway_times_out_without_stalling_mcp(
        self, call_tool, live_context, monkeypatch
    ):
        monkeypatch.setattr(
            "moomoo_mcp.services.base_service.HEALTH_DEADLINE_SECONDS", 0.3
        )
        blocked = threading.Event()
        live_context.moomoo_service.quote_ctx.get_global_state.side_effect = (
            lambda: blocked.wait(30) or (0, {})
        )
        try:
            result = await call_tool("check_health")
        finally:
            blocked.set()

        assert result.structured["quote"]["status"] == "timeout"
        assert result.structured["status"] == "degraded"
