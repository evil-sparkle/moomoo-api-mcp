"""Prepared-read quota planning and immutable paper argument identities."""

import inspect
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import RET_OK, Session

from moomoo_mcp.client_authorization import (
    ClientAuthorizationError,
    ClientGrants,
    client_grants_context,
)
from moomoo_mcp.services.admission import _paper_parameters
from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.trade_preparation import bound_parameters
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_controls import (
    TradingControlError,
    TradingControls,
    canonical_intent,
)
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.assertions import raises_message


@pytest.fixture
def preparation(tmp_path):
    controls = TradingControls(tmp_path / "controls.sqlite3", create=True)
    journal = ExecutionStore(tmp_path / "real.sqlite3", create=True, environment="REAL")
    paper_journal = ExecutionStore(
        tmp_path / "paper" / "execution.sqlite3", create=True
    )
    service = TradeService(
        dispatcher=BrokerRequestDispatcher(),
        trading_controls=controls,
        execution_store=paper_journal,
        real_execution_store=journal,
        simulated_account_allowlist=frozenset({456}),
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
    )
    context = MagicMock()
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
                {"acc_id": 456, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            ]
        ),
    )
    service.trade_ctx = context
    yield service, context
    paper_journal.close()
    journal.close()
    controls.close()


def parameters(service, method="place_order", **overrides):
    result = {
        "trd_env": "REAL",
        "acc_id": "0",
        "operation_id": "caller-operation",
        "admission_epoch": service.real.store.epoch,
    }
    if method == "place_order":
        result.update(code="US.AAPL", price=10.0, qty=2, trd_side="BUY")
    elif method == "place_combo_order":
        result.update(
            combo_legs=[
                {"code": "US.AAPL261120C100000", "trd_side": "BUY", "qty_ratio": 1},
                {"code": "US.AAPL261120C105000", "trd_side": "SELL", "qty_ratio": 1},
            ],
            price=1.0,
            qty=2,
        )
    elif method == "modify_order":
        result.update(order_id="9007199254740993", modify_order_op="NORMAL", qty=3)
    else:
        result.update(order_id="9007199254740993")
    return result | overrides


def prepare_plan(service, method="place_order", **overrides):
    return service.prepare_admission(
        "prepare_trade_request",
        {
            "method": method,
            "parameters": parameters(service, method, **overrides),
            "ttl_seconds": 300,
        },
    )


def requests(plan):
    return [(item.operation, item.account_id, item.weight) for item in plan.requests]


def assert_reads_only(plan):
    assert plan.write_operation is None
    assert all(
        item.operation
        not in {
            "place_order",
            "place_combo_order",
            "modify_order",
            "unlock_trade",
        }
        for item in plan.requests
    )


@pytest.mark.parametrize("acc_id", ["0", "123"])
def test_read_only_preview_reserves_snapshot_and_native_maximum(preparation, acc_id):
    service, context = preparation
    service.policy = TradingPolicy()
    bound = inspect.signature(service.preview_order).bind(
        "US.AAPL", 10.0, 2, "BUY", "REAL", acc_id=acc_id
    )
    bound.apply_defaults()
    plan = service.prepare_admission("preview_order", dict(bound.arguments))
    assert requests(plan) == [
        ("get_market_snapshot", None, 1),
        ("acctradinginfo_query", 123, 1),
    ]
    assert_reads_only(plan)
    context.get_acc_list.assert_called_once_with()
    context.place_order.assert_not_called()
    context.unlock_trade.assert_not_called()


@pytest.mark.parametrize("acc_id", ["0", "123"])
def test_single_preparation_reserves_only_snapshot_and_maximum(preparation, acc_id):
    service, context = preparation
    plan = prepare_plan(service, acc_id=acc_id)
    assert requests(plan) == [
        ("get_market_snapshot", None, 1),
        ("acctradinginfo_query", 123, 1),
    ]
    assert_reads_only(plan)
    context.get_acc_list.assert_called_once_with()


