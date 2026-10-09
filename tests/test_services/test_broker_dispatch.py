"""Broker policy boundaries using actual SDK starts and controlled time."""

import ast
import asyncio
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import anyio.to_thread
import pytest

from moomoo_mcp.services.broker_dispatch import (
    OPERATION_POLICIES,
    UNTIMED_OPERATIONS,
    BrokerRequestDispatcher,
    QuotaRequest,
)
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from tests.rate_limit_clock import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def dispatcher(clock):
    return BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)


@pytest.mark.parametrize("operation", OPERATION_POLICIES)
def test_each_published_limit_bounds_actual_starts(operation, dispatcher, clock):
    policy = OPERATION_POLICIES[operation]
    kwargs = {"acc_id": 123, "refresh_cache": True}
    sdk = Mock()
    for _ in range(policy.calls):
        dispatcher.call(operation, sdk, **kwargs)
        clock.advance(policy.min_interval_seconds + 0.002)
    with pytest.raises(ProviderRateLimitError):
        dispatcher.call(operation, sdk, **kwargs)
    assert sdk.call_count == policy.calls
    clock.advance(30.102)
    dispatcher.call(operation, sdk, **kwargs)
    assert sdk.call_count == policy.calls + 1


def test_scopes_and_independent_operations(dispatcher):
    sdk = Mock()
    for _ in range(10):
        dispatcher.call("order_list_query", sdk, acc_id=123, refresh_cache=True)
    with pytest.raises(ProviderRateLimitError):
        dispatcher.call("order_list_query", sdk, acc_id=123, refresh_cache=True)
    dispatcher.call("order_list_query", sdk, acc_id=456, refresh_cache=True)
    dispatcher.call("accinfo_query", sdk, acc_id=123, refresh_cache=True)
    dispatcher.call("history_order_list_query", sdk, acc_id=123)
    for _ in range(10):
        dispatcher.call("unlock_trade", sdk, acc_id=123)
    with pytest.raises(ProviderRateLimitError):
        dispatcher.call("unlock_trade", sdk, acc_id=456)
    dispatcher.call("get_market_state", sdk)


@pytest.mark.parametrize(
    ("first", "second", "calls", "spacing"),
    [
        ("place_order", "place_combo_order", 15, 0.022),
        ("acctradinginfo_query", "comboorder_tradinginfo_query", 10, 0),
    ],
)
def test_explicit_and_conservative_shared_groups(
    first, second, calls, spacing, dispatcher, clock
):
    sdk = Mock()
    for index in range(calls):
        dispatcher.call(first if index % 2 else second, sdk, acc_id=123)
        clock.advance(spacing)
    for operation in (first, second):
        with pytest.raises(ProviderRateLimitError):
            dispatcher.call(operation, sdk, acc_id=123)
    dispatcher.call(second, sdk, acc_id=456)


@pytest.mark.parametrize(
    "operation",
    ["accinfo_query", "position_list_query", "order_list_query", "deal_list_query"],
)
def test_cached_reads_do_not_reserve_or_charge(operation, dispatcher):
    sdk = Mock()
    held = dispatcher.limiter(QuotaRequest(operation, 123)).reserve(10)
    try:
        for _ in range(20):
            dispatcher.call(operation, sdk, acc_id=123, refresh_cache=False)
        with pytest.raises(ProviderRateLimitError):
            dispatcher.call(operation, sdk, acc_id=123, refresh_cache=True)
    finally:
        held.release()


def test_continuations_and_untimed_operations_bypass_full_budget(dispatcher):
    sdk = Mock()
    held = dispatcher.limiter(QuotaRequest("request_history_kline")).reserve(60)
    try:
        for _ in range(65):
            dispatcher.call("request_history_kline", sdk, page_req_key=b"continuation")
        with pytest.raises(ProviderRateLimitError):
            dispatcher.call("request_history_kline", sdk, page_req_key=None)
        with pytest.raises(ProviderRateLimitError):
            dispatcher.call("request_history_kline", sdk, page_req_key=b"")
    finally:
        held.release()
    for operation in UNTIMED_OPERATIONS:
        dispatcher.call(operation, sdk)


def test_weighted_relock_reservation_survives_other_callers(dispatcher):
    sdk = Mock()
    for _ in range(8):
        dispatcher.call("unlock_trade", sdk, is_unlock=False)
    with dispatcher.reserve([QuotaRequest("unlock_trade", weight=2)]):
        dispatcher.call("unlock_trade", sdk, is_unlock=True)
        with pytest.raises(ProviderRateLimitError):
            dispatcher.limiter(QuotaRequest("unlock_trade")).reserve()
        dispatcher.call("unlock_trade", sdk, is_unlock=False)
    with pytest.raises(ProviderRateLimitError):
        dispatcher.call("unlock_trade", sdk, is_unlock=True)
    assert sdk.call_count == 10


