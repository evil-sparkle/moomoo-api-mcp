"""Quota admission through real services and the MCP offload boundary."""

import asyncio
import inspect
from threading import Event
from unittest.mock import MagicMock

import anyio.to_thread
import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher, QuotaRequest
from moomoo_mcp.services.execution_identity import canonicalize_request
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.instruments import InstrumentAdapter
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.order_errors import OrderNotSentError, OrderOutcomeUnknownError
from moomoo_mcp.services.paper_execution import PaperExecution
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from moomoo_mcp.tools.offload import run_blocking
from tests.conftest import call_mcp_tool
from tests.rate_limit_clock import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def governed(mcp_app_context, clock):
    dispatcher = BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)
    trade = TradeService(
        dispatcher=dispatcher,
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
    )
    ctx = MagicMock()
    ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
                {"acc_id": 456, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            ]
        ),
    )
    for method in (
        "order_list_query",
        "position_list_query",
        "deal_list_query",
        "accinfo_query",
        "history_order_list_query",
        "history_deal_list_query",
        "get_margin_ratio",
        "get_acc_cash_flow",
        "acctradinginfo_query",
        "comboorder_tradinginfo_query",
        "place_order",
        "place_combo_order",
        "modify_order",
    ):
        getattr(ctx, method).return_value = (RET_OK, pd.DataFrame([{"order_id": "77"}]))
    ctx.unlock_trade.return_value = (RET_OK, None)
    trade.trade_ctx = ctx
    quote = MagicMock()
    market = MarketDataService(quote, dispatcher=dispatcher)
    for method in (
        "get_market_snapshot",
        "get_option_expiration_date",
        "get_market_state",
        "get_user_security_group",
        "get_user_security",
    ):
        getattr(quote, method).return_value = (RET_OK, pd.DataFrame())
    quote.request_trading_days.return_value = (RET_OK, [])
    quote.request_history_kline.return_value = (RET_OK, pd.DataFrame(), None)
    mcp_app_context.trade_service = trade
    mcp_app_context.market_data_service = market
    return mcp_app_context


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 3)


async def test_default_and_explicit_accounts_share_fresh_order_admission(
    governed, clock
):
    trade = governed.trade_service
    ctx = trade.trade_ctx
    assert ctx is not None
    tasks = [
        asyncio.create_task(
            call_mcp_tool(
                governed,
                "get_orders",
                {
                    "trd_env": "REAL",
                    "acc_id": "0" if index % 2 else "123",
                    "refresh_cache": True,
                },
            )
        )
        for index in range(11)
    ]
    try:
        await clock.wait_for_sleepers()
        await wait_until(lambda: sum(task.done() for task in tasks) == 10)
        assert ctx.order_list_query.call_count == 10
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        await call_mcp_tool(
            governed, "get_orders", {"trd_env": "REAL", "acc_id": "123"}
        )
        await call_mcp_tool(
            governed,
            "get_orders",
            {"trd_env": "SIMULATE", "acc_id": "456", "refresh_cache": True},
        )
        assert ctx.order_list_query.call_count == 12
        waiting = next(task for task in tasks if not task.done())
        clock.advance(5)
        with pytest.raises(Exception, match="order_list_query rate limit exceeded"):
            await waiting
        assert ctx.order_list_query.call_count == 12
        assert all(
            call.kwargs["acc_id"] in {123, 456}
            for call in ctx.order_list_query.call_args_list
        )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


