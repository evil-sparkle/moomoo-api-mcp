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

import pandas as pd
import pytest

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.conftest import call_mcp_tool

SERVICE_NAMES = {"trade_service", "market_data_service", "moomoo_service"}


def _direct_service_calls(path: pathlib.Path) -> list[str]:
    """Find service calls invoked directly rather than handed to run_blocking."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if isinstance(target, ast.Name) and target.id in SERVICE_NAMES:
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


class TestConcurrentRequests:
    """A slow tool call must not delay a concurrent health check."""

    @pytest.fixture
    def slow_context(self, mcp_app_context):
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
