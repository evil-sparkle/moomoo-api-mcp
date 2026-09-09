"""Argument-forwarding tests for the trading MCP tools.

These dispatch by tool name through the real FastMCP server, so they prove what
a client's arguments actually become by the time they reach the service — in
particular that a 64-bit account id supplied as a decimal string is forwarded
unchanged rather than being coerced into a lossy number by the tool schema.
"""

import pytest

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
