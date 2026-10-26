"""Documented broker query boundaries and immutable pagination evidence."""

from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import RET_OK, ComboLeg, SecurityFirm

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.query_contracts import TradeQueryError
from moomoo_mcp.services.query_snapshots import QuerySnapshots
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from moomoo_mcp.services.trade_service import TradeService
from tests.assertions import raises_message
from tests.mcp_helpers import call_mcp_tool
from tests.rate_limit_clock import FakeClock

import moomoo_mcp.tools.trade_queries  # noqa: F401,E402  isort:skip


def test_fee_duplicate_rows_are_not_double_counted(queries):
    service, context, _ = queries
    context.order_fee_query.return_value = (
        RET_OK,
        pd.DataFrame([{"order_id": "1", "fee_amount": 0}] * 2),
    )
    with pytest.raises(TradeQueryError) as failure:
        service.get_order_fees(["1"])
    assert failure.value.code == "INVALID_RESPONSE"
    context.unlock_trade.assert_not_called()
    context.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_fee_evidence_reports_missing_zero_and_partial_details(
    queries, mcp_app_context
):
    service, context, _ = queries
    mcp_app_context.trade_service = service
    context.order_fee_query.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"order_id": "1", "fee_amount": 0, "fee_details": []},
                {"order_id": "2", "fee_amount": None, "fee_details": None},
            ]
        ),
    )
    result = await call_mcp_tool(
        mcp_app_context,
        "get_order_fees",
        {
            "order_id_list": ["1", "2", "3"],
            "include_evidence": True,
            "trd_env": "REAL",
            "acc_id": "123",
        },
    )
    payload = result.structured["result"]
    assert payload["coverage"]["missing"] == ["3"]
    assert payload["scope"]["amounts_complete"] is False
    assert payload["scope"]["missing_amount_ids"] == ["2"]
    assert payload["scope"]["missing_detail_ids"] == ["2"]
    assert payload["scope"]["currency"] is None
    assert payload["data"][0]["fee_amount"] == 0
    assert payload["data"][1]["fee_amount"] is None
    context.order_fee_query.assert_called_once()
    context.unlock_trade.assert_not_called()


@pytest.fixture
def queries():
    clock = FakeClock()
    service = TradeService(
        dispatcher=BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)
    )
    context = MagicMock()
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "acc_id": 123,
                    "trd_env": "REAL",
                    "security_firm": SecurityFirm.FUTUSG,
                },
                {
                    "acc_id": 456,
                    "trd_env": "SIMULATE",
                    "security_firm": SecurityFirm.FUTUSG,
                },
            ]
        ),
    )
    rows = pd.DataFrame(
        [
            {
                "order_id": str(index),
                "code": "US.AAPL",
                "qty": 10,
                "price": 2,
                "dealt_qty": 2,
                "dealt_avg_price": 1.95,
                "order_status": "CANCELLED_PART",
            }
            for index in range(5)
        ]
    )
    for method in (
        "order_list_query",
        "history_order_list_query",
        "deal_list_query",
        "history_deal_list_query",
    ):
        getattr(context, method).return_value = (RET_OK, rows)
    context.order_fee_query.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "order_id": "1",
                    "fee_amount": 2.5,
                    "fee_details": [("Commission", 2.0), ("Platform Fee", 0.5)],
                }
            ]
        ),
    )
    service.trade_ctx = context
    service.queries.snapshots = QuerySnapshots(clock=clock)
    return service, context, clock


def test_current_lookup_exact_id_and_execution_fields(queries):
    service, context, _ = queries
    result = service.get_order("1")
    assert result["found"] is True
    assert result["order"]["qty"] == 10
    assert result["order"]["dealt_qty"] == 2
    assert result["order"]["order_status"] == "CANCELLED_PART"
    assert result["scope"]["acc_id"] == "123"
    assert context.order_list_query.call_args.kwargs["order_id"] == "1"
    context.history_order_list_query.assert_not_called()
    context.place_order.assert_not_called()
    context.unlock_trade.assert_not_called()


def test_history_lookup_matches_locally_and_scopes_absence(queries):
    service, context, _ = queries
    result = service.get_order(
        "1", source="HISTORY", code="US.AAPL", start="2026-01-01", end="2026-01-10"
    )
    assert result["found"] is True
    args = context.history_order_list_query.call_args.kwargs
    assert "order_id" not in args
    assert args["code"] == "US.AAPL" and args["start"] == "2026-01-01"
    absent = service.get_order(
        "missing", source="HISTORY", start="2026-01-01", end="2026-01-10"
    )
    assert absent["order"] is None and absent["found"] is False
    assert "not proof of lifetime completeness" in absent["scope"]["coverage"]
    context.order_list_query.assert_not_called()