@pytest.mark.parametrize("acc_id", ["0", "456"])
def test_paper_preparation_resolves_account_once(preparation, acc_id):
    service, context = preparation
    assert service.paper is not None
    plan = prepare_plan(
        service,
        trd_env="SIMULATE",
        acc_id=acc_id,
        price="10.00",
        admission_epoch=service.paper.store.epoch,
    )
    assert requests(plan) == [
        ("get_market_snapshot", None, 1),
        ("acctradinginfo_query", 456, 1),
    ]
    assert_reads_only(plan)
    context.get_acc_list.assert_called_once_with()


def test_explicit_real_preparation_verifies_read_account_membership(preparation):
    service, context = preparation
    service.policy = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({456}))
    with raises_message(ValueError, "not available"):
        prepare_plan(service, acc_id="456")
    context.get_acc_list.assert_called_once_with()


@pytest.mark.parametrize(
    "environment,acc_id", [("REAL", "0"), ("SIMULATE", "0"), ("SIMULATE", "456")]
)
@pytest.mark.parametrize("method", ["place_order", "modify_order", "cancel_order"])
@pytest.mark.parametrize("read_allowed", [False, True])
def test_preparation_admission_requires_read_grant_without_rediscovery(
    preparation, environment, acc_id, method, read_allowed
):
    service, context = preparation
    executor = service.real if environment == "REAL" else service.paper
    assert executor is not None
    overrides = {
        "trd_env": environment,
        "acc_id": acc_id,
        "admission_epoch": executor.store.epoch,
    }
    if method == "place_order":
        overrides["price"] = 10.0 if environment == "REAL" else "10.00"
    permissions = {environment.lower() + "_write"}
    if read_allowed:
        permissions.add(environment.lower() + "_read")
    grants = ClientGrants(
        permissions=frozenset(permissions),
        real_acc_ids=frozenset({123}),
        simulate_acc_ids=frozenset({456}),
    )
    with client_grants_context(grants):
        if read_allowed:
            plan = prepare_plan(service, method, **overrides)
            assert_reads_only(plan)
            account = 123 if environment == "REAL" else 456
            assert all(item.account_id in {None, account} for item in plan.requests)
        else:
            with pytest.raises(ClientAuthorizationError):
                prepare_plan(service, method, **overrides)
    context.get_acc_list.assert_called_once_with()
    context.order_list_query.assert_not_called()
    context.acctradinginfo_query.assert_not_called()


@pytest.mark.parametrize("operation", ["NORMAL", "ENABLE", " normal ", " enable "])
def test_exposing_modification_reserves_target_then_snapshot(preparation, operation):
    service, context = preparation
    plan = prepare_plan(service, "modify_order", modify_order_op=operation)
    assert requests(plan) == [
        ("order_list_query", 123, 1),
        ("get_market_snapshot", None, 1),
    ]
    assert_reads_only(plan)
    context.get_acc_list.assert_called_once_with()


@pytest.mark.parametrize(
    "method,patch",
    [
        ("cancel_order", {}),
        ("modify_order", {"modify_order_op": "CANCEL", "qty": None}),
        ("modify_order", {"modify_order_op": "DISABLE", "qty": None}),
    ],
)
def test_reducing_target_preparation_needs_only_target_lookup(
    preparation, method, patch
):
    service, _ = preparation
    plan = prepare_plan(service, method, **patch)
    assert requests(plan) == [("order_list_query", 123, 1)]
    assert_reads_only(plan)


@pytest.mark.parametrize("limit", ["none", "order", "daily"])
def test_combo_preparation_queries_margin_and_snapshot_when_valued(preparation, limit):
    service, _ = preparation
    service.policy = TradingPolicy(
        TradingMode.REAL,
        real_acc_ids=frozenset({123}),
        max_order_notional={"USD": 100} if limit == "order" else {},
        max_daily_notional={"USD": 100} if limit == "daily" else {},
    )
    plan = prepare_plan(service, "place_combo_order")
    expected: list[tuple[str, int | None, int]] = [
        ("comboorder_tradinginfo_query", 123, 1)
    ]
    if limit != "none":
        expected.insert(0, ("get_market_snapshot", None, 1))
    assert requests(plan) == expected
    assert_reads_only(plan)


