"""End-to-end read timing includes admission; slow reads cannot starve health."""

import asyncio
from threading import Event
from unittest.mock import MagicMock

import pandas as pd
import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

from moomoo_mcp.server import mcp
from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.broker_dispatch import (
    BrokerReadOverloadError,
    BrokerReadTimeoutError,
    BrokerRequestDispatcher,
    QuotaRequest,
)
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from moomoo_mcp.tools.offload import run_blocking
from tests.mcp_helpers import call_mcp_tool
from tests.rate_limit_clock import FakeClock
from tests.trade_helpers import real_request


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


@pytest.fixture
def reads():
    clock = FakeClock()
    dispatcher = BrokerRequestDispatcher(
        clock=clock,
        sleep=clock.sleep,
        deadline_sleep=clock.sleep,
        read_timeout_seconds=10,
        max_wait_seconds=10,
        max_inflight_reads=1,
    )
    quote = MagicMock()
    quote.get_market_snapshot.return_value = (0, pd.DataFrame([{"code": "US.TEST"}]))
    quote.get_option_chain.return_value = (0, pd.DataFrame([{"code": "US.TEST"}]))
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame([{"code": "US.TEST", "stock_type": "STOCK"}]),
    )
    return MarketDataService(quote, dispatcher=dispatcher), quote, clock


@pytest.mark.parametrize("expires", [False, True])
async def test_one_budget_covers_admission_quota_and_sdk(reads, monkeypatch, expires):
    service, quote, clock = reads
    dispatcher = service.dispatcher
    dispatcher.read_timeout_seconds = 6 if expires else 10
    entered, release = Event(), Event()
    original = MarketDataService.prepare_admission

    def prepare(owner, method, params):
        clock.advance(1)
        return original(owner, method, params)

    def sdk(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame([{"code": "US.TEST"}])

    monkeypatch.setattr(MarketDataService, "prepare_admission", prepare)
    quote.get_market_snapshot.side_effect = sdk
    held = dispatcher.limiter(QuotaRequest("get_market_snapshot")).reserve(60)
    task = asyncio.create_task(
        run_blocking(service.get_market_snapshot_evidence, ["US.TEST"])
    )
    try:
        await clock.wait_for_sleepers(2)
        assert clock() == 1
        held.release()
        clock.advance(2)
        await wait_until(entered.is_set)
        clock.advance(3)
        if expires:
            with pytest.raises(BrokerReadTimeoutError) as failure:
                await asyncio.wait_for(task, 2)
            timing = failure.value.timing
            assert timing["sdk_in_flight"] is True
        else:
            release.set()
            result = await asyncio.wait_for(task, 2)
            assert result["data"] == [{"code": "US.TEST"}]
            timing = result["timing"]
            assert timing["sdk_in_flight"] is False
            assert timing["sdk_calls_completed"] == 1
        assert timing["admission_seconds"] == 1
        assert timing["quota_wait_seconds"] == 2
        assert timing["sdk_seconds"] == 3
        assert timing["total_seconds"] == 6
        assert timing["sdk_calls_started"] == 1
        quote.get_market_snapshot.assert_called_once()
    finally:
        held.release()
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)


async def test_admission_account_discovery_is_inside_read_deadline(
    reads, trade_service_factory
):
    service, _quote, clock = reads
    dispatcher = service.dispatcher
    trade = trade_service_factory(dispatcher=dispatcher, policy=TradingPolicy())
    context = MagicMock()
    entered, release = Event(), Event()

    def accounts():
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame([{"acc_id": 123, "trd_env": "REAL"}])

    context.get_acc_list.side_effect = accounts
    trade.trade_ctx = context
    task = asyncio.create_task(run_blocking(trade.get_assets, trd_env="REAL"))
    try:
        await wait_until(entered.is_set)
        await clock.wait_for_sleepers()
        clock.advance(10)
        with pytest.raises(BrokerReadTimeoutError) as failure:
            await asyncio.wait_for(task, 2)
        assert failure.value.timing["admission_seconds"] == 10
        assert failure.value.timing["sdk_seconds"] == 10
        assert dispatcher.read_workers_in_flight == 1
        context.accinfo_query.assert_not_called()
    finally:
        release.set()
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
    context.accinfo_query.assert_not_called()


