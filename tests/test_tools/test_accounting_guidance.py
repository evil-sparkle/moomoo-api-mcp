"""Registered MCP guidance and synthetic broker response contracts.

These are in-process metadata and dispatch checks, not live client testing or
financial reconciliation. No broker connection or private account data is used.
"""

from copy import deepcopy
from datetime import datetime
from functools import partial
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from moomoo import RET_OK, OpenSecTradeContext, TrdCategory, TrdMarket

from moomoo_mcp.server import mcp
from moomoo_mcp.services.trade_service import TradeService

ACCOUNT_TOOLS = ("get_positions", "get_account_summary")
HISTORY_TOOLS = ("get_history_orders", "get_history_deals")
ACCOUNT_ID = 9007199254740993
POSITION_ID = 9007199254740995
COMBO_ID = 9007199254740997
ORDER_ID = "9007199254740999"
DEAL_ID = 9007199254741001
ADDITIONAL_FIELDS = (
    "average_cost",
    "diluted_cost",
    "pl_ratio_avg_cost",
    "unrealized_pl",
    "realized_pl",
)


async def registered_description(name):
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    return " ".join((tools[name].description or "").split()).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ACCOUNT_TOOLS)
async def test_account_descriptions_preserve_basis_and_uncertainty(tool_name):
    description = await registered_description(tool_name)

    assert "broker-reported position data" in description
    assert "not an independently reconciled accounting ledger" in description
    assert "cost_price is diluted cost" in description
    assert "diluted-cost p/l percentage" in description
    assert (
        "do not label these as average purchase cost or unrealized return"
        in description
    )
    assert "do not treat a current position row as lifetime p/l" in description
    for excluded_scope in (
        "derivatives",
        "option premiums",
        "fees",
        "dividends",
        "closed positions",
    ):
        assert excluded_scope in description
    assert "app/api discrepancy" in description
    assert "unresolved until verified" in description
    assert "do not replace broker fields with reconstructed values" in description
    assert "do not alone prove an error" in description
    assert "validity flags are not independent financial reconciliation" in description
    assert "do not prove a suspected upstream accounting mechanism" in description


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ACCOUNT_TOOLS)
async def test_account_descriptions_define_additional_fields_and_limits(tool_name):
    description = await registered_description(tool_name)

    for field, meaning in (
        ("average_cost", "average cost price"),
        ("diluted_cost", "diluted cost price"),
        ("pl_ratio_avg_cost", "p/l percentage using average cost"),
        ("unrealized_pl", "unrealized p/l amount"),
        ("realized_pl", "realized p/l amount"),
    ):
        assert f"{field}: broker-reported {meaning}" in description
    assert "not applicable to simulate securities accounts" in description
    assert "universal securities accounts" in description
    assert "unrealized_pl and realized_pl use the average-cost basis" in description
    assert "for futures accounts, cost_price is average cost" in description
    assert (
        "diluted_cost, pl_ratio and pl_ratio_avg_cost are not applicable" in description
    )
    assert "preserve unavailable fields as reported" in description


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", HISTORY_TOOLS)
async def test_history_descriptions_explain_dates_and_coverage(tool_name):
    description = await registered_description(tool_name)

    assert "omit dates with empty strings" in description
    assert (
        "both start and end omitted: start is 90 days before today, end is today"
        in description
    )
    assert "sdk host's current date" in description
    assert "only end supplied: start is 90 days before end" in description
    assert "only start supplied: end is 90 days after start" in description
    assert "not necessarily today" in description
    assert "both start and end supplied: request the explicit range" in description
    assert "does not cap it at 90 days" in description
    assert "broker availability and filters still apply" in description
    assert "00:00:00 for start and 23:59:59 for end" in description
    assert "explicit ranges covering the intended period" in description
    assert "does not establish lifetime completeness" in description
    assert "including an empty list" in description
    assert "account and code filters limit scope" in description
    assert (
        "broker history availability has not been independently established"
        in description
    )
    assert "does not include all derivatives" in description
    assert "empty for max range" not in description
    assert "empty for today" not in description


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", HISTORY_TOOLS)
async def test_history_descriptions_distinguish_requests_from_fills(tool_name):
    description = await registered_description(tool_name)

    assert "broker-reported" in description
    assert "dealt_qty" in description
    assert "dealt_avg_price" in description
    assert "cancelled_part" in description
    assert "partial fills" in description
    assert "remainder" in description
    if tool_name == "get_history_orders":
        assert "qty and price are the requested quantity and order price" in description
        assert "executed quantity and average fill price" in description
        assert "do not discard its executions" in description
        assert "status filters can also exclude orders with executions" in description
    else:
        assert (
            "qty and price are the broker-reported actual fill quantity and fill price"
            in description
        )
        assert "do not exclude those executions" in description
        assert "simulate availability must not be assumed" in description