READ_CASES = [
    (
        "market",
        "get_market_snapshot",
        {"codes": ["US.AAPL"]},
        "get_market_snapshot",
        None,
    ),
    (
        "market",
        "get_historical_klines",
        {"code": "US.AAPL"},
        "request_history_kline",
        None,
    ),
    (
        "market",
        "get_historical_klines_page",
        {"code": "US.AAPL"},
        "request_history_kline",
        None,
    ),
    (
        "market",
        "get_option_expiration_date",
        {"code": "US.AAPL"},
        "get_option_expiration_date",
        None,
    ),
    ("market", "get_market_state", {"codes": ["US.AAPL"]}, "get_market_state", None),
    ("market", "get_trading_days", {"market": "US"}, "request_trading_days", None),
    ("market", "get_user_security_group", {}, "get_user_security_group", None),
    (
        "market",
        "get_user_security",
        {"group_name": "Favorites"},
        "get_user_security",
        None,
    ),
    ("trade", "get_assets", {"refresh_cache": True}, "accinfo_query", 123),
    ("trade", "get_positions", {"refresh_cache": True}, "position_list_query", 123),
    ("trade", "get_orders", {"refresh_cache": True}, "order_list_query", 123),
    ("trade", "get_deals", {"refresh_cache": True}, "deal_list_query", 123),
    ("trade", "get_history_orders", {}, "history_order_list_query", 123),
    ("trade", "get_history_deals", {}, "history_deal_list_query", 123),
    (
        "trade",
        "get_max_tradable",
        {"code": "US.AAPL", "price": 1.0, "order_type": "NORMAL"},
        "acctradinginfo_query",
        123,
    ),
    ("trade", "get_cash_flow", {}, "get_acc_cash_flow", 123),
    ("trade", "get_margin_ratio", {"code_list": ["US.AAPL"]}, "get_margin_ratio", None),
    ("trade", "lock_trade", {}, "unlock_trade", None),
    ("trade", "unlock_trade", {"password": "test-only"}, "unlock_trade", None),
    (
        "trade",
        "preview_combo_order",
        {
            "combo_legs": [
                {"code": "US.AAPL261120C100000", "trd_side": "BUY", "qty_ratio": 1},
                {"code": "US.AAPL261120C110000", "trd_side": "SELL", "qty_ratio": 1},
            ],
            "price": 1.0,
            "qty": 1,
        },
        "comboorder_tradinginfo_query",
        123,
    ),
]


@pytest.mark.parametrize(
    ("method", "params", "operation"),
    [
        (
            "place_order",
            {"code": "US.AAPL", "price": 1.0, "qty": 1, "trd_side": "BUY"},
            "place_order",
        ),
        (
            "place_combo_order",
            {
                "combo_legs": [
                    {"code": "US.AAPL261120C100000", "trd_side": "BUY", "qty_ratio": 1},
                    {
                        "code": "US.AAPL261120C110000",
                        "trd_side": "SELL",
                        "qty_ratio": 1,
                    },
                ],
                "price": 1.0,
                "qty": 1,
            },
            "place_combo_order",
        ),
        (
            "modify_order",
            {"order_id": "77", "modify_order_op": "NORMAL", "qty": 2, "price": 1.0},
            "modify_order",
        ),
        ("cancel_order", {"order_id": "77"}, "modify_order"),
    ],
)
async def test_all_live_mutations_admit_before_unlock_or_internal_reads(
    method, params, operation, governed, clock
):
    trade = governed.trade_service
    trade.trade_password = "test-only"
    held = trade.dispatcher.limiter(QuotaRequest(operation, 123)).reserve()
    task = asyncio.create_task(
        run_blocking(getattr(trade, method), **params, trd_env="REAL", acc_id="123")
    )
    try:
        await clock.wait_for_sleepers()
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        clock.advance(5)
        with pytest.raises(OrderNotSentError, match="rate limit"):
            await task
        trade.trade_ctx.unlock_trade.assert_not_called()
        trade.trade_ctx.order_list_query.assert_not_called()
        trade.trade_ctx.place_order.assert_not_called()
        trade.trade_ctx.place_combo_order.assert_not_called()
        trade.trade_ctx.modify_order.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        held.release()


@pytest.mark.parametrize(
    ("kind", "method", "params", "operation", "account"), READ_CASES
)
async def test_all_governed_service_reads_wait_without_an_sdk_worker(
    kind, method, params, operation, account, governed, clock
):
    service = (
        governed.trade_service if kind == "trade" else governed.market_data_service
    )
    params = dict(params)
    if account:
        params.update(trd_env="REAL", acc_id="123")
    limiter = service.dispatcher.limiter(QuotaRequest(operation, account))
    held = limiter.reserve(limiter.policy.calls)
    task = asyncio.create_task(run_blocking(getattr(service, method), **params))
    try:
        await clock.wait_for_sleepers()
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        ctx = service.trade_ctx if kind == "trade" else service.quote_ctx
        getattr(ctx, operation).assert_not_called()
        clock.advance(5)
        with pytest.raises(ProviderRateLimitError, match="retry_after_seconds"):
            await task
        getattr(ctx, operation).assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        held.release()


async def test_empty_snapshot_and_continuation_need_no_permits(governed):
    market = governed.market_data_service
    held = market.dispatcher.limiter(QuotaRequest("get_market_snapshot")).reserve(60)
    try:
        assert await run_blocking(market.get_market_snapshot, []) == []
    finally:
        held.release()
    held = market.dispatcher.limiter(QuotaRequest("request_history_kline")).reserve(60)
    try:
        assert await run_blocking(
            market.get_historical_klines_page, "US.AAPL", page_req_key=b"next"
        ) == ([], None)
    finally:
        held.release()