async def test_abandoned_reference_read_cannot_update_shared_metadata_cache(reads):
    service, quote, clock = reads
    entered, release = Event(), Event()

    def reference(**_kwargs):
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame([{"code": "US.TEST", "stock_type": "STOCK"}])

    quote.get_stock_basicinfo.side_effect = reference
    task = asyncio.create_task(run_blocking(service.get_instrument_facts, ["US.TEST"]))
    try:
        await wait_until(entered.is_set)
        await clock.wait_for_sleepers()
        clock.advance(10)
        with pytest.raises(BrokerReadTimeoutError):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await wait_until(lambda: service.dispatcher.read_workers_in_flight == 0)
    assert not service.instrument_adapter._metadata
    quote.get_stock_basicinfo.side_effect = None
    await run_blocking(service.get_instrument_facts, ["US.TEST"])
    assert quote.get_stock_basicinfo.call_count == 2


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
async def test_abandoned_chain_processing_cannot_publish_shared_reference_facts(
    reads, monkeypatch, stop
):
    from moomoo_mcp.services import market_data_service

    service, _quote, clock = reads
    entered, release = Event(), Event()
    original = market_data_service.bounded_rows

    def processing(rows):
        entered.set()
        assert release.wait(5)
        return original(rows)

    monkeypatch.setattr(market_data_service, "bounded_rows", processing)
    task = asyncio.create_task(service.get_option_chain_async("US.TEST"))
    try:
        await wait_until(entered.is_set)
        if stop == "timeout":
            await clock.wait_for_sleepers()
            clock.advance(10)
            with pytest.raises(BrokerReadTimeoutError):
                await asyncio.wait_for(task, 2)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert service.dispatcher.read_workers_in_flight == 1
        assert service.dispatcher.read_requests_pending == 0
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_until(lambda: service.dispatcher.read_workers_in_flight == 0)
    assert not service.instrument_adapter._chain_metadata


async def test_chain_timeout_crosses_mcp_with_explicit_background_semantics(
    reads, mcp_app_context
):
    service, quote, clock = reads
    mcp_app_context.market_data_service = service
    entered, release = Event(), Event()

    def chain(**_kwargs):
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame()

    quote.get_option_chain.side_effect = chain

    async def invoke():
        token = request_ctx.set(
            RequestContext(
                request_id="deadline-test",
                meta=None,
                session=MagicMock(),
                lifespan_context=mcp_app_context,
            )
        )
        try:
            return await mcp._call_tool_with_errors(
                "get_option_chain", {"code": "US.TEST"}
            )
        finally:
            request_ctx.reset(token)

    task = asyncio.create_task(invoke())
    try:
        await wait_until(entered.is_set)
        await clock.wait_for_sleepers()
        clock.advance(10)
        result = await asyncio.wait_for(task, 2)
        assert result.isError is True
        diagnostic = result.structuredContent
        assert diagnostic["error_code"] == "READ_DEADLINE_EXCEEDED"
        assert diagnostic["phase"] == "sdk"
        assert diagnostic["underlying_sdk_cancelled"] is False
        assert diagnostic["timing"]["sdk_in_flight"] is True
        assert "background" in diagnostic["message"]
        assert diagnostic["retryable"] is False
        assert service.dispatcher.read_workers_in_flight == 1
    finally:
        release.set()
        await wait_until(lambda: service.dispatcher.read_workers_in_flight == 0)


async def test_chain_success_timing_is_additive_and_legacy_rows_are_unchanged(
    reads, mcp_app_context
):
    service, quote, clock = reads
    mcp_app_context.market_data_service = service

    def chain(**_kwargs):
        clock.advance(2)
        return 0, pd.DataFrame([{"code": "US.TEST"}])

    quote.get_option_chain.side_effect = chain
    legacy = await call_mcp_tool(
        mcp_app_context, "get_option_chain", {"code": "US.TEST"}
    )
    assert legacy.structured == {"result": [{"code": "US.TEST"}]}
    observed = await call_mcp_tool(
        mcp_app_context,
        "get_option_chain",
        {"code": "US.TEST", "include_evidence": True},
    )
    result = observed.structured["result"]
    assert result["data"] == [{"code": "US.TEST"}]
    assert result["timing"]["sdk_seconds"] == 2
    assert result["timing"]["total_seconds"] == 2
    assert result["timing"]["sdk_calls_started"] == 1
    assert result["timing"]["sdk_calls_completed"] == 1
    assert result["timing"]["sdk_in_flight"] is False
    assert result["source_revision"] is None
    assert result["delay_status"] == "UNKNOWN"


