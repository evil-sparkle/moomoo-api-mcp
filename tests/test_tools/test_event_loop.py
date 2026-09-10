"""Blocking SDK work must not stall the event loop.

FastMCP runs an `async def` tool body on the event loop, and — less obviously —
invokes a plain `def` tool inline on the loop too. Either way, a synchronous SDK
call made directly from a tool freezes every concurrent request, including the
health check an operator runs to find out what is wrong.
"""

import ast
import asyncio
import pathlib
import threading
import time
from unittest.mock import MagicMock

import anyio.to_thread
import pandas as pd
import pytest

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.health import HEALTH_DEADLINE_SECONDS
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from moomoo_mcp.tools.offload import run_blocking
from tests.conftest import call_mcp_tool

SERVICE_NAMES = {"trade_service", "market_data_service", "moomoo_service"}

# Service methods that touch no socket and return without waiting, so calling
# them on the event loop is safe. Everything else must go through run_blocking.
NON_BLOCKING_METHODS = {
    # Submits both probes to their own dedicated workers and returns a handle.
    "start_health_check",
}


def _direct_service_calls(path: pathlib.Path) -> list[str]:
    """Find service calls invoked directly rather than handed to run_blocking."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if (
                isinstance(target, ast.Name)
                and target.id in SERVICE_NAMES
                and node.func.attr not in NON_BLOCKING_METHODS
            ):
                found.append(
                    f"{path.name}:{node.lineno} {target.id}.{node.func.attr}()"
                )
    return found


def test_no_tool_calls_a_service_directly():
    """Every SDK call in the tool layer must go through run_blocking.

    A structural check, because the failure it prevents — one slow tool
    delaying every other request — only shows up under concurrency and is easy
    to reintroduce with a single new tool.
    """
    offenders = []
    for path in sorted(pathlib.Path("src/moomoo_mcp/tools").glob("*.py")):
        offenders += _direct_service_calls(path)

    assert offenders == [], (
        "these call a blocking service method on the event loop; "
        f"wrap them in run_blocking: {offenders}"
    )


@pytest.fixture
def slow_context(mcp_app_context):
    """Real services on a mock SDK whose option-chain call blocks until released."""
    release = threading.Event()

    quote_ctx = MagicMock()

    def slow_chain(**_):
        release.wait(10)
        return (0, pd.DataFrame([{"code": "US.XYZ260116C100000"}]))

    quote_ctx.get_option_chain.side_effect = slow_chain
    quote_ctx.get_global_state.return_value = (
        0,
        {"server_ver": "9.2", "qot_logined": "1"},
    )

    moomoo_service = MoomooService()
    moomoo_service.quote_ctx = quote_ctx

    trade_ctx = MagicMock()
    trade_ctx.get_acc_list.return_value = (0, [{"acc_id": 1}])
    trade_service = TradeService(policy=TradingPolicy(TradingMode.READ_ONLY))
    trade_service.trade_ctx = trade_ctx

    mcp_app_context.moomoo_service = moomoo_service
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)
    mcp_app_context.trade_service = trade_service

    yield mcp_app_context, release

    release.set()
    moomoo_service.close()
    trade_service.close()


class TestConcurrentRequests:
    """A slow tool call must not delay a concurrent health check."""

    @pytest.mark.asyncio
    async def test_health_answers_while_an_option_chain_is_blocked(self, slow_context):
        context, release = slow_context

        chain = asyncio.create_task(
            call_mcp_tool(context, "get_option_chain", {"code": "US.XYZ"})
        )
        # Let the chain call reach the (blocked) SDK before racing health.
        await asyncio.sleep(0.1)

        started = time.monotonic()
        health = await call_mcp_tool(context, "check_health")
        elapsed = time.monotonic() - started

        # Before the fix this waited for the option chain to return.
        assert elapsed < 2.0, f"health was delayed {elapsed:.2f}s by a blocked tool"
        assert health.structured["status"] == "connected"
        assert not chain.done(), "the chain call should still be blocked"

        release.set()
        await chain

    @pytest.mark.asyncio
    async def test_event_loop_keeps_ticking_during_a_blocked_call(self, slow_context):
        context, release = slow_context
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        chain = asyncio.create_task(
            call_mcp_tool(context, "get_option_chain", {"code": "US.XYZ"})
        )
        await asyncio.sleep(0.3)
        release.set()
        await chain
        ticker.cancel()

        # A frozen loop would have produced no ticks at all.
        assert ticks > 5, f"event loop only advanced {ticks} times while blocked"

    @pytest.mark.asyncio
    async def test_a_blocked_order_does_not_stall_the_loop(self, mcp_app_context):
        """Trading tools are sync `def`, which FastMCP also runs on the loop."""
        release = threading.Event()
        trade_ctx = MagicMock()

        def slow_place(**_):
            release.wait(10)
            return (0, pd.DataFrame([{"order_id": "1"}]))

        trade_ctx.place_order.side_effect = slow_place
        service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
        service.trade_ctx = trade_ctx
        mcp_app_context.trade_service = service

        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        order = asyncio.create_task(
            call_mcp_tool(
                mcp_app_context,
                "place_order",
                {
                    "code": "US.AAPL",
                    "price": 1.0,
                    "qty": 1,
                    "trd_side": "BUY",
                    "trd_env": "SIMULATE",
                    "acc_id": "456",
                },
            )
        )
        await asyncio.sleep(0.3)
        release.set()
        await order
        ticker.cancel()
        service.close()

        assert ticks > 5, f"event loop only advanced {ticks} times while blocked"


class TestHealthUnderLoad:
    """Health must answer within its deadline while every worker is busy."""

    @pytest.fixture
    def saturating(self):
        """Occupy every slot in the shared thread limiter until released."""
        release = threading.Event()

        async def fill() -> list[asyncio.Task]:
            limiter = anyio.to_thread.current_default_thread_limiter()
            tasks = [
                asyncio.create_task(run_blocking(release.wait, 30))
                for _ in range(int(limiter.total_tokens))
            ]
            # Wait until the pool is genuinely full, not merely scheduled.
            deadline = time.monotonic() + 10
            while limiter.available_tokens > 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert limiter.available_tokens == 0, "the worker pool never filled"
            return tasks

        yield fill, release
        release.set()

    @pytest.mark.asyncio
    async def test_health_answers_with_every_worker_occupied(
        self, slow_context, saturating
    ):
        """Routing health through the shared limiter made it queue, not run.

        The probes have had dedicated workers all along, but they are only
        reached once the outer call gets a turn. With all 40 slots taken that
        turn never came inside the deadline, so a saturated server reported
        nothing about its own health.
        """
        context, chain_release = slow_context
        fill, release = saturating
        tasks = await fill()

        started = time.monotonic()
        health = await call_mcp_tool(context, "check_health")
        elapsed = time.monotonic() - started

        assert elapsed < HEALTH_DEADLINE_SECONDS, (
            f"health took {elapsed:.2f}s queueing behind ordinary queries"
        )
        assert health.structured["status"] == "connected"

        release.set()
        chain_release.set()
        await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_the_deadline_covers_waiting_not_just_probing(
        self, slow_context, saturating
    ):
        """A hung gateway plus a saturated pool must still answer on time.

        The deadline is measured from when the request arrives, so whatever the
        server spends getting to the probes comes out of the same budget.
        """
        context, chain_release = slow_context
        stuck = threading.Event()
        context.moomoo_service.quote_ctx.get_global_state.side_effect = (
            lambda: stuck.wait(30) or (0, {"server_ver": "9.2"})
        )
        fill, release = saturating
        tasks = await fill()

        started = time.monotonic()
        health = await call_mcp_tool(context, "check_health")
        elapsed = time.monotonic() - started

        assert elapsed < HEALTH_DEADLINE_SECONDS + 1.0, (
            f"health overran its deadline by {elapsed - HEALTH_DEADLINE_SECONDS:.2f}s"
        )
        assert health.structured["quote"]["status"] == "timeout"
        assert health.structured["trade"]["status"] == "ok"
        assert health.structured["status"] == "degraded"

        stuck.set()
        release.set()
        chain_release.set()
        await asyncio.gather(*tasks)