async def test_invalid_filters_and_policies_fail_before_full_quota(governed):
    market, trade = governed.market_data_service, governed.trade_service
    held = market.dispatcher.limiter(QuotaRequest("request_trading_days")).reserve(30)
    try:
        with pytest.raises(ValueError, match="market must be"):
            await run_blocking(market.get_trading_days, "invalid")
    finally:
        held.release()
    trade.policy = TradingPolicy()
    with pytest.raises(OrderNotSentError, match="read-only"):
        await run_blocking(trade.place_order, "US.AAPL", 1.0, 1, "BUY", trd_env="REAL")
    trade.trade_ctx.get_acc_list.assert_not_called()
    trade.trade_ctx.unlock_trade.assert_not_called()


def test_internal_instrument_snapshot_shares_public_budget(governed):
    market = governed.market_data_service
    held = market.dispatcher.limiter(QuotaRequest("get_market_snapshot")).reserve(60)
    adapter = InstrumentAdapter(lambda: market.quote_ctx, dispatcher=market.dispatcher)
    try:
        with pytest.raises(ProviderRateLimitError):
            adapter(["US.AAPL"])
        market.quote_ctx.get_market_snapshot.assert_not_called()
        market.quote_ctx.get_stock_basicinfo.assert_not_called()
    finally:
        held.release()


@pytest.mark.parametrize("async_call", [False, True])
async def test_live_write_exhausted_unlock_capacity_is_not_sent(
    async_call, governed, clock
):
    trade = governed.trade_service
    trade.trade_password = "test-only"
    ctx = trade.trade_ctx
    for _ in range(9):
        trade.dispatcher.call("unlock_trade", ctx.unlock_trade, is_unlock=False)
    ctx.reset_mock()
    if async_call:
        task = asyncio.create_task(
            run_blocking(
                trade.place_order,
                "US.AAPL",
                1.0,
                1,
                "BUY",
                trd_env="REAL",
                acc_id="123",
            )
        )
        await clock.wait_for_sleepers()
        assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        clock.advance(5)
        with pytest.raises(OrderNotSentError, match="unlock_trade rate limit"):
            await task
    else:
        with pytest.raises(OrderNotSentError, match="unlock_trade rate limit"):
            trade.place_order("US.AAPL", 1.0, 1, "BUY", trd_env="REAL", acc_id="123")
    ctx.place_order.assert_not_called()
    ctx.unlock_trade.assert_not_called()
    # A failed bundle returns the unused placement permit.
    trade.dispatcher.limiter(QuotaRequest("place_order", 123)).reserve().release()


async def test_provider_write_error_is_unknown_and_never_replayed(governed):
    trade = governed.trade_service
    trade.trade_password = "test-only"
    trade.trade_ctx.place_order.return_value = (RET_ERROR, "response lost")
    with pytest.raises(OrderOutcomeUnknownError):
        await run_blocking(
            trade.place_order, "US.AAPL", 1.0, 1, "BUY", trd_env="REAL", acc_id="123"
        )
    trade.trade_ctx.place_order.assert_called_once()
    assert [
        call.kwargs["is_unlock"] for call in trade.trade_ctx.unlock_trade.call_args_list
    ] == [True, False]
    with pytest.raises(ProviderRateLimitError):
        trade.dispatcher.limiter(QuotaRequest("place_order", 123)).reserve()


async def test_cancelled_live_worker_still_relocks(governed):
    trade = governed.trade_service
    trade.trade_password = "test-only"
    entered, release, finished = Event(), Event(), Event()
    for _ in range(8):
        trade.dispatcher.call(
            "unlock_trade", trade.trade_ctx.unlock_trade, is_unlock=False
        )
    trade.trade_ctx.reset_mock()

    def place(**_kwargs):
        entered.set()
        assert release.wait(3)
        return RET_OK, pd.DataFrame([{"order_id": "77"}])

    def lock(**kwargs):
        if kwargs["is_unlock"] is False:
            finished.set()
        return RET_OK, None

    trade.trade_ctx.place_order.side_effect = place
    trade.trade_ctx.unlock_trade.side_effect = lock
    task = asyncio.create_task(
        run_blocking(
            trade.place_order, "US.AAPL", 1.0, 1, "BUY", trd_env="REAL", acc_id="123"
        )
    )
    try:
        await wait_until(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProviderRateLimitError):
            trade.dispatcher.call(
                "unlock_trade", trade.trade_ctx.unlock_trade, is_unlock=False
            )
    finally:
        release.set()
        await wait_until(finished.is_set)
    trade.trade_ctx.place_order.assert_called_once()
    assert [
        call.kwargs["is_unlock"] for call in trade.trade_ctx.unlock_trade.call_args_list
    ] == [True, False]