@pytest.mark.parametrize(
    "start,end",
    [
        ("", ""),
        ("2026-01-01", ""),
        ("2026-02-01", "2026-01-01"),
        ("2024-01-01", "2026-01-01"),
        ("2026-99-01", "2026-01-01"),
    ],
)
def test_unbounded_or_invalid_history_lookup_is_unsent(queries, start, end):
    service, context, _ = queries
    with pytest.raises(ValueError):
        service.get_order("1", source="HISTORY", start=start, end=end)
    context.get_acc_list.assert_not_called()
    context.history_order_list_query.assert_not_called()


def test_ambiguous_and_inexact_order_ids_are_not_hidden(queries):
    service, context, _ = queries
    context.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame([{"order_id": "1"}, {"order_id": "1"}]),
    )
    with pytest.raises(TradeQueryError) as failure:
        service.get_order("1")
    assert failure.value.code == "AMBIGUOUS_ORDER"
    context.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame([{"order_id": float(9007199254740993)}]),
    )
    with pytest.raises(TradeQueryError) as failure:
        service.get_order("9007199254740993")
    assert failure.value.code == "INVALID_IDENTIFIER"


def test_actual_fees_preserve_missing_values_without_currency(queries):
    service, context, _ = queries
    fees = service.get_order_fees(["1"])
    assert fees == [
        {
            "order_id": "1",
            "fee_amount": 2.5,
            "fee_details": [
                {"name": "Commission", "amount": 2.0},
                {"name": "Platform Fee", "amount": 0.5},
            ],
        }
    ]
    assert "currency" not in fees[0]
    context.order_fee_query.return_value = (
        RET_OK,
        pd.DataFrame([{"order_id": "1", "fee_amount": "N/A"}]),
    )
    assert service.get_order_fees(["1"])[0]["fee_amount"] is None
    assert service.get_order_fees(["1"])[0]["fee_details"] is None


@pytest.mark.parametrize("ids", [[], ["1"] * 401, [""], [1], [True], [" 1"]])
def test_fee_identifier_bounds_fail_before_discovery(queries, ids):
    service, context, _ = queries
    with pytest.raises(ValueError):
        service.get_order_fees(ids)
    context.get_acc_list.assert_not_called()
    context.order_fee_query.assert_not_called()


def test_fee_400_ids_are_forwarded_without_truncation(queries):
    service, context, _ = queries
    ids = [str(index) for index in range(400)]
    service.get_order_fees(ids)
    assert context.order_fee_query.call_args.kwargs["order_id_list"] == ids


@pytest.mark.parametrize(
    "method",
    [
        "get_deals",
        "get_history_deals",
        "get_deals_page",
        "get_history_deals_page",
        "get_order_fees",
    ],
)
def test_real_only_queries_refuse_paper_before_any_sdk_call(queries, method):
    service, context, _ = queries
    params: dict[str, object] = {"trd_env": "SIMULATE"}
    if method == "get_order_fees":
        params["order_id_list"] = ["1"]
    with raises_message(ValueError, "REAL"):
        getattr(service, method)(**params)
    assert context.mock_calls == []


def test_canadian_fees_refused_from_discovered_or_configured_firm(queries):
    service, context, _ = queries
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "acc_id": 123,
                    "trd_env": "REAL",
                    "security_firm": SecurityFirm.FUTUCA,
                }
            ]
        ),
    )
    with raises_message(ValueError, "Moomoo CA"):
        service.get_order_fees(["1"])
    context.order_fee_query.assert_not_called()
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame([{"acc_id": 123, "trd_env": "REAL"}]),
    )
    service.security_firm = SecurityFirm.FUTUCA
    with raises_message(ValueError, "Moomoo CA"):
        service.get_order_fees(["1"])
    context.order_fee_query.assert_not_called()


def test_fee_quota_is_independent_by_operation_and_account(queries):
    service, context, _ = queries
    for _ in range(10):
        service.get_order_fees(["1"])
        service.get_history_orders(trd_env="REAL")
    with pytest.raises(ProviderRateLimitError):
        service.get_order_fees(["1"])
    assert context.order_fee_query.call_count == 10
    accounts = context.get_acc_list.return_value[1]
    context.get_acc_list.return_value = (
        RET_OK,
        pd.concat([accounts, pd.DataFrame([{"acc_id": 789, "trd_env": "REAL"}])]),
    )
    service.get_order_fees(["1"], acc_id="789")
    assert context.order_fee_query.call_count == 11