@pytest.mark.parametrize("active_method", ["snapshot", "chain"])
async def test_ordinary_and_chain_reads_share_pending_budget_and_overload_diagnostic(
    reads, mcp_app_context, active_method
):
    service, quote, clock = reads
    dispatcher = service.dispatcher
    dispatcher.max_pending_reads = 2
    mcp_app_context.market_data_service = service
    entered, release = Event(), Event()

    def stuck(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame()

    quote.get_market_snapshot.side_effect = stuck
    quote.get_option_chain.side_effect = stuck

    async def invoke(method):
        if method == "snapshot":
            return await run_blocking(service.get_market_snapshot, ["US.TEST"])
        return await service.get_option_chain_async("US.TEST")

    active = asyncio.create_task(invoke(active_method))
    queued = None
    try:
        await wait_until(entered.is_set)
        queued = asyncio.create_task(
            invoke("chain" if active_method == "snapshot" else "snapshot")
        )
        await wait_until(lambda: dispatcher.read_requests_pending == 2)
        with pytest.raises(BrokerReadOverloadError):
            await run_blocking(service.get_market_snapshot_evidence, ["US.TEST"])

        token = request_ctx.set(
            RequestContext(
                request_id="overload-test",
                meta=None,
                session=MagicMock(),
                lifespan_context=mcp_app_context,
            )
        )
        try:
            result = await mcp._call_tool_with_errors(
                "get_option_chain", {"code": "US.TEST"}
            )
        finally:
            request_ctx.reset(token)
        assert result.isError is True
        diagnostic = result.structuredContent
        assert diagnostic["error_code"] == "READ_OVERLOADED"
        assert diagnostic["pending_reads"] == diagnostic["max_pending_reads"] == 2
        assert diagnostic["admission_rejected"] is True
        assert diagnostic["underlying_sdk_cancelled"] is False
        assert diagnostic["retryable"] is False
        assert "before admission or SDK dispatch" in diagnostic["message"]
        assert clock() == 0
        assert dispatcher.read_requests_pending == 2
        assert dispatcher.read_workers_in_flight == 1
        assert (
            quote.get_market_snapshot.call_count + quote.get_option_chain.call_count
        ) == 1
    finally:
        release.set()
        active.cancel()
        tasks = [active]
        if queued is not None:
            queued.cancel()
            tasks.append(queued)
        await asyncio.gather(*tasks, return_exceptions=True)
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
    assert dispatcher.read_requests_pending == 0


async def test_nested_chain_deadlines_consume_one_pending_slot(reads, mcp_app_context):
    service, quote, _clock = reads
    service.dispatcher.max_pending_reads = 1
    mcp_app_context.market_data_service = service

    def chain(**_kwargs):
        assert service.dispatcher.read_requests_pending == 1
        return 0, pd.DataFrame([{"code": "US.TEST"}])

    quote.get_option_chain.side_effect = chain
    result = await call_mcp_tool(
        mcp_app_context,
        "get_option_chain",
        {"code": "US.TEST", "include_evidence": True},
    )
    assert result.structured["result"]["timing"]["sdk_calls_started"] == 1
    assert service.dispatcher.read_requests_pending == 0
    quote.get_option_chain.assert_called_once()


async def test_hung_read_does_not_occupy_mutation_pool_or_health_workers(
    reads, mcp_app_context, trade_service_factory
):
    service, quote, _clock = reads
    service.dispatcher.max_pending_reads = 1
    release, entered = Event(), Event()

    def stuck(*_args):
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame()

    quote.get_market_snapshot.side_effect = stuck
    quote.get_global_state.return_value = (0, {"server_ver": "test"})
    base = MoomooService(dispatcher=service.dispatcher)
    base.quote_ctx = quote
    trade = trade_service_factory(
        dispatcher=service.dispatcher,
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
    )
    trade.trade_ctx = MagicMock()
    trade.trade_ctx.get_acc_list.return_value = (0, pd.DataFrame())
    trade.trade_password = "test-only"
    trade.trade_ctx.unlock_trade.return_value = (0, None)
    trade.trade_ctx.place_order.return_value = (0, pd.DataFrame([{"order_id": "77"}]))
    mcp_app_context.moomoo_service = base
    mcp_app_context.market_data_service = service
    mcp_app_context.trade_service = trade
    task = asyncio.create_task(run_blocking(service.get_market_snapshot, ["US.TEST"]))
    queued = None
    try:
        await wait_until(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.dispatcher.read_workers_in_flight == 1
        assert service.dispatcher.read_requests_pending == 0
        queued = asyncio.create_task(service.get_option_chain_async("US.TEST"))
        await wait_until(lambda: service.dispatcher.read_requests_pending == 1)
        with pytest.raises(BrokerReadOverloadError):
            await run_blocking(service.get_market_snapshot, ["US.TEST"])
        assert (
            await asyncio.wait_for(run_blocking(lambda: "available"), 2) == "available"
        )
        health = await asyncio.wait_for(
            call_mcp_tool(mcp_app_context, "check_health"), 2
        )
        assert health.structured["status"] == "connected"
        receipt = await asyncio.wait_for(
            run_blocking(
                trade.place_order,
                "US.TEST",
                1.0,
                1,
                "BUY",
                trd_env="REAL",
                acc_id="123",
                **real_request(trade, "write-under-read-saturation"),
            ),
            2,
        )
        assert receipt["state"] == "ACKNOWLEDGED"
        assert [
            call.kwargs["is_unlock"]
            for call in trade.trade_ctx.unlock_trade.call_args_list
        ] == [True, False]
        assert service.dispatcher.read_workers_in_flight == 1
        assert service.dispatcher.read_requests_pending == 1
    finally:
        release.set()
        if queued is not None:
            queued.cancel()
            await asyncio.gather(queued, return_exceptions=True)
        await wait_until(lambda: service.dispatcher.read_workers_in_flight == 0)
        base.close()
    assert service.dispatcher.read_requests_pending == 0