def test_missing_controls_preparation_fails_before_account_discovery(preparation):
    service, context = preparation
    service.trading_controls = None
    with raises_message(TradingControlError, "controls"):
        prepare_plan(service)
    context.get_acc_list.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        {"operation_id": ""},
        {"admission_epoch": ""},
        {"approval_id": "cannot-self-approve"},
        {"session": "invalid"},
    ],
)
def test_invalid_preparation_identity_fails_before_account_discovery(
    preparation, invalid
):
    service, context = preparation
    with pytest.raises((ValueError, TypeError)):
        prepare_plan(service, **invalid)
    context.get_acc_list.assert_not_called()


def test_missing_required_parameters_and_unknown_method_query_nothing(preparation):
    service, context = preparation
    for method, params in (("place_order", {"trd_env": "REAL"}), ("unlock_trade", {})):
        with pytest.raises((ValueError, TypeError)):
            service.prepare_admission(
                "prepare_trade_request",
                {
                    "method": method,
                    "parameters": params,
                    "ttl_seconds": 300,
                },
            )
    context.get_acc_list.assert_not_called()


@pytest.mark.parametrize("invalid", [{"qty": True}, {"qty": 0}, {"price": -1.0}])
def test_invalid_single_order_values_fail_before_account_discovery(
    preparation, invalid
):
    service, context = preparation
    with pytest.raises((ValueError, RuntimeError)):
        prepare_plan(service, **invalid)
    context.get_acc_list.assert_not_called()


def test_stale_epoch_preparation_fails_before_account_discovery(preparation):
    service, context = preparation
    with raises_message(TradingControlError, "epoch"):
        prepare_plan(service, admission_epoch="stale-epoch")
    context.get_acc_list.assert_not_called()


def test_closed_controls_preparation_fails_before_account_discovery(preparation):
    service, context = preparation
    service.trading_controls.close()
    with raises_message(TradingControlError, "controls"):
        prepare_plan(service)
    context.get_acc_list.assert_not_called()


def test_paper_session_aliases_retain_canonical_request_and_caller_parameters(
    preparation,
):
    service, context = preparation
    original = parameters(service, trd_env="simulate", price="10.00")
    baseline = bound_parameters(service, "place_order", original)
    for alias in ("N/A", "NONE", "rth", " RTH "):
        normalized = bound_parameters(
            service, "place_order", original | {"session": alias}
        )
        assert normalized["session"] == Session.NONE
        assert canonical_intent("place_order", normalized) == canonical_intent(
            "place_order", baseline
        )
        kind, inner = _paper_parameters("place_order", normalized)
        assert kind == "PLACE" and "session" not in inner
    assert original["trd_env"] == "simulate"
    assert "session" not in original and "approval_id" not in original
    context.get_acc_list.assert_not_called()


def test_paper_wider_sessions_fail_during_binding_and_no_quotas(preparation):
    service, context = preparation
    for unsupported in ("ETH", "OVERNIGHT", "ALL"):
        with raises_message(ValueError, "RTH"):
            prepare_plan(
                service, trd_env="SIMULATE", price="10.00", session=unsupported
            )
    context.get_acc_list.assert_not_called()


def test_bound_combo_parameters_copy_nested_leg_identity(preparation):
    service, context = preparation
    original = parameters(service, "place_combo_order")
    bound = bound_parameters(service, "place_combo_order", original)
    original["combo_legs"][0]["qty_ratio"] = 999
    assert bound["combo_legs"][0]["qty_ratio"] == 1
    context.get_acc_list.assert_not_called()


def test_preparation_resolves_write_account_before_read_planning(preparation):
    service, context = preparation
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
                {"acc_id": 456, "trd_env": "REAL", "trdmarket_auth": ["US"]},
            ]
        ),
    )
    plan = prepare_plan(service)
    assert requests(plan) == [
        ("get_market_snapshot", None, 1),
        ("acctradinginfo_query", 123, 1),
    ]
    assert_reads_only(plan)
    context.get_acc_list.assert_called_once_with()
