"""Read deadlines bound awaits and actual workers, never protected writes."""

import asyncio
from threading import Event
from unittest.mock import Mock, patch

import anyio
import pytest

from moomoo_mcp.services.broker_dispatch import (
    BrokerReadOverloadError,
    BrokerReadTimeoutError,
    BrokerRequestDispatcher,
    QuotaRequest,
)
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from tests.rate_limit_clock import FakeClock


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


@pytest.fixture
def bounded():
    clock = FakeClock()
    dispatcher = BrokerRequestDispatcher(
        clock=clock,
        sleep=clock.sleep,
        deadline_sleep=clock.sleep,
        read_timeout_seconds=5,
        max_wait_seconds=10,
        max_inflight_reads=2,
    )
    return dispatcher, clock


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_read_deadline_must_be_finite_and_positive(value):
    with pytest.raises(ValueError):
        BrokerRequestDispatcher(read_timeout_seconds=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_read_worker_limit_must_be_a_positive_integer(value):
    with pytest.raises(ValueError):
        BrokerRequestDispatcher(max_inflight_reads=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_pending_read_limit_must_be_a_positive_integer(value):
    with pytest.raises(ValueError):
        BrokerRequestDispatcher(max_pending_reads=value)


async def test_active_and_pending_saturation_rejects_without_timer_or_worker(bounded):
    dispatcher, clock = bounded
    dispatcher.max_pending_reads = 3
    entered = [Event(), Event()]
    release = Event()
    queued_work = Mock(return_value="queued result")
    rejected_work = Mock()

    def sdk(index):
        entered[index].set()
        assert release.wait(5)
        return index

    async def active(index):
        async with dispatcher.read_deadline("active"):
            return await dispatcher.run_async(
                [], dispatcher.call, "get_market_state", sdk, index
            )

    async def queued():
        async with dispatcher.read_deadline("queued"):
            return await dispatcher.run_async([], queued_work)

    tasks = [asyncio.create_task(active(index)) for index in range(2)]
    try:
        await wait_until(lambda: all(event.is_set() for event in entered))
        tasks.append(asyncio.create_task(queued()))
        await wait_until(lambda: dispatcher.read_requests_pending == 3)
        assert dispatcher.read_workers_in_flight == 2
        with (
            patch("moomoo_mcp.services.broker_dispatch.asyncio.create_task") as timer,
            pytest.raises(BrokerReadOverloadError) as failure,
        ):
            async with dispatcher.read_deadline("excess"):
                await dispatcher.run_async([], rejected_work)
        timer.assert_not_called()
        rejected_work.assert_not_called()
        assert failure.value.operation == "excess"
        assert failure.value.pending_reads == failure.value.max_pending_reads == 3
        assert "before admission or SDK dispatch" in str(failure.value)
        assert clock() == 0
        assert dispatcher.read_requests_pending == 3
        assert dispatcher.read_workers_in_flight == 2
        release.set()
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
        clock.advance(0.05)
        assert await asyncio.wait_for(asyncio.gather(*tasks), 2) == [
            0,
            1,
            "queued result",
        ]
        assert dispatcher.read_requests_pending == 0
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)


@pytest.mark.parametrize("stop", ["completion", "deadline", "asyncio", "anyio"])
async def test_pending_slot_cleanup_is_independent_of_abandoned_worker(bounded, stop):
    dispatcher, clock = bounded
    dispatcher.max_pending_reads = 1
    entered, release = Event(), Event()
    scope = anyio.CancelScope()

    def sdk():
        entered.set()
        assert release.wait(5)
        return "completed"

    async def read():
        with scope:
            async with dispatcher.read_deadline("first"):
                return await dispatcher.run_async(
                    [], dispatcher.call, "get_market_state", sdk
                )

    task = asyncio.create_task(read())
    try:
        await wait_until(entered.is_set)
        assert dispatcher.read_requests_pending == 1
        if stop == "completion":
            release.set()
            assert await asyncio.wait_for(task, 2) == "completed"
        elif stop == "deadline":
            await clock.wait_for_sleepers()
            clock.advance(5)
            with pytest.raises(BrokerReadTimeoutError):
                await asyncio.wait_for(task, 2)
        elif stop == "asyncio":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            scope.cancel()
            await asyncio.wait_for(task, 2)
        assert dispatcher.read_requests_pending == 0
        assert dispatcher.read_workers_in_flight == int(stop != "completion")
        async with dispatcher.read_deadline("replacement"):
            assert dispatcher.read_requests_pending == 1
            assert await dispatcher.run_async([], lambda: "new") == "new"
        assert dispatcher.read_requests_pending == 0
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)


async def test_quota_waits_also_occupy_pending_budget_and_cancel_releases_it(bounded):
    dispatcher, clock = bounded
    dispatcher.max_pending_reads = 1
    request = QuotaRequest("get_market_state")
    held = dispatcher.limiter(request).reserve(10)
    sdk = Mock()

    async def read():
        async with dispatcher.read_deadline("quota"):
            await dispatcher.run_async([request], sdk)

    task = asyncio.create_task(read())
    try:
        await clock.wait_for_sleepers(2)
        assert dispatcher.read_requests_pending == 1
        assert dispatcher.read_workers_in_flight == 0
        with pytest.raises(BrokerReadOverloadError):
            async with dispatcher.read_deadline("excess"):
                pytest.fail("overloaded request entered its body")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert dispatcher.read_requests_pending == 0
        async with dispatcher.read_deadline("replacement"):
            assert await dispatcher.run_async([], lambda: "available") == "available"
        sdk.assert_not_called()
    finally:
        held.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("overall_first", [True, False])