@pytest.mark.parametrize(
    ("operation", "minimum"), [("place_order", 0.02), ("modify_order", 0.04)]
)
def test_pacing_applies_to_delayed_pending_workers(
    operation, minimum, dispatcher, clock
):
    limiter = dispatcher.limiter(QuotaRequest(operation, 123))
    pending = limiter.reserve()
    clock.advance(100)
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    pending.start()
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    clock.advance(minimum - 0.001)
    with pytest.raises(ProviderRateLimitError):
        limiter.reserve()
    clock.advance(0.003)
    limiter.reserve().start()


async def test_async_waiters_are_fifo_and_cancelled_waiters_do_not_block(
    dispatcher, clock
):
    limiter = dispatcher.limiter(QuotaRequest("get_market_state"))
    held = [limiter.reserve() for _ in range(10)]
    first = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers()
    second = asyncio.create_task(limiter.acquire())
    await clock.wait_for_sleepers(2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    held[0].release()
    clock.advance(0.05)
    admitted = await asyncio.wait_for(second, 2)
    admitted.start()
    for slot in held:
        slot.release()


async def test_cancelled_active_worker_keeps_reserved_relock_capacity(
    dispatcher, clock
):
    entered, release, finished = Event(), Event(), Event()
    sdk = Mock()
    for _ in range(8):
        dispatcher.call("unlock_trade", sdk, is_unlock=False)

    def worker():
        with dispatcher.protect_mutation([QuotaRequest("unlock_trade", weight=2)]):
            try:
                dispatcher.call("unlock_trade", sdk, is_unlock=True)
                entered.set()
                assert release.wait(3)
                dispatcher.call("unlock_trade", sdk, is_unlock=False)
            finally:
                finished.set()

    task = asyncio.create_task(
        dispatcher.run_async([QuotaRequest("unlock_trade", weight=2)], worker)
    )
    try:
        assert await anyio.to_thread.run_sync(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProviderRateLimitError):
            dispatcher.call("unlock_trade", sdk, is_unlock=True)
    finally:
        release.set()
        assert await anyio.to_thread.run_sync(finished.wait, 2)
    assert sdk.call_count == 10
    assert sdk.call_args.kwargs == {"is_unlock": False}
    clock.advance(30.102)
    dispatcher.call("unlock_trade", sdk, is_unlock=False)


def test_runtime_sdk_calls_have_registered_dispatch_gates():
    registered = set(OPERATION_POLICIES) | set(UNTIMED_OPERATIONS)
    observed = set()
    services = Path(__file__).resolve().parents[2] / "src" / "moomoo_mcp" / "services"
    for path in services.glob("*.py"):
        tree = ast.parse(path.read_text())
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            # Service wrappers can have the same name: inspect only SDK contexts.
            owner = ast.unparse(node.value)
            if not (owner.endswith("ctx") or owner.endswith("_ctx")):
                continue
            parent = parents[node]
            if not isinstance(parent, ast.Call) or node.attr in {
                "close",
                "set_sync_query_connect_timeout",
            }:
                continue
            assert node.attr in registered, (path, node.lineno, node.attr)
            observed.add(node.attr)
            if node.attr == "get_option_chain":
                assert isinstance(parent, ast.Call) and parent.func is node
                continue  # Existing explicit reservation immediately precedes it.
            assert isinstance(parent, ast.Call), (path, node.lineno)
            assert (
                isinstance(parent.func, ast.Attribute) and parent.func.attr == "call"
            ), (path, node.lineno)
            assert (
                isinstance(parent.args[0], ast.Constant)
                and parent.args[0].value == node.attr
            )
    assert observed == registered


async def test_cancellation_during_validation_prevents_later_dispatch(dispatcher):
    entered, release, finished = Event(), Event(), Event()
    sdk = Mock()

    def worker():
        try:
            entered.set()
            assert release.wait(3)
            dispatcher.call("get_market_state", sdk)
        finally:
            finished.set()

    task = asyncio.create_task(
        dispatcher.run_async([QuotaRequest("get_market_state")], worker)
    )
    try:
        assert await anyio.to_thread.run_sync(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await anyio.to_thread.run_sync(finished.wait, 2)
    sdk.assert_not_called()
    # Cancelled validation did not charge its unused slot.
    dispatcher.limiter(QuotaRequest("get_market_state")).reserve(10).release()
