"""Rolling admission, pending dispatch and deadlines with a fake clock."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from moomoo_mcp.services.market_data_service import OPTION_CHAIN_RATE_LIMIT
from moomoo_mcp.services.rate_limit import (
    ProviderRateLimitError,
    ProviderRequestLimiter,
    RateLimitPolicy,
)
from tests.rate_limit_clock import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    return ProviderRequestLimiter(
        OPTION_CHAIN_RATE_LIMIT, clock=clock, sleep=clock.sleep
    )


def fill(limiter):
    for _ in range(limiter.policy.calls):
        limiter.reserve().start()


def test_ten_dispatches_and_safety_margin(limiter, clock):
    fill(limiter)
    with pytest.raises(ProviderRateLimitError) as exc:
        limiter.reserve()
    assert exc.value.retry_after_seconds == 31
    assert exc.value.provider == "Moomoo"
    assert exc.value.operation == "get_option_chain"
    assert "retry_after_seconds=31" in str(exc.value)

    clock.advance(30)
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    clock.advance(0.1)
    limiter.reserve().start()


def test_rolling_window_expires_entries_individually(clock):
    limiter = ProviderRequestLimiter(
        RateLimitPolicy("test", "query", 2, 30, safety_margin_seconds=0),
        clock=clock,
    )
    limiter.reserve().start()
    clock.advance(10)
    limiter.reserve().start()
    clock.advance(20)
    limiter.reserve().start()
    with pytest.raises(ProviderRateLimitError) as exc:
        limiter.reserve()
    assert exc.value.retry_after_seconds == 10


def test_pending_reservations_do_not_expire(limiter, clock):
    pending = [limiter.reserve() for _ in range(10)]
    clock.advance(100)
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    for slot in pending:
        slot.start()
        slot.release()  # A started request cannot be refunded.
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    clock.advance(30.1)
    limiter.reserve()


def test_release_reuses_capacity_and_prevents_late_dispatch(limiter):
    pending = [limiter.reserve() for _ in range(10)]
    pending[0].release()
    pending[0].release()
    limiter.reserve()
    with pytest.raises(RuntimeError) as exc:
        pending[0].start()
    assert "already released or used" in str(exc.value)
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()


def test_simultaneous_threads_share_one_budget(limiter):
    def reserve(_index):
        try:
            limiter.reserve().start()
            return True
        except ProviderRateLimitError:
            return False

    with ThreadPoolExecutor(max_workers=20) as workers:
        assert sum(workers.map(reserve, range(40))) == 10


async def test_async_timeout_is_bounded_with_retry_after(limiter, clock):
    fill(limiter)
    waiting = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    clock.advance(5)
    with pytest.raises(ProviderRateLimitError) as exc:
        await waiting
    assert clock.now == 5
    assert exc.value.retry_after_seconds == 26


async def test_expiry_within_deadline_admits_waiter(limiter, clock):
    fill(limiter)
    clock.advance(28)
    waiting = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    clock.advance(2.1)
    (await waiting).start()


async def test_released_capacity_admits_waiter(limiter, clock):
    slots = [limiter.reserve() for _ in range(10)]
    waiting = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    slots[0].release()
    clock.advance(0.05)
    (await waiting).start()


async def test_cancelled_wait_does_not_take_a_slot(limiter, clock):
    slots = [limiter.reserve() for _ in range(10)]
    waiting = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    slots[0].release()
    limiter.reserve().start()


async def test_delayed_scheduler_cannot_admit_after_deadline(limiter, clock):
    fill(limiter)
    waiting = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    clock.advance(100)
    with pytest.raises(ProviderRateLimitError):
        await waiting
    limiter.reserve().start()


async def test_zero_wait_policy_attempts_immediate_admission():
    # Advancing on every clock read models real monotonic time without a sleep.
    ticks = iter([0.0, 1.0, 2.0])
    limiter = ProviderRequestLimiter(
        RateLimitPolicy("test", "query", 1, 30, max_wait_seconds=0),
        clock=lambda: next(ticks),
    )
    (await limiter.acquire()).start()
    with pytest.raises(ProviderRateLimitError):
        await limiter.acquire()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"calls": 0},
        {"period_seconds": 0},
        {"period_seconds": float("inf")},
        {"safety_margin_seconds": -1},
        {"max_wait_seconds": float("nan")},
    ],
)
def test_invalid_policies_are_rejected(kwargs):
    values = {
        "provider": "test",
        "operation": "query",
        "calls": 10,
        "period_seconds": 30,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        RateLimitPolicy(**values)
