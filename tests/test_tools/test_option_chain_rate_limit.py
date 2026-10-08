"""Concurrent MCP option discovery shares admission before worker dispatch."""

import asyncio
from threading import Event
from unittest.mock import MagicMock

import anyio
import anyio.to_thread
import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import (
    OPTION_CHAIN_RATE_LIMIT,
    MarketDataService,
)
from moomoo_mcp.services.rate_limit import (
    ProviderRateLimitError,
    ProviderRequestLimiter,
)
from tests.conftest import call_mcp_tool
from tests.rate_limit_clock import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def governed_context(mcp_app_context, clock):
    quote = MagicMock()
    quote.get_option_chain.return_value = (
        0,
        pd.DataFrame([{"code": "US.XYZ261120C100000"}]),
    )
    quote.get_option_expiration_date.return_value = (0, pd.DataFrame())
    limiter = ProviderRequestLimiter(
        OPTION_CHAIN_RATE_LIMIT, clock=clock, sleep=clock.sleep
    )
    mcp_app_context.market_data_service = MarketDataService(
        quote, option_chain_limiter=limiter
    )
    return mcp_app_context


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


def quote_context(context):
    return context.market_data_service.quote_ctx


async def test_eleven_concurrent_mcp_clients_begin_at_most_ten_sdk_calls(
    governed_context, clock
):
    quote = quote_context(governed_context)
    starts = []
    response = quote.get_option_chain.return_value

    def sdk(**_kwargs):
        starts.append(clock())
        return response

    quote.get_option_chain.side_effect = sdk
    tasks = [
        asyncio.create_task(
            call_mcp_tool(governed_context, "get_option_chain", {"code": f"US.TEST{i}"})
        )
        for i in range(11)
    ]
    try:
        await clock.wait_for_sleepers()
        results = await asyncio.wait_for(asyncio.gather(*tasks[:10]), timeout=2)
        assert all(
            r.structured["result"][0]["code"] == "US.XYZ261120C100000" for r in results
        )
        assert starts == [0.0] * 10
        assert not tasks[10].done()
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0

        # Unrelated discovery remains callable while the chain request waits.
        await call_mcp_tool(
            governed_context, "get_option_expiration_date", {"code": "US.XYZ"}
        )
        quote.get_option_expiration_date.assert_called_once()
        clock.advance(5)
        with pytest.raises(Exception) as exc:
            await tasks[10]
        assert "get_option_chain rate limit exceeded" in str(exc.value)
        assert "retry_after_seconds=26" in str(exc.value)
        assert quote.get_option_chain.call_count == 10

        clock.advance(25.1)
        await call_mcp_tool(governed_context, "get_option_chain", {"code": "US.XYZ"})
        assert starts[-1] >= 30.1
        assert all(starts[i + 10] - starts[i] >= 30 for i in range(len(starts) - 10))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_capacity_expiring_within_deadline_allows_mcp_request(
    governed_context, clock
):
    service = governed_context.market_data_service
    for _ in range(10):
        service.get_option_chain("US.XYZ")
    clock.advance(28)
    waiting = asyncio.create_task(
        call_mcp_tool(governed_context, "get_option_chain", {"code": "US.XYZ"})
    )
    await clock.wait_for_sleepers()
    clock.advance(2.1)
    result = await waiting
    assert result.structured["result"][0]["code"] == "US.XYZ261120C100000"
    assert quote_context(governed_context).get_option_chain.call_count == 11


async def test_worker_congestion_cannot_expire_pending_reservations(
    governed_context, clock
):
    pool = anyio.to_thread.current_default_thread_limiter()
    original = pool.total_tokens
    pool.total_tokens = 1
    await pool.acquire()
    held = True
    service = governed_context.market_data_service
    tasks = [
        asyncio.create_task(service.get_option_chain_async("US.XYZ")) for _ in range(10)
    ]
    try:
        await wait_until(lambda: pool.statistics().tasks_waiting == 10)
        clock.advance(100)
        with pytest.raises(ProviderRateLimitError):
            service.get_option_chain("US.XYZ")
        quote_context(governed_context).get_option_chain.assert_not_called()
        pool.release()
        held = False
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert quote_context(governed_context).get_option_chain.call_count == 10
        with pytest.raises(ProviderRateLimitError):
            service.get_option_chain("US.XYZ")
    finally:
        if held:
            pool.release()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pool.total_tokens = original