PAGE_CASES = [
    ("get_orders_page", "order_list_query"),
    ("get_deals_page", "deal_list_query"),
    ("get_history_orders_page", "history_order_list_query"),
    ("get_history_deals_page", "history_deal_list_query"),
]


@pytest.mark.parametrize("method,sdk", PAGE_CASES)
def test_pages_are_immutable_and_continuations_query_nothing(queries, method, sdk):
    service, context, _ = queries
    first = getattr(service, method)(limit=2)
    first["items"][0]["raw"]["qty"] = 999
    getattr(context, sdk).return_value = (RET_OK, pd.DataFrame())
    calls = list(context.mock_calls)
    service.trade_ctx = None  # A continuation even survives temporary disconnect.
    second = getattr(service, method)(limit=2, cursor=first["next_cursor"])
    third = getattr(service, method)(limit=2, cursor=second["next_cursor"])
    assert len(second["items"]) == 2 and len(third["items"]) == 1
    assert second["items"][0]["raw"]["qty"] == 10
    assert third["next_cursor"] is None
    assert second["fetched_at"] == first["fetched_at"]
    second["items"][0]["raw"]["qty"] = 999
    replayed = getattr(service, method)(limit=2, cursor=first["next_cursor"])
    assert replayed["items"][0]["raw"]["qty"] == 10
    assert context.mock_calls == calls


@pytest.mark.parametrize(
    "change",
    [
        {"code": "US.TSLA"},
        {"limit": 1},
        {"acc_id": "123"},
        {"trd_env": "SIMULATE"},
        {"status_filter_list": []},
        {"refresh_cache": True},
    ],
)
def test_cursor_scope_cannot_change_without_new_query(queries, change):
    service, context, _ = queries
    first = service.get_orders_page(limit=2)
    calls = list(context.mock_calls)
    with pytest.raises(TradeQueryError) as failure:
        service.get_orders_page(
            **({"limit": 2, "cursor": first["next_cursor"]} | change)
        )
    assert failure.value.code == "CURSOR_SCOPE"
    assert context.mock_calls == calls


def test_history_cursor_binds_dates_and_method(queries):
    service, context, _ = queries
    first = service.get_history_orders_page(start="2026-01-01", limit=2)
    calls = list(context.mock_calls)
    with raises_message(TradeQueryError, "CURSOR_SCOPE"):
        service.get_history_orders_page(
            start="2026-01-02", limit=2, cursor=first["next_cursor"]
        )
    with raises_message(TradeQueryError, "CURSOR_SCOPE"):
        service.get_orders_page(limit=2, cursor=first["next_cursor"])
    assert context.mock_calls == calls


def test_cursor_expiry_and_eviction_are_explicit(queries, monkeypatch):
    service, _, clock = queries
    first = service.get_orders_page(limit=2)
    clock.advance(120)
    with raises_message(TradeQueryError, "CURSOR_EXPIRED"):
        service.get_orders_page(limit=2, cursor=first["next_cursor"])
    monkeypatch.setattr("moomoo_mcp.services.query_snapshots.MAX_SNAPSHOTS", 1)
    first = service.get_orders_page(limit=2)
    service.get_orders_page(limit=1)
    with raises_message(TradeQueryError, "CURSOR_EXPIRED"):
        service.get_orders_page(limit=2, cursor=first["next_cursor"])


@pytest.mark.parametrize("limit", [0, 201, True, 1.5])
def test_invalid_page_size_queries_nothing(queries, limit):
    service, context, _ = queries
    with pytest.raises(ValueError):
        service.get_orders_page(limit=limit)
    assert context.mock_calls == []


def test_snapshot_row_and_byte_bounds_never_silently_truncate(queries, monkeypatch):
    service, _, _ = queries
    monkeypatch.setattr("moomoo_mcp.services.trade_queries.MAX_SNAPSHOT_ROWS", 4)
    with raises_message(TradeQueryError, "SNAPSHOT_LIMIT"):
        service.get_orders_page()
    monkeypatch.setattr("moomoo_mcp.services.trade_queries.MAX_SNAPSHOT_ROWS", 10000)
    monkeypatch.setattr("moomoo_mcp.services.query_snapshots.MAX_SNAPSHOT_BYTES", 10)
    with raises_message(TradeQueryError, "SNAPSHOT_LIMIT"):
        service.get_orders_page()


