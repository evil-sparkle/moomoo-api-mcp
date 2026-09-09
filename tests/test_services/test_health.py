"""Tests for active gateway health probes (R1)."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.health import BoundedProbe, aggregate_status, sanitize_error
from moomoo_mcp.services.trade_service import TradeService

GLOBAL_STATE = {
    "server_ver": "9.2.5208",
    "qot_logined": "1",
    "trd_logined": "1",
    "market_us": "CLOSED",
}


@pytest.fixture
def services():
    """A quote and trade service pair wired to mock SDK contexts."""
    moomoo_service = MoomooService(host="127.0.0.1", port=11111)
    moomoo_service.quote_ctx = MagicMock()
    moomoo_service.quote_ctx.get_global_state.return_value = (0, dict(GLOBAL_STATE))

    trade_service = TradeService()
    trade_service.trade_ctx = MagicMock()
    trade_service.trade_ctx.get_acc_list.return_value = (0, [{"acc_id": 1}])

    yield moomoo_service, trade_service

    moomoo_service.close()
    trade_service.close()


class TestHealthyGateway:
    """Both probes succeed."""

    def test_reports_connected_with_endpoint_and_timestamp(self, services):
        moomoo_service, trade_service = services

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["status"] == "connected"
        assert health["host"] == "127.0.0.1:11111"
        assert health["checked_at"].endswith("Z")
        assert health["quote"]["status"] == "ok"
        assert health["trade"]["status"] == "ok"
        assert health["gateway_version"] == "9.2.5208"

    def test_probes_are_actually_issued(self, services):
        moomoo_service, trade_service = services

        moomoo_service.check_health(trade_service=trade_service)

        moomoo_service.quote_ctx.get_global_state.assert_called_once_with()
        trade_service.trade_ctx.get_acc_list.assert_called_once_with()

    def test_no_account_contents_or_credentials_in_result(self, services):
        moomoo_service, trade_service = services
        trade_service.trade_ctx.get_acc_list.return_value = (
            0,
            [{"acc_id": 987654321098765432, "card_num": "SECRET"}],
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["trade"] == {"status": "ok", "account_count": 1}
        assert "987654321098765432" not in repr(health)
        assert "SECRET" not in repr(health)

    def test_connectivity_does_not_claim_unlocked_trading(self, services):
        """A locked account still connects; health must not imply authorization."""
        moomoo_service, trade_service = services

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["status"] == "connected"
        assert "unlocked" not in repr(health).lower()
        assert "authorized" not in repr(health).lower()


class TestPartialAndTotalFailure:
    """One, or neither, probe succeeds."""

    def test_quote_failure_is_degraded(self, services):
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.return_value = (-1, "Connect failed")

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["status"] == "degraded"
        assert health["quote"]["status"] == "error"
        assert health["quote"]["error"] == "Connect failed"
        assert health["trade"]["status"] == "ok"

    def test_trade_failure_is_degraded(self, services):
        moomoo_service, trade_service = services
        trade_service.trade_ctx.get_acc_list.return_value = (-1, "trade svr not ready")

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["status"] == "degraded"
        assert health["quote"]["status"] == "ok"
        assert health["trade"]["status"] == "error"

    def test_gateway_disappears_after_startup(self, services):
        """Contexts still exist; the gateway stopped answering."""
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.side_effect = OSError(
            "connection reset by peer"
        )
        trade_service.trade_ctx.get_acc_list.side_effect = OSError(
            "connection reset by peer"
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert moomoo_service.quote_ctx is not None
        assert trade_service.trade_ctx is not None
        assert health["status"] == "disconnected"
        assert "connection reset" in health["quote"]["error"]
        assert "connection reset" in health["trade"]["error"]

    def test_uninitialized_contexts_report_unavailable(self):
        moomoo_service = MoomooService()
        trade_service = TradeService()
        try:
            health = moomoo_service.check_health(trade_service=trade_service)
        finally:
            moomoo_service.close()
            trade_service.close()

        assert health["status"] == "disconnected"
        assert health["quote"]["reason"] == "not_initialized"
        assert health["trade"]["reason"] == "not_initialized"

    def test_permission_failure_is_labelled_separately(self, services):
        moomoo_service, trade_service = services
        trade_service.trade_ctx.get_acc_list.return_value = (
            -1,
            "no permission for this account",
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["trade"]["reason"] == "permission"
        assert health["trade"]["status"] != "ok"

    def test_transport_failure_is_not_labelled_permission(self, services):
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.return_value = (-1, "socket closed")

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["quote"]["reason"] == "gateway_error"


class TestDeadlineAndBoundedWorkers:
    """A stuck gateway must not stall or accumulate work."""

    def test_deadline_is_respected(self, services):
        moomoo_service, trade_service = services
        blocked = threading.Event()
        moomoo_service.quote_ctx.get_global_state.side_effect = (
            lambda: blocked.wait() or (0, dict(GLOBAL_STATE))
        )

        try:
            started = time.monotonic()
            health = moomoo_service.check_health(
                trade_service=trade_service, deadline=0.3
            )
            elapsed = time.monotonic() - started

            assert elapsed < 3.0
            assert health["quote"]["status"] == "timeout"
            assert health["quote"]["reason"] == "timeout"
            assert health["status"] == "degraded"
        finally:
            blocked.set()

    def test_repeated_calls_do_not_accumulate_workers(self, services):
        moomoo_service, trade_service = services
        blocked = threading.Event()
        started_probes = []

        def hang():
            started_probes.append(1)
            blocked.wait()
            return (0, dict(GLOBAL_STATE))

        moomoo_service.quote_ctx.get_global_state.side_effect = hang

        try:
            for _ in range(5):
                health = moomoo_service.check_health(
                    trade_service=trade_service, deadline=0.15
                )
                assert health["quote"]["status"] == "timeout"

            # Five health calls, one stuck worker: later calls joined the
            # in-flight probe instead of starting another.
            assert len(started_probes) == 1
            assert moomoo_service._quote_probe.in_flight is True
        finally:
            blocked.set()

    def test_both_probes_share_one_deadline(self, services):
        """A hung quote probe must not grant the trade probe extra time."""
        moomoo_service, trade_service = services
        blocked = threading.Event()
        moomoo_service.quote_ctx.get_global_state.side_effect = (
            lambda: blocked.wait() or (0, dict(GLOBAL_STATE))
        )
        trade_service.trade_ctx.get_acc_list.side_effect = (
            lambda: blocked.wait() or (0, [])
        )

        try:
            started = time.monotonic()
            health = moomoo_service.check_health(
                trade_service=trade_service, deadline=0.3
            )
            elapsed = time.monotonic() - started

            assert elapsed < 1.0
            assert health["status"] == "disconnected"
            assert health["quote"]["status"] == "timeout"
            assert health["trade"]["status"] == "timeout"
        finally:
            blocked.set()

    def test_probe_recovers_after_a_completed_call(self, services):
        moomoo_service, _ = services

        first = moomoo_service.probe_quote()
        second = moomoo_service.check_health()

        assert first["status"] == "ok"
        assert second["quote"]["status"] == "ok"
        assert moomoo_service.quote_ctx.get_global_state.call_count == 2


class TestStartupResilience:
    """Health must survive a failed downstream startup."""

    def test_health_available_when_trade_init_fails_after_quote(self):
        moomoo_service = MoomooService()
        moomoo_service.quote_ctx = MagicMock()
        moomoo_service.quote_ctx.get_global_state.return_value = (0, dict(GLOBAL_STATE))
        trade_service = TradeService()  # connect() raised; no context

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["status"] == "degraded"
        assert health["quote"]["status"] == "ok"
        assert health["trade"]["reason"] == "not_initialized"

        # Shutdown still releases the partially initialized quote context.
        quote_ctx = moomoo_service.quote_ctx
        trade_service.close()
        moomoo_service.close()
        quote_ctx.close.assert_called_once()
        assert moomoo_service.quote_ctx is None

    def test_close_is_safe_without_any_context(self):
        moomoo_service = MoomooService()
        trade_service = TradeService()

        moomoo_service.close()
        trade_service.close()


class TestHealthHelpers:
    """Unit coverage for the shared health primitives."""

    def test_aggregate_status_levels(self):
        ok = {"status": "ok"}
        bad = {"status": "error"}

        assert aggregate_status(ok, ok) == "connected"
        assert aggregate_status(ok, bad) == "degraded"
        assert aggregate_status(bad, bad) == "disconnected"

    def test_sanitize_error_flattens_and_truncates(self):
        assert sanitize_error("a\n  b\tc") == "a b c"
        assert len(sanitize_error("x" * 500)) == 200

    def test_bounded_probe_reports_worker_exception(self):
        probe = BoundedProbe("unit")
        try:

            def boom():
                raise ValueError("probe blew up")

            result = probe.collect(probe.submit(boom), timeout=2.0)
        finally:
            probe.close()

        assert result["status"] == "error"
        assert "probe blew up" in result["error"]