@pytest.fixture
def broker(mcp_app_context):
    """Real read service backed entirely by synthetic SDK response frames."""
    context = MagicMock(spec=OpenSecTradeContext)
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [{"acc_id": ACCOUNT_ID, "trd_env": "REAL", "trdmarket_auth": ["US"]}]
        ),
    )
    service = TradeService()
    service.trade_ctx = context
    mcp_app_context.trade_service = service
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ACCOUNT_TOOLS)
@pytest.mark.parametrize("availability", ("reported", "unavailable", "absent"))
async def test_account_dispatch_preserves_broker_fields_and_shapes(
    call_tool, broker, tool_name, availability
):
    # Deliberately distinct costs and P/L: dispatch must not reconcile arithmetic.
    position = {
        "code": "US.SYNTH",
        "acc_id": ACCOUNT_ID,
        "position_id": POSITION_ID,
        "combo_id": COMBO_ID,
        "qty": 3.0,
        "cost_price": -12.375,
        "cost_price_valid": True,
        "average_cost": 109.125,
        "diluted_cost": -12.375,
        "market_val": 333.875,
        "pl_ratio": 9876.543,
        "pl_ratio_valid": True,
        "pl_ratio_avg_cost": -7.75,
        "pl_val": 456.125,
        "pl_val_valid": False,
        "unrealized_pl": -78.625,
        "realized_pl": 678.875,
        "currency": "USD",
    }
    if availability == "unavailable":
        position.update(dict.fromkeys(ADDITIONAL_FIELDS, "N/A"))
        position["position_id"] = None
        position["combo_id"] = "N/A"
    elif availability == "absent":
        for field in ADDITIONAL_FIELDS:
            position.pop(field)
    assets = {"acc_id": ACCOUNT_ID, "cash": 1234.125, "total_assets": 5678.875}
    before = deepcopy(position)
    position_frame = pd.DataFrame([position])
    broker.position_list_query.return_value = RET_OK, position_frame
    broker.accinfo_query.return_value = RET_OK, pd.DataFrame([assets])

    result = await call_tool(tool_name, {"trd_env": "REAL", "acc_id": str(ACCOUNT_ID)})

    expected_position = {
        **position,
        "acc_id": str(ACCOUNT_ID),
        "position_id": None if availability == "unavailable" else str(POSITION_ID),
        "combo_id": "N/A" if availability == "unavailable" else str(COMBO_ID),
    }
    if tool_name == "get_positions":
        assert result.structured == {"result": [expected_position]}
        assert result.json_blocks == [expected_position]
    else:
        expected_summary = {
            "assets": {**assets, "acc_id": str(ACCOUNT_ID)},
            "positions": [expected_position],
        }
        assert result.structured == expected_summary
        assert result.json == expected_summary
    assert broker.position_list_query.call_args.kwargs["acc_id"] == ACCOUNT_ID
    assert position == before
    assert position_frame.to_dict("records") == [before]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", HISTORY_TOOLS)