async def test_worker_congestion_and_cancellation_preserve_mutation_spacing(clock):
    dispatcher = BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)
    workers = anyio.to_thread.current_default_thread_limiter()
    previous = workers.total_tokens
    entered, release = Event(), Event()
    starts = []

    def block():
        entered.set()
        assert release.wait(5)

    def sdk(**_kwargs):
        starts.append(clock())

    def place():
        dispatcher.call("place_order", sdk, acc_id=123)

    workers.total_tokens = 1
    blocker = asyncio.create_task(anyio.to_thread.run_sync(block))
    tasks = []
    try:
        await wait_until(entered.is_set)
        first = asyncio.create_task(
            dispatcher.run_async([QuotaRequest("place_order", 123)], place)
        )
        tasks.append(first)
        await wait_until(lambda: workers.statistics().tasks_waiting == 1)
        clock.advance(100)
        second = asyncio.create_task(
            dispatcher.run_async([QuotaRequest("place_combo_order", 123)], place)
        )
        tasks.append(second)
        await clock.wait_for_sleepers()
        assert starts == []
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        clock.advance(0.05)
        await wait_until(lambda: workers.statistics().tasks_waiting == 1)
        release.set()
        await blocker
        await second
        third = asyncio.create_task(
            dispatcher.run_async([QuotaRequest("place_order", 123)], place)
        )
        tasks.append(third)
        await clock.wait_for_sleepers()
        clock.advance(0.019)
        await asyncio.sleep(0)
        assert len(starts) == 1
        clock.advance(0.003)
        await third
        assert len(starts) == 2 and starts[1] - starts[0] >= 0.02
    finally:
        release.set()
        workers.total_tokens = previous
        for task in tasks:
            task.cancel()
        await asyncio.gather(blocker, *tasks, return_exceptions=True)


