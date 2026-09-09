"""Tests for active gateway health probes (R1)."""

import pathlib
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.health import (
    HEALTH_DEADLINE_SECONDS,
    SYNC_CONNECT_TIMEOUT_SECONDS,
    BoundedProbe,
    aggregate_status,
    sanitize_error,
)
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy

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


class TestHealthReportsTradingMode:
    """Health exposes the configured policy (R3)."""

    @pytest.mark.parametrize("mode", list(TradingMode))
    def test_configured_mode_is_reported(self, services, mode):
        moomoo_service, trade_service = services
        trade_service.policy = TradingPolicy(mode)

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["trading_mode"] == mode.value

    def test_default_service_reports_read_only(self, services):
        moomoo_service, trade_service = services

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["trading_mode"] == "READ_ONLY"


class TestConnectDoesNotBlockStartup:
    """The SDK retries a refused connection forever instead of raising."""

    def test_quote_connect_uses_async_connect(self):
        """Without is_async_connect the constructor never returns.

        OpenContextBase.__init__ loops on a six-second retry while
        _auto_reconnect is set, so an inline construction hangs the lifespan
        before it yields and check_health never becomes reachable.
        """
        service = MoomooService(host="10.0.0.5", port=22222)
        with patch(
            "moomoo_mcp.services.base_service.OpenQuoteContext"
        ) as ctx_class:
            service.connect()

        assert ctx_class.call_args.kwargs["is_async_connect"] is True
        service.close()

    def test_quote_connect_bounds_sync_queries(self):
        """A query against a not-yet-ready context must not wait forever."""
        service = MoomooService()
        with patch(
            "moomoo_mcp.services.base_service.OpenQuoteContext"
        ) as ctx_class:
            service.connect()

        timeout = ctx_class.return_value.set_sync_query_connect_timeout.call_args
        assert timeout.args[0] == SYNC_CONNECT_TIMEOUT_SECONDS
        # Shorter than the health deadline, so a probe returns the gateway's own
        # diagnostic instead of being cut off by the deadline.
        assert SYNC_CONNECT_TIMEOUT_SECONDS < HEALTH_DEADLINE_SECONDS
        service.close()

    def test_trade_connect_returns_when_the_gateway_never_answers(self):
        """OpenSecTradeContext has no async-connect option, so it is bounded."""
        started = threading.Event()
        release = threading.Event()

        def never_connects(**_):
            started.set()
            release.wait(30)
            return MagicMock()

        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=never_connects,
        ):
            begin = time.monotonic()
            service.connect(timeout=0.3)
            elapsed = time.monotonic() - begin

            assert started.wait(5), "the connection attempt never started"
            assert elapsed < 3.0, f"connect blocked startup for {elapsed:.2f}s"
            assert service.trade_ctx is None

            # Health stays answerable and reports the trade side honestly.
            health = MoomooService().check_health(trade_service=service, deadline=1.0)
            assert health["trade"]["reason"] == "not_initialized"

            release.set()
            service.close()

    def test_repeated_connects_do_not_stack_attempts(self):
        attempts = []
        release = threading.Event()

        def never_connects(**_):
            attempts.append(1)
            release.wait(30)
            return MagicMock()

        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=never_connects,
        ):
            for _ in range(4):
                service.connect(timeout=0.1)

            assert len(attempts) == 1
            release.set()
            service.close()

    def test_a_late_connection_is_closed_if_shutdown_already_ran(self):
        """A worker that finally connects after close() must not leak it."""
        release = threading.Event()
        built = MagicMock()

        def slow_connect(**_):
            release.wait(30)
            return built

        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=slow_connect,
        ):
            service.connect(timeout=0.1)
            service.close()
            release.set()
            time.sleep(0.3)

        assert service.trade_ctx is None
        built.close.assert_called_once()

    def test_a_connection_that_arrives_late_is_published(self):
        release = threading.Event()
        built = MagicMock()

        def slow_connect(**_):
            release.wait(30)
            return built

        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=slow_connect,
        ):
            service.connect(timeout=0.1)
            assert service.trade_ctx is None

            release.set()
            deadline = time.monotonic() + 5
            while service.trade_ctx is None and time.monotonic() < deadline:
                time.sleep(0.02)

            assert service.trade_ctx is built
            service.close()