async def test_history_dispatch_preserves_requests_fills_and_ids(
    call_tool, broker, tool_name
):
    arguments: dict[str, str | list[str]] = {
        "code": "US.SYNTH",
        "start": "2024-01-01",
        "end": "2026-10-07",
        "trd_env": "REAL",
        "acc_id": str(ACCOUNT_ID),
    }
    if tool_name == "get_history_orders":
        rows = [
            {
                "order_id": ORDER_ID,
                "code": "US.SYNTH",
                "qty": 100.0,
                "price": 120.125,
                "dealt_qty": 7.5,
                "dealt_avg_price": 120.005,
                "order_status": "CANCELLED_PART",
                "combo_legs": [{"position_id": POSITION_ID, "qty_ratio": 1}],
            }
        ]
        query = broker.history_order_list_query
        arguments["status_filter_list"] = ["CANCELLED_PART"]
        expected = [
            {
                **rows[0],
                "combo_legs": [{"position_id": str(POSITION_ID), "qty_ratio": 1}],
            }
        ]
    else:
        rows = [
            {
                "deal_id": DEAL_ID,
                "order_id": ORDER_ID,
                "code": "US.SYNTH",
                "qty": 7.5,
                "price": 120.005,
                "create_time": "2025-04-01 10:00:00",
            }
        ]
        query = broker.history_deal_list_query
        expected = [{**rows[0], "deal_id": str(DEAL_ID)}]
    before = deepcopy(rows)
    frame = pd.DataFrame(rows)
    query.return_value = RET_OK, frame

    result = await call_tool(tool_name, arguments)

    assert result.structured == {"result": expected}
    assert result.json_blocks == expected
    kwargs = query.call_args.kwargs
    assert kwargs["acc_id"] == ACCOUNT_ID
    assert kwargs["start"] == arguments["start"]
    assert kwargs["end"] == arguments["end"]
    assert rows == before
    assert frame.to_dict("records") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", (*ACCOUNT_TOOLS, *HISTORY_TOOLS))
async def test_empty_broker_responses_keep_existing_shapes(
    call_tool, broker, tool_name
):
    for query in (
        broker.accinfo_query,
        broker.position_list_query,
        broker.history_order_list_query,
        broker.history_deal_list_query,
    ):
        query.return_value = RET_OK, pd.DataFrame()

    result = await call_tool(tool_name, {"trd_env": "REAL", "acc_id": str(ACCOUNT_ID)})

    if tool_name == "get_account_summary":
        assert result.structured == {"assets": {}, "positions": []}
        assert result.json == {"assets": {}, "positions": []}
    else:
        assert result.structured == {"result": []}
        assert result.json_blocks == []


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 7, 12, 30, tzinfo=tz)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", HISTORY_TOOLS)
@pytest.mark.parametrize(
    ("bounds", "expected_start", "expected_end"),
    [
        ({}, "2026-07-09 00:00:00", "2026-10-07 23:59:59"),
        ({"end": "2025-04-01"}, "2025-01-01 00:00:00", "2025-04-01 23:59:59"),
        ({"start": "2025-01-01"}, "2025-01-01 00:00:00", "2025-04-01 23:59:59"),
        (
            {"start": "2024-01-01", "end": "2026-10-07"},
            "2024-01-01 00:00:00",
            "2026-10-07 23:59:59",
        ),
        (
            {"start": "2024-01-01 10:30:45", "end": "2026-10-07 16:30:17"},
            "2024-01-01 10:30:45",
            "2026-10-07 16:30:17",
        ),
    ],
)
async def test_history_dates_through_mcp_and_installed_sdk(
    call_tool, broker, tool_name, bounds, expected_start, expected_end
):
    """Only the SDK transport is mocked; its date normalization runs normally."""
    broker._OpenTradeContextBase__trd_category = TrdCategory.SECURITY
    broker._OpenTradeContextBase__trd_mkt = TrdMarket.NONE
    for method in (
        "history_order_list_query",
        "history_deal_list_query",
        "_check_acc_id_and_acc_index",
        "_check_acc_id_exist",
        "_check_trd_env",
        "_check_stock_code",
        "_check_order_status",
    ):
        getattr(broker, method).side_effect = partial(
            getattr(OpenSecTradeContext, method), broker
        )
    query = broker._get_sync_query_processor.return_value
    query.return_value = RET_OK, "", []

    with patch("moomoo.common.utils.datetime", FixedDateTime):
        result = await call_tool(
            tool_name, {**bounds, "trd_env": "REAL", "acc_id": str(ACCOUNT_ID)}
        )

    assert result.structured == {"result": []}
    query.assert_called_once()
    assert query.call_args.kwargs["start"] == expected_start
    assert query.call_args.kwargs["end"] == expected_end
    assert query.call_args.kwargs["acc_id"] == ACCOUNT_ID
