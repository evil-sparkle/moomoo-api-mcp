"""Argument-forwarding tests for the trading MCP tools.

These dispatch by tool name through the real FastMCP server, so they prove what
a client's arguments actually become by the time they reach the service — in
particular that a 64-bit account id supplied as a decimal string is forwarded
unchanged rather than being coerced into a lossy number by the tool schema.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.conftest import call_mcp_tool

# Larger than 2**53: a client that parsed this as a number would round it.
LARGE_ACC_ID = "283726802397238513"


@pytest.mark.asyncio
async def test_place_order_forwards_string_account_id(call_tool, mock_trade_service):
    mock_trade_service.place_order.return_value = {"order_id": "1"}

    await call_tool(
        "place_order",
        {
            "code": "US.AAPL",
            "price": 100.0,
            "qty": 1,
            "trd_side": "BUY",
            "trd_env": "SIMULATE",
            "acc_id": LARGE_ACC_ID,
        },
    )

    kwargs = mock_trade_service.place_order.call_args.kwargs
    assert kwargs["acc_id"] == LARGE_ACC_ID
    assert int(kwargs["acc_id"]) == int(LARGE_ACC_ID)
    assert kwargs["code"] == "US.AAPL"
    assert kwargs["trd_env"] == "SIMULATE"


@pytest.mark.asyncio
async def test_place_combo_order_forwards_legs_and_string_ids(
    call_tool, mock_trade_service
):
    mock_trade_service.place_combo_order.return_value = {"order_id": "2"}
    legs = [
        {
            "code": "US.XYZ260101C100000",
            "trd_side": "SELL",
            "qty_ratio": 1,
            "position_id": "3333333333333333333",
        },
        {"code": "US.XYZ260101C105000", "trd_side": "BUY", "qty_ratio": 1},
    ]

    await call_tool(
        "place_combo_order",
        {
            "combo_legs": legs,
            "price": 2.5,
            "qty": 1,
            "trd_env": "SIMULATE",
            "acc_id": LARGE_ACC_ID,
        },
    )

    kwargs = mock_trade_service.place_combo_order.call_args.kwargs
    assert kwargs["acc_id"] == LARGE_ACC_ID
    assert kwargs["combo_legs"] == legs
    assert kwargs["combo_legs"][0]["position_id"] == "3333333333333333333"


@pytest.mark.asyncio
async def test_modify_order_forwards_string_account_id(call_tool, mock_trade_service):
    mock_trade_service.modify_order.return_value = {"order_id": "3"}

    await call_tool(
        "modify_order",
        {
            "order_id": "3",
            "modify_order_op": "NORMAL",
            "qty": 5,
            "price": 10.0,
            "trd_env": "SIMULATE",
            "acc_id": LARGE_ACC_ID,
        },
    )

    assert mock_trade_service.modify_order.call_args.kwargs["acc_id"] == LARGE_ACC_ID


@pytest.mark.asyncio
async def test_cancel_order_forwards_string_account_id(call_tool, mock_trade_service):
    mock_trade_service.cancel_order.return_value = {"order_id": "4"}

    await call_tool(
        "cancel_order",
        {"order_id": "4", "trd_env": "SIMULATE", "acc_id": LARGE_ACC_ID},
    )

    assert mock_trade_service.cancel_order.call_args.kwargs["acc_id"] == LARGE_ACC_ID


@pytest.mark.asyncio
async def test_order_reads_forward_string_account_id(call_tool, mock_trade_service):
    mock_trade_service.get_orders.return_value = []
    mock_trade_service.get_deals.return_value = []
    mock_trade_service.get_history_orders.return_value = []
    mock_trade_service.get_history_deals.return_value = []

    for name in ("get_orders", "get_deals", "get_history_orders", "get_history_deals"):
        await call_tool(name, {"trd_env": "SIMULATE", "acc_id": LARGE_ACC_ID})
        called = getattr(mock_trade_service, name)
        assert called.call_args.kwargs["acc_id"] == LARGE_ACC_ID


class TestPolicyThroughMcpDispatch:
    """Policy refusals must surface through actual MCP tool calls too."""

    @staticmethod
    def _live_trade_service(context, mode):
        service = TradeService(policy=TradingPolicy(mode))
        service.trade_ctx = context
        return service

    @pytest.fixture
    def trade_ctx(self):
        context = MagicMock()
        frame = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
        context.place_order.return_value = (0, frame)
        context.place_combo_order.return_value = (0, frame)
        context.modify_order.return_value = (0, frame)
        context.unlock_trade.return_value = (0, None)
        return context

    @pytest.mark.parametrize(
        "tool,arguments",
        [
            (
                "place_order",
                {
                    "code": "US.AAPL",
                    "price": 1.0,
                    "qty": 1,
                    "trd_side": "BUY",
                    "trd_env": "REAL",
                    "acc_id": "456",
                },
            ),
            (
                "place_combo_order",
                {
                    "combo_legs": [
                        {"code": "US.A260101C1", "trd_side": "BUY", "qty_ratio": 1},
                        {"code": "US.A260101C2", "trd_side": "SELL", "qty_ratio": 1},
                    ],
                    "price": 2.5,
                    "qty": 1,
                    "trd_env": "REAL",
                    "acc_id": "456",
                },
            ),
            (
                "modify_order",
                {
                    "order_id": "1",
                    "modify_order_op": "NORMAL",
                    "trd_env": "REAL",
                    "acc_id": "456",
                },
            ),
            ("cancel_order", {"order_id": "1", "trd_env": "REAL", "acc_id": "456"}),
        ],
    )
    @pytest.mark.asyncio
    async def test_read_only_refuses_every_write_tool(
        self, mcp_app_context, trade_ctx, tool, arguments
    ):
        mcp_app_context.trade_service = self._live_trade_service(
            trade_ctx, TradingMode.READ_ONLY
        )

        with pytest.raises(Exception, match="not permitted"):
            await call_mcp_tool(mcp_app_context, tool, arguments)

        assert trade_ctx.method_calls == []

    @pytest.mark.asyncio
    async def test_simulate_mode_permits_a_simulate_write(
        self, mcp_app_context, trade_ctx
    ):
        mcp_app_context.trade_service = self._live_trade_service(
            trade_ctx, TradingMode.SIMULATE
        )

        result = await call_mcp_tool(
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

        assert result.json["order_id"] == "1"
        assert trade_ctx.place_order.call_args.kwargs["trd_env"] == "SIMULATE"

    @pytest.mark.asyncio
    async def test_simulate_mode_refuses_a_real_write(self, mcp_app_context, trade_ctx):
        mcp_app_context.trade_service = self._live_trade_service(
            trade_ctx, TradingMode.SIMULATE
        )

        with pytest.raises(Exception, match="does not permit REAL writes"):
            await call_mcp_tool(
                mcp_app_context,
                "place_order",
                {
                    "code": "US.AAPL",
                    "price": 1.0,
                    "qty": 1,
                    "trd_side": "BUY",
                    "trd_env": "REAL",
                    "acc_id": "456",
                },
            )

        trade_ctx.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_unlock_tool_is_refused_outside_real_mode(
        self, mcp_app_context, trade_ctx
    ):
        mcp_app_context.trade_service = self._live_trade_service(
            trade_ctx, TradingMode.SIMULATE
        )

        with pytest.raises(Exception, match="only REAL mode may unlock"):
            await call_mcp_tool(
                mcp_app_context, "unlock_trade", {"password": "pw"}
            )

        trade_ctx.unlock_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_account_reads_stay_available_in_read_only(
        self, mcp_app_context, trade_ctx
    ):
        trade_ctx.get_acc_list.return_value = (
            0,
            pd.DataFrame([{"acc_id": 456, "trd_env": "REAL"}]),
        )
        mcp_app_context.trade_service = self._live_trade_service(
            trade_ctx, TradingMode.READ_ONLY
        )

        result = await call_mcp_tool(mcp_app_context, "get_accounts")

        assert result.structured["result"][0]["acc_id"] == "456"