# Run in a subprocess: the defect this guards is at interpreter *exit*, which an
# in-process test cannot observe. The constructor is deliberately never
# released, so a regression shows up as a process that will not terminate.
_STUCK_SHUTDOWN_SCRIPT = """
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])

from moomoo_mcp.services.trade_service import TradeService

blocked = threading.Event()


def never_returns(**_):
    # Models OpenContextBase.__init__, which retries every six seconds for as
    # long as OpenD is down and cannot be interrupted from outside.
    blocked.wait()


with patch(
    "moomoo_mcp.services.trade_service.OpenSecTradeContext",
    side_effect=never_returns,
):
    service = TradeService()
    service.connect(timeout=0.2)
    service.close()

print("closed", flush=True)
# `blocked` is never set. Falling off the end here must be enough to exit.
"""


class TestShutdownWithAStuckConnection:
    """Shutdown must not depend on a connection attempt ever finishing."""

    def test_the_process_exits_without_releasing_the_constructor(self):
        """A ThreadPoolExecutor worker would hold the interpreter open here.

        ``shutdown(wait=False)`` bounds only the caller: concurrent.futures
        registers an atexit hook that joins its non-daemon workers, so the
        process stays alive until the SDK constructor returns — which, against a
        down gateway, is never.
        """
        src = str(pathlib.Path(__file__).resolve().parents[2] / "src")
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _STUCK_SHUTDOWN_SCRIPT, src],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                "the interpreter could not exit while a connection attempt was "
                "still running; shutdown waits on a worker it cannot stop"
            )

        assert "closed" in proc.stdout, proc.stderr
        assert proc.returncode == 0, proc.stderr

    def test_close_returns_while_the_constructor_is_still_running(self):
        started = threading.Event()

        def never_returns(**_):
            started.set()
            # Never released: the point is that close() does not need it to be.
            threading.Event().wait()

        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=never_returns,
        ):
            service.connect(timeout=0.2)
            assert started.wait(5), "the connection attempt never started"

            begin = time.monotonic()
            service.close()
            elapsed = time.monotonic() - begin

        assert elapsed < 1.0, f"close() waited {elapsed:.2f}s on a stuck worker"
        assert service.trade_ctx is None

    def test_workers_are_daemon_threads(self):
        """Nothing this package starts may be joined at interpreter exit."""
        service = TradeService()
        with patch(
            "moomoo_mcp.services.trade_service.OpenSecTradeContext",
            side_effect=lambda **_: threading.Event().wait(),
        ):
            service.connect(timeout=0.2)

        probe = BoundedProbe("stuck")
        probe.submit(lambda: threading.Event().wait())

        alive = threading.enumerate()
        # Other tests in this module also leave stuck workers behind, so this
        # asserts on what every one of them must be, not on how many there are.
        connect = [t for t in alive if t.name == "trade-connect"]
        probes = [t for t in alive if t.name == "health-stuck"]
        assert connect and probes, f"workers did not start: {alive}"
        non_daemon = [t for t in connect + probes if not t.daemon]
        assert not non_daemon, (
            f"these are joined at interpreter exit, so a stuck SDK call would "
            f"hang the process: {non_daemon}"
        )

        probe.close()
        service.close()


# The exact payload a live OpenD 1010 gateway returned, trimmed to the fields
# health reads. Recorded during a read-only smoke test: `qot_logined` is a
# bool, not the '1'/'0' string the SDK's docstring describes.
LIVE_GLOBAL_STATE = {
    "market_us": "AFTER_HOURS_BEGIN",
    "market_hk": "CLOSED",
    "server_ver": "1010",
    "trd_logined": True,
    "qot_logined": True,
    "program_status_type": "READY",
}


class TestGatewayLoginFlag:
    """The login flag reaches us as a bool, whatever the SDK docs say."""

    def test_a_logged_in_gateway_is_not_reported_as_logged_out(self, services):
        """Against a live gateway `str(True) == "1"` was False, inverting this."""
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.return_value = (
            0,
            dict(LIVE_GLOBAL_STATE),
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["quote"]["logged_in"] is True
        assert health["quote"]["status"] == "ok"
        assert health["gateway_version"] == "1010"

    def test_a_logged_out_gateway_is_reported_as_logged_out(self, services):
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.return_value = (
            0,
            {**LIVE_GLOBAL_STATE, "qot_logined": False},
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["quote"]["logged_in"] is False
        # Status still follows the return code, so this stays visible rather
        # than being reclassified as a failure.
        assert health["quote"]["status"] == "ok"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, True),
            (False, False),
            ("1", True),
            ("0", False),
            (1, True),
            (0, False),
        ],
    )
    def test_both_documented_and_actual_shapes_are_accepted(
        self, services, raw, expected
    ):
        moomoo_service, trade_service = services
        moomoo_service.quote_ctx.get_global_state.return_value = (
            0,
            {**LIVE_GLOBAL_STATE, "qot_logined": raw},
        )

        health = moomoo_service.check_health(trade_service=trade_service)

        assert health["quote"]["logged_in"] is expected