async def test_quota_deadline_and_overall_deadline_are_distinct(bounded, overall_first):
    dispatcher, clock = bounded
    dispatcher.max_wait_seconds = 10 if overall_first else 2
    request = QuotaRequest("get_market_state")
    held = dispatcher.limiter(request).reserve(10)
    sdk = Mock()

    async def read():
        async with dispatcher.read_deadline("state"):
            await dispatcher.run_async(
                [request], dispatcher.call, request.operation, sdk
            )

    task = asyncio.create_task(read())
    try:
        await clock.wait_for_sleepers(2)
        clock.advance(5 if overall_first else 2)
        with pytest.raises(
            BrokerReadTimeoutError if overall_first else ProviderRateLimitError
        ) as failure:
            await asyncio.wait_for(task, 2)
        if overall_first:
            assert isinstance(failure.value, BrokerReadTimeoutError)
            assert failure.value.phase == "quota"
            assert failure.value.timing["quota_wait_seconds"] == 5
            assert failure.value.timing["sdk_calls_started"] == 0
        sdk.assert_not_called()
        assert dispatcher.read_workers_in_flight == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        held.release()


async def test_cancellation_before_dispatch_returns_partial_bundle(bounded):
    dispatcher, clock = bounded
    first = QuotaRequest("get_market_snapshot")
    second = QuotaRequest("get_market_state")
    held = dispatcher.limiter(second).reserve(10)
    sdk = Mock()

    async def read():
        async with dispatcher.read_deadline("bundle"):
            await dispatcher.run_async([first, second], sdk)

    task = asyncio.create_task(read())
    try:
        await clock.wait_for_sleepers(2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        sdk.assert_not_called()
        dispatcher.limiter(first).reserve(60).release()
        assert dispatcher.read_workers_in_flight == 0
    finally:
        held.release()


@pytest.mark.parametrize("stop", ["deadline", "asyncio", "anyio"])
@pytest.mark.parametrize("late_failure", [False, True])
async def test_dispatched_read_is_abandoned_not_aborted_and_late_work_is_ignored(
    bounded, stop, late_failure
):
    dispatcher, clock = bounded
    entered, release = Event(), Event()
    later = Mock()
    published = []
    scope = anyio.CancelScope()
    request = QuotaRequest("get_market_state", weight=2)

    def slow_sdk():
        entered.set()
        assert release.wait(5)
        if late_failure:
            raise OSError("late provider failure")
        return "late data"

    def worker():
        result = dispatcher.call("get_market_state", slow_sdk)
        published.append(result)
        dispatcher.call("get_market_state", later)

    async def read():
        with scope:
            async with dispatcher.read_deadline("state"):
                await dispatcher.run_async([request], worker)

    task = asyncio.create_task(read())
    try:
        await wait_until(entered.is_set)
        if stop == "deadline":
            await clock.wait_for_sleepers()
            clock.advance(5)
            with pytest.raises(BrokerReadTimeoutError) as failure:
                await asyncio.wait_for(task, 2)
            assert failure.value.phase == "sdk"
            assert failure.value.timing["sdk_seconds"] == 5
            assert failure.value.timing["total_seconds"] == 5
            assert failure.value.timing["sdk_calls_started"] == 1
            assert failure.value.timing["sdk_calls_completed"] == 0
            assert failure.value.timing["sdk_in_flight"] is True
            assert "background" in str(failure.value)
            assert "not aborted" in str(failure.value)
        elif stop == "asyncio":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            scope.cancel()
            await asyncio.wait_for(task, 2)
        assert dispatcher.read_workers_in_flight == 1
        # Only the SDK attempt that really started remains charged.
        dispatcher.limiter(request).reserve(9).release()
    finally:
        release.set()
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert published == []
    later.assert_not_called()
    # A later caller gets its own data, never the abandoned result or exception.
    async with dispatcher.read_deadline("replacement"):
        assert await dispatcher.run_async([], lambda: "new data") == "new data"


async def test_abandoned_workers_retain_their_slots_and_queue_has_a_deadline(bounded):
    dispatcher, clock = bounded
    release = Event()
    entered = [Event(), Event()]

    def sdk(index):
        entered[index].set()
        assert release.wait(5)

    async def read(index):
        async with dispatcher.read_deadline("slow"):
            await dispatcher.run_async([], sdk, index)

    tasks = [asyncio.create_task(read(index)) for index in range(2)]
    queued = None
    later = Mock()
    try:
        await wait_until(lambda: all(event.is_set() for event in entered))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert dispatcher.read_workers_in_flight == 2

        async def another():
            async with dispatcher.read_deadline("queued"):
                await dispatcher.run_async([], later)

        queued = asyncio.create_task(another())
        await clock.wait_for_sleepers(2)
        clock.advance(5)
        with pytest.raises(BrokerReadTimeoutError) as failure:
            await asyncio.wait_for(queued, 2)
        assert failure.value.phase == "worker_queue"
        assert failure.value.timing["worker_queue_seconds"] == 5
        assert failure.value.timing["sdk_calls_started"] == 0
        assert dispatcher.read_workers_in_flight == 2
        later.assert_not_called()
    finally:
        release.set()
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
        if queued is not None:
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)


async def test_actual_wall_clock_deadline_does_not_wait_for_sdk_exit():
    dispatcher = BrokerRequestDispatcher(read_timeout_seconds=0.05)
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(5)

    async def read():
        async with dispatcher.read_deadline("real clock"):
            await dispatcher.run_async([], dispatcher.call, "get_market_state", slow)

    task = asyncio.create_task(read())
    try:
        await wait_until(entered.is_set)
        with pytest.raises(BrokerReadTimeoutError):
            await asyncio.wait_for(task, 2)
        assert not release.is_set()
        assert dispatcher.read_workers_in_flight == 1
    finally:
        release.set()
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
