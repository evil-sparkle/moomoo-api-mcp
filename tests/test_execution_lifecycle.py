"""Operator containment persists without bypassing mutation or journal policy."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from moomoo import RET_OK
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.client_authorization import (
    ClientAuthorizationError,
    ClientGrants,
    client_grants_context,
)
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_controls import TradingControlError, TradingControls
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from moomoo_mcp.settings import load_settings, validate_storage
from tests.assertions import raises_message


def test_pause_survives_restart_and_scopes_environment_and_account(tmp_path):
    path = tmp_path / "controls.db"
    controls = TradingControls(path, create=True)
    controls.set_paused(
        "REAL", True, operator_id="operator", reason="review", account="123"
    )
    controls.close()
    controls = TradingControls(path)
    with raises_message(TradingControlError, "paused"):
        controls.check_new_risk("REAL", "123")
    controls.check_new_risk("REAL", "456")
    controls.check_new_risk("SIMULATE", "123")
    controls.set_paused("REAL", True, operator_id="operator", reason="all accounts")
    controls.set_paused(
        "REAL", False, operator_id="operator", reason="account resumed", account="123"
    )
    with raises_message(TradingControlError, "paused"):
        controls.check_new_risk("REAL", "123")
    controls.close()


def test_read_only_observation_opens_existing_both_journals_without_enabling_writes(
    tmp_path,
):
    paper = tmp_path / "paper" / "journal.db"
    real = tmp_path / "real" / "journal.db"
    ExecutionStore(paper, create=True).close()
    ExecutionStore(real, create=True, environment="REAL").close()
    settings = load_settings(
        {
            "MOOMOO_TRADING_MODE": "READ_ONLY",
            "MOOMOO_OBSERVE_EXECUTION": "1",
            "MOOMOO_JOURNAL_PATH": str(paper),
            "MOOMOO_REAL_JOURNAL_PATH": str(real),
            "MOOMOO_CREATE_JOURNAL": "1",
            "MOOMOO_CREATE_REAL_JOURNAL": "1",
        }
    )
    assert not settings.create_journal and not settings.create_real_journal
    validate_storage(settings)
    paper_store, real_store = server._open_execution_stores(settings)
    service = TradeService(
        policy=settings.policy,
        execution_store=paper_store,
        real_execution_store=real_store,
    )
    try:
        assert service.paper is not None and service.real is not None
        for environment in ("REAL", "SIMULATE"):
            with raises_message(RuntimeError, "read-only"):
                service.policy.check_write("cancel", environment)
    finally:
        service.close()


def test_observation_does_not_initialize_missing_storage(tmp_path):
    path = tmp_path / "missing" / "paper.db"
    settings = load_settings(
        {
            "MOOMOO_OBSERVE_EXECUTION": "1",
            "MOOMOO_JOURNAL_PATH": str(path),
            "MOOMOO_CREATE_JOURNAL": "1",
        }
    )
    with raises_message(ValueError, "initialization"):
        validate_storage(settings)
    assert not path.exists()


def test_operator_pause_endpoint_rejects_mcp_token_and_survives_reopen(tmp_path):
    controls = TradingControls(tmp_path / "controls.db", create=True)
    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL), trading_controls=controls
    )
    server.mcp._session_manager = None
    app = server.create_streamable_http_app(
        auth_token="full-test-token",
        operator_auth_token="operator-test-token",
        operator_id="operator",
        allow_chatgpt_tunnel_host=False,
    )
    context = SimpleNamespace(trade_service=service)
    try:
        with (
            patch.object(server, "get_services", return_value=context),
            TestClient(app) as client,
        ):
            url = "/operator/execution/REAL"
            assert (
                client.post(
                    url,
                    headers={"Authorization": "Bearer full-test-token"},
                    json={"paused": True, "reason": "review"},
                ).status_code
                == 401
            )
            headers = {"Authorization": "Bearer operator-test-token"}
            result = client.post(
                url, headers=headers, json={"paused": True, "reason": "review"}
            )
            assert result.status_code == 200 and result.json()["paused"]
            assert client.get(url, headers=headers).json()["paused"]
            assert (
                client.post(
                    url, headers=headers, json={"paused": "false", "reason": "review"}
                ).status_code
                == 409
            )
    finally:
        service.close()
        server.mcp._session_manager = None
    reopened = TradingControls(tmp_path / "controls.db")
    assert reopened.pause_state("REAL")["paused"]
    reopened.close()


def test_final_dispatch_pause_blocks_exposure_before_unlock(tmp_path):
    controls = TradingControls(tmp_path / "controls.db", create=True)
    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL), trading_controls=controls
    )
    service.real = MagicMock()
    service.trade_ctx = MagicMock()
    service.set_execution_paused("REAL", True, operator_id="operator", reason="review")
    call = MagicMock()
    try:
        with raises_message(RuntimeError, "paused"):
            service._dispatch_write(
                "REAL",
                "place_order",
                call,
                lambda value: value,
                adds_exposure=True,
                sdk_operation="place_order",
                account_id=123,
                journal_request={"operation_id": "test"},
            )
        call.assert_not_called()
        service.trade_ctx.unlock_trade.assert_not_called()
        call.return_value = (RET_OK, {"order_id": "12345"})
        receipt, error = service._dispatch_write(
            "REAL",
            "cancel_order",
            call,
            lambda value: value,
            adds_exposure=False,
            sdk_operation="modify_order",
            account_id=123,
            journal_request={"operation_id": "cancel-test"},
        )
        assert receipt == {"order_id": "12345"} and error is None
        call.assert_called_once()
    finally:
        service.close()


def test_real_service_account_scope_covers_reads_defaults_and_stored_retries(
    trade_service_factory,
):
    service = trade_service_factory(
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123, 456}))
    )
    service.trade_ctx = MagicMock()
    service.trade_ctx.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": account, "trd_env": "REAL", "trdmarket_auth": ["US"]}
                for account in (123, 456)
            ]
        ),
    )
    grants = ClientGrants(
        frozenset({"real_read", "real_write"}), real_acc_ids=frozenset({123})
    )
    with client_grants_context(grants):
        assert [row["acc_id"] for row in service.get_accounts()] == [123]
        assert service.resolve_read_account("REAL", "0") == ("REAL", 123)
        assert service._resolve_account("REAL", "US", "0") == 123
        with pytest.raises(ClientAuthorizationError):
            service.resolve_read_account("REAL", "456")
        with pytest.raises(ClientAuthorizationError):
            service._resolve_account("REAL", "US", "456")
        assert service.real is not None
        with pytest.raises(ClientAuthorizationError):
            service.real._retry({"account": "456"}, "PLACE", {})


def test_source_cap_matches_compose_and_requires_explicit_disable():
    for environment in ({}, {"MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": ""}):
        assert dict(load_settings(environment).policy.max_order_notional) == {
            "USD": 500
        }
    assert not load_settings(
        {"MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": "NONE"}
    ).policy.max_order_notional


@pytest.mark.parametrize("missing_lookup", [False, True])
def test_paper_cancel_remains_available_without_quote_metadata(
    tmp_path, missing_lookup
):
    store = ExecutionStore(tmp_path / "paper" / "journal.db", create=True)
    lookup = MagicMock(side_effect=RuntimeError("quote metadata unavailable"))
    service = TradeService(
        policy=TradingPolicy(TradingMode.SIMULATE),
        execution_store=store,
        simulated_account_allowlist=frozenset({123}),
        instrument_lookup=None if missing_lookup else lookup,
    )
    context = MagicMock()
    service.trade_ctx = context
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [{"acc_id": 123, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]}]
        ),
    )
    context.order_list_query.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {
                    "order_id": "9001",
                    "code": "US.AAPL",
                    "trd_side": "BUY",
                    "order_type": "NORMAL",
                    "time_in_force": "DAY",
                    "session": "RTH",
                    "order_status": "SUBMITTED",
                    "qty": 2,
                    "price": 10.0,
                }
            ]
        ),
    )
    context.modify_order.return_value = RET_OK, pd.DataFrame([{"order_id": "9001"}])
    try:
        plan = service.prepare_admission(
            "cancel_order",
            {
                "order_id": "9001",
                "trd_env": "SIMULATE",
                "acc_id": "123",
                "operation_id": "cancel-without-quotes",
                "admission_epoch": store.epoch,
            },
        )
        assert [request.operation for request in plan.requests] == [
            "order_list_query",
            "modify_order",
        ]
        result = service.cancel_order(
            order_id="9001",
            trd_env="SIMULATE",
            acc_id="123",
            operation_id="cancel-without-quotes",
            admission_epoch=store.epoch,
        )
        assert result["state"] == "ACKNOWLEDGED"
        context.modify_order.assert_called_once()
        assert context.modify_order.call_args.kwargs["modify_order_op"] == "CANCEL"
        lookup.assert_not_called()
        context.unlock_trade.assert_not_called()
    finally:
        service.close()