@pytest.mark.parametrize("recovery_path", ["manual", "background"])
async def test_paper_recovery_uses_public_history_budget(
    governed, tmp_path, clock, recovery_path
):
    trade = governed.trade_service
    trade.policy = TradingPolicy(TradingMode.SIMULATE)
    trade.trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            ]
        ),
    )
    quote = governed.market_data_service.quote_ctx
    quote.get_market_snapshot.return_value = (
        RET_OK,
        pd.DataFrame([{"code": "US.AAPL", "last_price": 1.0}]),
    )
    quote.get_stock_basicinfo.return_value = (
        RET_OK,
        pd.DataFrame([{"code": "US.AAPL", "stock_type": "STOCK"}]),
    )
    trade.instrument_lookup = InstrumentAdapter(
        lambda: quote, dispatcher=trade.dispatcher
    )
    store = ExecutionStore(tmp_path / "paper.db", create=True)
    trade.paper = PaperExecution(trade, store, frozenset({123}))
    try:
        await run_blocking(
            trade.place_order,
            "US.AAPL",
            "1.10",
            1,
            "BUY",
            trd_env="SIMULATE",
            acc_id="123",
            operation_id="one",
            admission_epoch=store.epoch,
        )
        # A retry returns the journal outcome while disconnected and while the
        # placement pool is reserved; it neither waits for quota nor replays.
        clock.advance(0.05)
        placement = trade.dispatcher.limiter(QuotaRequest("place_order", 123)).reserve()
        ctx = trade.trade_ctx
        trade.trade_ctx = None
        try:
            retry = await run_blocking(
                trade.place_order,
                "US.AAPL",
                "1.10",
                1,
                "BUY",
                trd_env="SIMULATE",
                acc_id="123",
                operation_id="one",
                admission_epoch=store.epoch,
            )
            assert retry["state"] == "ACKNOWLEDGED"
        finally:
            trade.trade_ctx = ctx
            placement.release()
        held = trade.dispatcher.limiter(
            QuotaRequest("history_order_list_query", 123)
        ).reserve(10)
        stored = await run_blocking(trade.paper.reconcile, "one")
        assert stored["state"] == "ACKNOWLEDGED"
        trade.trade_ctx.order_list_query.assert_not_called()
        trade.trade_ctx.history_order_list_query.assert_not_called()
        store.outcome(
            "one", "UNKNOWN_OUTCOME", "POSSIBLY_SENT", reason="UNRESOLVED_OUTCOME"
        )
        if recovery_path == "background":
            try:
                await run_blocking(trade.paper.recovery.run_once)
                result = store.lookup("one")
                assert result is not None
                assert result["recovery_checks"][-1]["outcome"] == "ERROR"
                assert (
                    trade.paper.result(result)["recovery"]["successful_negative_checks"]
                    == 0
                )
                trade.trade_ctx.order_list_query.assert_called_once()
                trade.trade_ctx.history_order_list_query.assert_not_called()
                trade.trade_ctx.place_order.assert_called_once()
            finally:
                held.release()
            # A correlated order also needs the public fresh-position budget.
            order = trade.trade_ctx.place_order.call_args.kwargs | {
                "order_id": "77",
                "order_status": "SUBMITTED",
                "dealt_qty": 0,
                "dealt_avg_price": 0,
            }
            for operation in ("order_list_query", "history_order_list_query"):
                getattr(trade.trade_ctx, operation).return_value = (
                    RET_OK,
                    pd.DataFrame([order]),
                )
            trade.trade_ctx.position_list_query.return_value = (RET_OK, pd.DataFrame())
            now = trade.paper.recovery.clock()
            trade.paper.recovery.clock = lambda: now + 6
            positions = trade.dispatcher.limiter(
                QuotaRequest("position_list_query", 123)
            ).reserve(10)
            try:
                await run_blocking(trade.paper.recovery.run_once)
                result = store.lookup("one")
                assert result is not None
                assert result["recovery_checks"][-1]["outcome"] == "ERROR"
                trade.trade_ctx.history_order_list_query.assert_called_once()
                trade.trade_ctx.position_list_query.assert_not_called()
            finally:
                positions.release()
            trade.paper.recovery.clock = lambda: now + 12
            await run_blocking(trade.paper.recovery.run_once)
            result = store.lookup("one")
            assert result is not None and result["state"] == "RECONCILED"
            trade.trade_ctx.position_list_query.assert_called_once()
            trade.trade_ctx.place_order.assert_called_once()
            return
        task = asyncio.create_task(run_blocking(trade.paper.reconcile, "one"))
        try:
            await clock.wait_for_sleepers()
            assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
            trade.trade_ctx.order_list_query.assert_not_called()
            trade.trade_ctx.history_order_list_query.assert_not_called()
            clock.advance(5)
            with pytest.raises(ProviderRateLimitError):
                await task
            trade.trade_ctx.place_order.assert_called_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            held.release()
        # The partially acquired fresh-order permit was returned on timeout.
        trade.dispatcher.limiter(QuotaRequest("order_list_query", 123)).reserve(
            10
        ).release()
        trade.dispatcher.limiter(QuotaRequest("position_list_query", 123)).reserve(
            10
        ).release()
    finally:
        store.close()


@pytest.mark.parametrize("conflicting", [False, True])
async def test_concurrent_paper_admission_is_read_after_quota_timeout(
    conflicting, governed, tmp_path, clock
):
    trade = governed.trade_service
    trade.policy = TradingPolicy(TradingMode.SIMULATE)
    trade.trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            ]
        ),
    )
    store = ExecutionStore(tmp_path / "paper.db", create=True)
    trade.paper = PaperExecution(trade, store, frozenset({123}))
    held = trade.dispatcher.limiter(QuotaRequest("place_order", 123)).reserve()
    params = inspect.signature(trade.place_order).bind(
        "US.AAPL",
        "1.10",
        1,
        "BUY",
        trd_env="SIMULATE",
        acc_id="0",
        operation_id="one",
        admission_epoch=store.epoch,
    )
    params.apply_defaults()
    request = {
        key: value
        for key, value in params.arguments.items()
        if key not in {"trd_env", "acc_id", "operation_id", "admission_epoch"}
    }
    task = asyncio.create_task(run_blocking(trade.place_order, **params.arguments))
    try:
        await clock.wait_for_sleepers()
        # Another caller admits the same token while this caller awaits quota.
        if conflicting:
            request["price"] = "2.00"
        canonical = canonicalize_request("SIMULATE", 123, "PLACE", request)
        store.admit("one", store.epoch, 123, "PLACE", canonical, request)
        clock.advance(5)
        if conflicting:
            with pytest.raises(OrderNotSentError, match="Operation ID conflict"):
                await task
        else:
            result = await task
            assert result["state"] == "ADMITTED"
            assert result["status"] == "IN_FLIGHT"
        trade.trade_ctx.place_order.assert_not_called()
        trade.trade_ctx.unlock_trade.assert_not_called()
        governed.market_data_service.quote_ctx.get_market_snapshot.assert_not_called()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        held.release()
        store.close()