async def test_cancellation_while_waiting_for_worker_releases_reservation(
    governed_context,
):
    service = governed_context.market_data_service
    # Nine dispatched attempts leave one slot for the queued request.
    for _ in range(9):
        service.get_option_chain("US.XYZ")
    pool = anyio.to_thread.current_default_thread_limiter()
    original = pool.total_tokens
    pool.total_tokens = 1
    await pool.acquire()
    waiting = asyncio.create_task(service.get_option_chain_async("US.CANCELLED"))
    try:
        await wait_until(lambda: pool.statistics().tasks_waiting == 1)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        # Reuse that slot synchronously while the pool is still occupied.
        service.get_option_chain("US.REPLACEMENT")
        assert quote_context(governed_context).get_option_chain.call_count == 10
        assert all(
            call.kwargs["code"] != "US.CANCELLED"
            for call in quote_context(governed_context).get_option_chain.call_args_list
        )
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        pool.release()
        pool.total_tokens = original


async def test_anyio_cancellation_during_quota_wait_never_dispatches(
    governed_context, clock
):
    service = governed_context.market_data_service
    for _ in range(10):
        service.get_option_chain("US.XYZ")
    scope = anyio.CancelScope()

    async def request():
        with scope:
            await service.get_option_chain_async("US.CANCELLED")

    waiting = asyncio.create_task(request())
    await clock.wait_for_sleepers()
    scope.cancel()
    await waiting
    assert quote_context(governed_context).get_option_chain.call_count == 10
    clock.advance(30.1)
    service.get_option_chain("US.REPLACEMENT")


async def test_anyio_cancellation_after_worker_assignment_prevents_dispatch(
    governed_context, monkeypatch
):
    service = governed_context.market_data_service
    quote = quote_context(governed_context)
    for _ in range(9):
        service.get_option_chain("US.XYZ")
    loop = asyncio.get_running_loop()
    assigned = asyncio.Event()
    finished = asyncio.Event()
    release = Event()
    worker_errors = []
    original_query = service._query_option_chain

    def paused_query(*args, **kwargs):
        loop.call_soon_threadsafe(assigned.set)
        try:
            if not release.wait(timeout=5):
                raise AssertionError("test did not release SDK worker")
            return original_query(*args, **kwargs)
        except RuntimeError as exc:
            worker_errors.append(str(exc))
            raise
        finally:
            loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(service, "_query_option_chain", paused_query)
    scope = anyio.CancelScope()

    async def request():
        with scope:
            await service.get_option_chain_async("US.CANCELLED")

    waiting = asyncio.create_task(request())
    try:
        await asyncio.wait_for(assigned.wait(), timeout=2)
        # The assigned worker holds its callable, so replacement calls can use
        # the real query while the cancelled worker is still paused.
        monkeypatch.setattr(service, "_query_option_chain", original_query)
        scope.cancel()
        await asyncio.wait_for(waiting, timeout=1)
        assert quote.get_option_chain.call_count == 9
        service.get_option_chain("US.REPLACEMENT")
        assert quote.get_option_chain.call_count == 10
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert worker_errors == [
            "Provider request reservation already released or used"
        ]
        assert quote.get_option_chain.call_count == 10
        assert all(
            call.kwargs["code"] != "US.CANCELLED"
            for call in quote.get_option_chain.call_args_list
        )
    finally:
        release.set()
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.wait_for(finished.wait(), timeout=2)


@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
async def test_cancelled_dispatched_sdk_call_remains_counted(
    governed_context, cancellation
):
    service = governed_context.market_data_service
    quote = quote_context(governed_context)
    for _ in range(9):
        service.get_option_chain("US.XYZ")
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = Event()
    response = quote.get_option_chain.return_value

    def sdk(**_kwargs):
        loop.call_soon_threadsafe(started.set)
        try:
            if not release.wait(timeout=5):
                raise AssertionError("test did not release SDK worker")
            return response
        finally:
            loop.call_soon_threadsafe(finished.set)

    quote.get_option_chain.side_effect = sdk
    scope = anyio.CancelScope()

    async def invoke():
        with scope:
            await service.get_option_chain_async("US.XYZ")

    request = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        if cancellation == "asyncio":
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            scope.cancel()
            await asyncio.wait_for(request, timeout=1)
        with pytest.raises(ProviderRateLimitError):
            service.get_option_chain("US.XYZ")
        assert quote.get_option_chain.call_count == 10
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        await asyncio.gather(request, return_exceptions=True)