def test_cache_total_byte_bound_evicts_oldest_snapshots(queries, monkeypatch):
    service, _, _ = queries
    snapshots = service.queries.snapshots
    first = service.get_orders_page(limit=2)
    size = snapshots._bytes
    monkeypatch.setattr("moomoo_mcp.services.query_snapshots.MAX_CACHE_BYTES", size * 2)
    service.get_orders_page(limit=2)
    service.get_orders_page(limit=2)
    assert snapshots._bytes <= size * 2
    with raises_message(TradeQueryError, "CURSOR_EXPIRED"):
        service.get_orders_page(limit=2, cursor=first["next_cursor"])


def test_page_exact_nested_ids_and_missing_numeric_values(queries):
    service, context, _ = queries
    leg = ComboLeg()
    for field, value in {
        "code": "US.XYZ",
        "trd_side": "BUY",
        "qty_ratio": 1,
        "position_id": 3333333333333333333,
    }.items():
        setattr(leg, field, value)
    context.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "order_id": 9007199254740993,
                    "dealt_avg_price": "N/A",
                    "dealt_qty": float("nan"),
                    "combo_legs": [leg],
                }
            ],
            dtype=object,
        ),
    )
    row = service.get_orders_page()["items"][0]
    assert row["order_id"] == "9007199254740993"
    assert row["raw"]["combo_legs"][0]["position_id"] == "3333333333333333333"
    assert row["dealt_avg_price"] is None and row["dealt_qty"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("method,sdk", PAGE_CASES)
async def test_mcp_pages_have_typed_schema_and_no_continuation_admission(
    queries, mcp_app_context, method, sdk
):
    service, context, _ = queries
    mcp_app_context.trade_service = service
    first = await call_mcp_tool(mcp_app_context, method, {"limit": 2})
    assert first.structured["items"] == first.json["items"]
    calls = list(context.mock_calls)
    second = await call_mcp_tool(
        mcp_app_context,
        method,
        {
            "limit": 2,
            "cursor": first.json["next_cursor"],
        },
    )
    assert len(second.json["items"]) == 2
    assert context.mock_calls == calls
    assert getattr(context, sdk).call_count == 1


@pytest.mark.asyncio
async def test_mcp_fees_and_lookup_preserve_large_ids(queries, mcp_app_context):
    service, context, _ = queries
    mcp_app_context.trade_service = service
    context.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame([{"order_id": 9007199254740993}], dtype=object),
    )
    result = await call_mcp_tool(
        mcp_app_context,
        "get_order",
        {
            "order_id": "9007199254740993",
        },
    )
    assert result.json["order"]["order_id"] == "9007199254740993"
    assert result.structured["scope"]["acc_id"] == "123"
    fees = await call_mcp_tool(
        mcp_app_context,
        "get_order_fees",
        {
            "order_id_list": ["1"],
        },
    )
    assert fees.json_blocks[0]["order_id"] == "1"


@pytest.mark.asyncio
async def test_mcp_query_schema_exposes_resource_bounds(queries, mcp_app_context):
    from moomoo_mcp.server import mcp

    service, context, _ = queries
    mcp_app_context.trade_service = service
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for method, _ in PAGE_CASES:
        limit = tools[method].inputSchema["properties"]["limit"]
        assert limit["minimum"] == 1 and limit["maximum"] == 200
    ids = tools["get_order_fees"].inputSchema["properties"]["order_id_list"]
    assert ids["minItems"] == 1 and ids["maxItems"] == 400
    with raises_message(Exception, "integer"):
        await call_mcp_tool(mcp_app_context, "get_orders_page", {"limit": True})
    assert context.mock_calls == []


@pytest.mark.asyncio
async def test_mcp_unsupported_fees_do_not_create_quota_reservations(
    queries, mcp_app_context, monkeypatch
):
    service, context, _ = queries
    mcp_app_context.trade_service = service
    reserve = MagicMock()
    monkeypatch.setattr(service.dispatcher, "run_async", reserve)
    with raises_message(Exception, "REAL"):
        await call_mcp_tool(
            mcp_app_context,
            "get_order_fees",
            {
                "order_id_list": ["1"],
                "trd_env": "SIMULATE",
            },
        )
    reserve.assert_not_called()
    context.order_fee_query.assert_not_called()
