"""Real SDK callback decoding and bounded process observation behavior."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK, TrdEnv, TrdMarket
from moomoo.common.pb import Trd_UpdateOrder_pb2, Trd_UpdateOrderFill_pb2

from moomoo_mcp.services.sdk_response import as_frame
from moomoo_mcp.services.trade_events import (
    DealEventHandler,
    OrderEventHandler,
    TradeEventBuffer,
)
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy

ACCOUNT = 987654321098765431
ORDER = 987654321098765437


def order_response(environment="SIMULATE", account=ACCOUNT):
    response = cast(Any, Trd_UpdateOrder_pb2).Response()
    response.retType = RET_OK
    response.s2c.header.accID = account
    response.s2c.header.trdEnv = TrdEnv.to_number(environment)[1]
    response.s2c.header.trdMarket = TrdMarket.to_number("US")[1]
    response.s2c.order.orderIDEx = str(ORDER)
    response.s2c.order.secMarket = TrdMarket.to_number("US")[1]
    response.s2c.order.trdMarket = TrdMarket.to_number("US")[1]
    response.s2c.order.code = "AAPL"
    response.s2c.order.qty = 3
    response.s2c.order.fillQty = 1
    return response


def test_real_sdk_order_callback_retains_header_account_before_receipt():
    buffer = TradeEventBuffer()
    callback = OrderEventHandler(buffer)

    ret, frame = callback.on_recv_rsp(order_response())

    assert ret == RET_OK
    assert "acc_id" not in as_frame("callback", frame).columns
    result = buffer.read("SIMULATE", ACCOUNT)
    assert result["events"][0]["acc_id"] == str(ACCOUNT)
    assert result["events"][0]["data"]["order_id"] == str(ORDER)
    assert result["events"][0]["data"]["dealt_qty"] == 1
    assert "operation_id" not in result["events"][0]
    assert result["complete_history"] is False


def test_real_sdk_deal_identifiers_are_exact_and_paper_deals_ignored():
    buffer = TradeEventBuffer()
    handler = DealEventHandler(buffer)
    response = cast(Any, Trd_UpdateOrderFill_pb2).Response()
    response.retType = RET_OK
    response.s2c.header.accID = ACCOUNT
    response.s2c.header.trdEnv = TrdEnv.to_number("REAL")[1]
    response.s2c.header.trdMarket = TrdMarket.to_number("US")[1]
    response.s2c.orderFill.orderIDEx = str(ORDER)
    response.s2c.orderFill.fillID = ORDER + 1
    response.s2c.orderFill.secMarket = TrdMarket.to_number("US")[1]
    response.s2c.orderFill.code = "AAPL"
    handler.on_recv_rsp(response)

    result = buffer.read("REAL", ACCOUNT)
    assert result["events"][0]["data"]["deal_id"] == str(ORDER + 1)
    response.s2c.header.trdEnv = TrdEnv.to_number("SIMULATE")[1]
    handler.on_recv_rsp(response)
    assert buffer.read("SIMULATE", ACCOUNT)["events"] == []
    assert buffer.read("REAL", ACCOUNT)["latest_sequence"] == 1


def test_rejected_and_missing_account_callbacks_are_not_guessed():
    buffer = TradeEventBuffer()
    callback = OrderEventHandler(buffer)
    response = order_response()
    response.retType = RET_ERROR
    callback.on_recv_rsp(response)
    response.retType = RET_OK
    response.s2c.header.ClearField("accID")
    callback.on_recv_rsp(response)
    assert buffer.read("SIMULATE", ACCOUNT)["events"] == []


def test_scope_filtering_and_reads_do_not_mutate_stored_rows():
    buffer = TradeEventBuffer()
    buffer.append("ORDER", ACCOUNT, [{"order_id": "1", "trd_env": "REAL"}])
    buffer.append("ORDER", ACCOUNT + 1, [{"order_id": "2", "trd_env": "REAL"}])
    buffer.append("ORDER", ACCOUNT, [{"order_id": "3", "trd_env": "SIMULATE"}])
    result = buffer.read("REAL", ACCOUNT)
    assert [event["data"]["order_id"] for event in result["events"]] == ["1"]
    result["events"][0]["data"]["order_id"] = "changed"
    assert buffer.read("REAL", ACCOUNT)["events"][0]["data"]["order_id"] == "1"
    assert buffer.read("SIMULATE", ACCOUNT)["events"][0]["data"]["order_id"] == "3"


def test_count_retention_gap_and_local_page_continuation():
    buffer = TradeEventBuffer(max_events=3)
    for identifier in range(1, 6):
        buffer.append(
            "ORDER", ACCOUNT, [{"order_id": str(identifier), "trd_env": "REAL"}]
        )
    first = buffer.read("REAL", ACCOUNT, limit=2)
    assert first["retention_gap"] is True
    assert first["oldest_sequence"] == 3
    assert first["has_more"] is True
    assert first["next_sequence"] == 4
    second = buffer.read(
        "REAL",
        ACCOUNT,
        after_sequence=first["next_sequence"],
        event_epoch=first["event_epoch"],
    )
    assert second["retention_gap"] is False
    assert [event["data"]["order_id"] for event in second["events"]] == ["5"]


def test_time_retention_and_restart_are_explicit_gaps():
    clock = [10.0]
    buffer = TradeEventBuffer(retention_seconds=10, monotonic=lambda: clock[0])
    buffer.append("ORDER", ACCOUNT, [{"order_id": "1", "trd_env": "REAL"}])
    clock[0] = 20.0
    assert buffer.read("REAL", ACCOUNT)["retention_gap"] is True
    restarted = TradeEventBuffer()
    result = restarted.read("REAL", ACCOUNT, after_sequence=1, event_epoch=buffer.epoch)
    assert result["restart_gap"] is True
    assert result["events"] == []
    assert result["event_epoch"] != buffer.epoch


def test_concurrent_callback_sequences_are_unique_and_ordered():
    buffer = TradeEventBuffer()

    def emit(number):
        buffer.append("ORDER", ACCOUNT, [{"order_id": str(number), "trd_env": "REAL"}])

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(emit, range(1, 201)))
    result = buffer.read("REAL", ACCOUNT, limit=200)
    assert [event["sequence"] for event in result["events"]] == list(range(1, 201))
    assert len({event["data"]["order_id"] for event in result["events"]}) == 200


@pytest.mark.parametrize("identifier", [float(ORDER), True, "N/A", "0", "-1"])
def test_lossy_or_invalid_callback_identifiers_are_discarded(identifier):
    buffer = TradeEventBuffer()
    buffer.append("ORDER", ACCOUNT, [{"order_id": identifier, "trd_env": "REAL"}])
    assert buffer.read("REAL", ACCOUNT)["events"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": True},
        {"after_sequence": -1},
        {"after_sequence": 1},
    ],
)
def test_event_cursor_and_resource_bounds_are_validated(params):
    with pytest.raises(ValueError):
        TradeEventBuffer().read("REAL", ACCOUNT, **params)


def test_context_registers_once_reconnect_reports_gap_and_close_discards():
    context = MagicMock()
    context.set_handler.return_value = RET_OK
    service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
    with patch(
        "moomoo_mcp.services.trade_service.OpenSecTradeContext", return_value=context
    ):
        service.connect()
    assert context.set_handler.call_count == 2
    order_handler = context.set_handler.call_args_list[0].args[0]
    order_handler.on_recv_rsp(order_response())
    assert len(service.events.read("SIMULATE", ACCOUNT)["events"]) == 1
    context.on_api_socket_reconnected()
    assert context.set_handler.call_count == 2
    assert service.events.read("SIMULATE", ACCOUNT)["reconnect_count"] == 1
    service.close()
    order_handler.on_recv_rsp(order_response())
    assert service.events.read("SIMULATE", ACCOUNT)["events"] == []


def test_failed_handler_registration_closes_unpublished_context():
    context = MagicMock()
    context.set_handler.return_value = RET_ERROR
    service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
    with patch(
        "moomoo_mcp.services.trade_service.OpenSecTradeContext", return_value=context
    ):
        service.connect()
    assert service.trade_ctx is None
    context.close.assert_called_once()


def test_service_event_read_resolves_exact_discovered_environment():
    context = MagicMock()
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": ACCOUNT, "trd_env": "REAL"},
                {"acc_id": ACCOUNT + 1, "trd_env": "SIMULATE"},
            ]
        ),
    )
    service = TradeService()
    service.trade_ctx = context
    service.events.append("ORDER", ACCOUNT, [{"order_id": "1", "trd_env": "REAL"}])
    service.events.append(
        "ORDER", ACCOUNT + 1, [{"order_id": "2", "trd_env": "SIMULATE"}]
    )
    result = service.get_trade_events("real", str(ACCOUNT))
    assert result["events"][0]["data"]["order_id"] == "1"
    with pytest.raises(ValueError) as error:
        service.get_trade_events("SIMULATE", str(ACCOUNT))
    assert "not available" in str(error.value)
    context.unlock_trade.assert_not_called()
    context.place_order.assert_not_called()
