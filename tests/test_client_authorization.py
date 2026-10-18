"""Restricted HTTP clients cannot inherit a REAL backend's execution authority."""

import json
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import anyio.to_thread
import pandas as pd
import pytest
from moomoo import RET_OK
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.client_authorization import (
    CLIENT_PERMISSIONS,
    ClientAuthorizationError,
    ClientGrants,
    account_access_allowed,
    authorize_tool,
    client_authorization_status,
    client_grants_context,
    current_client_grants,
    filter_readable_accounts,
    visible_journal_health,
)
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.query_contracts import QueryScope, order_view
from moomoo_mcp.services.query_snapshots import QuerySnapshots
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy

PRIMARY_TOKEN = "fixture-primary-client"
RESTRICTED_TOKEN = "fixture-restricted-client"
OPERATOR_TOKEN = "fixture-operator-client"


def test_restricted_permissions_default_to_reads_and_paper():
    with client_grants_context(ClientGrants()):
        authorize_tool("place_order", {"trd_env": "SIMULATE", "acc_id": "123"})
        authorize_tool("get_positions", {"trd_env": "REAL", "acc_id": "456"})
        for name, arguments in (
            ("place_order", {"trd_env": "REAL"}),
            ("cancel_order", {"trd_env": "REAL"}),
            ("lock_trade", {}),
            ("unlock_trade", {}),
            ("unsubscribe_market_data", {}),
            ("future_unclassified_tool", {}),
            (
                "prepare_trade_request",
                {"method": "place_order", "parameters": {"trd_env": "REAL"}},
            ),
        ):
            with pytest.raises(ClientAuthorizationError):
                authorize_tool(name, arguments)
    assert current_client_grants() is None


def test_account_reads_and_writes_use_environment_specific_grants():
    grants = ClientGrants(
        permissions=frozenset({"real_read", "simulate_write"}),
        real_acc_ids=frozenset({123}),
        simulate_acc_ids=frozenset({456}),
    )
    with client_grants_context(grants):
        assert account_access_allowed("REAL", "123")
        assert not account_access_allowed("REAL", "456")
        assert not account_access_allowed("SIMULATE", "456")
        assert account_access_allowed("SIMULATE", "456", write=True)
        assert not account_access_allowed("REAL", "123", write=True)
        assert filter_readable_accounts(
            [
                {"trd_env": "REAL", "acc_id": 123},
                {"trd_env": "REAL", "acc_id": 456},
                {"trd_env": "SIMULATE", "acc_id": 456},
            ]
        ) == [{"trd_env": "REAL", "acc_id": 123}]


def test_write_account_resolution_does_not_require_account_read_permission():
    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123, 456}))
    )
    context = MagicMock()
    context.get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"trd_env": "REAL", "acc_id": 123, "trdmarket_auth": ["US"]},
                {"trd_env": "REAL", "acc_id": 456, "trdmarket_auth": ["US"]},
            ]
        ),
    )
    service.trade_ctx = context
    try:
        with client_grants_context(
            ClientGrants(
                permissions=frozenset({"real_write"}), real_acc_ids=frozenset({123})
            )
        ):
            assert service.get_accounts() == []
            assert service._resolve_account("REAL", "US", "0") == 123
            with pytest.raises(ClientAuthorizationError):
                service.resolve_read_account("REAL", "123")
    finally:
        service.close()


def test_real_combo_preview_uses_read_grant_without_live_execution_grant():
    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123}))
    )
    context = MagicMock()
    context.comboorder_tradinginfo_query.return_value = (
        RET_OK,
        pd.DataFrame([{"maintenance_margin": 100.0}]),
    )
    service.trade_ctx = context
    parameters: dict[str, Any] = {
        "combo_legs": [
            {"code": "US.AAPL261120C100000", "trd_side": "BUY", "qty_ratio": 1},
            {"code": "US.AAPL261120C105000", "trd_side": "SELL", "qty_ratio": 1},
        ],
        "price": 1.0,
        "qty": 1,
        "order_type": "NORMAL",
        "trd_env": "REAL",
        "acc_id": "123",
    }
    try:
        with client_grants_context(ClientGrants()):
            plan = service.prepare_admission("preview_combo_order", dict(parameters))
            assert plan.write_operation is None
            assert service.preview_combo_order(**parameters)["acc_id"] == 123
        context.comboorder_tradinginfo_query.assert_called_once()
        context.place_order.assert_not_called()
        context.unlock_trade.assert_not_called()
    finally:
        service.close()


def test_process_service_startup_does_not_inherit_request_grants(monkeypatch):
    context = MagicMock()

    def build_services():
        assert current_client_grants() is None
        return context

    monkeypatch.setattr(server, "_services", None)
    monkeypatch.setattr(server, "_build_services", build_services)
    monkeypatch.setattr(server.atexit, "register", lambda _callback: None)
    with client_grants_context(ClientGrants()):
        assert server.get_services() is context
        assert current_client_grants() is not None


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_scoped_result_redacts_pending_ids_from_persisted_legacy_error(
    tmp_path, environment
):
    store = ExecutionStore(
        tmp_path / environment / "journal.db", create=True, environment=environment
    )
    store.review()
    legacy_error = (
        f"{environment} execution JOURNAL_BLOCKED: "
        "pending operations ['other-account-private-operation']; "
        "automatic recovery or healthy storage restart is required; "
        "see check_health/get_execution. No order was sent"
    )
    store.admit("own-operation", store.epoch, 123, "PLACE", "{}", {})
    store.outcome("own-operation", "REFUSED", "NOT_SENT", error=legacy_error)
    service = TradeService(
        execution_store=store if environment == "SIMULATE" else None,
        real_execution_store=store if environment == "REAL" else None,
    )
    try:
        execution = service.real if environment == "REAL" else service.paper
        row = store.lookup("own-operation")
        assert execution is not None and row is not None
        assert execution.result(row)["error"] == legacy_error
        grants = ClientGrants(
            real_acc_ids=frozenset({123}), simulate_acc_ids=frozenset({123})
        )
        with client_grants_context(grants):
            error = execution.result(row)["error"]
        assert "other-account-private-operation" not in error
        assert f"{environment} execution JOURNAL_BLOCKED" in error
        assert "check_health/get_execution. No order was sent" in error
        stored = store.lookup("own-operation")
        assert stored is not None and stored["error"] == legacy_error
    finally:
        service.close()


def test_health_does_not_disclose_cross_account_journal_evidence():
    health = {
        "state": "RECOVERY_PENDING",
        "admission_epoch": "epoch",
        "pending_operation_ids": ["private-operation"],
        "recovery_updates": [{"account": "456"}],
        "blocking_reasons": ["RECOVERY_PENDING"],
    }
    with client_grants_context(
        ClientGrants(
            permissions=frozenset({"real_read"}), real_acc_ids=frozenset({123})
        )
    ):
        assert visible_journal_health(health, "SIMULATE") == {"state": "NOT_AUTHORIZED"}
        visible = visible_journal_health(health, "REAL")
        assert visible["state"] == "RECOVERY_PENDING"
        assert "pending_operation_ids" not in visible
        assert "recovery_updates" not in visible
    assert health["pending_operation_ids"] == ["private-operation"]


def test_snapshot_continuation_rechecks_resolved_account_access():
    snapshots = QuerySnapshots()
    query = {"limit": 1, "acc_id": "0", "trd_env": "REAL"}
    scope: QueryScope = {
        "acc_id": "456",
        "trd_env": "REAL",
        "source": "CURRENT_ORDERS",
        "filters": {},
        "coverage": "provider_returned_rows",
    }
    first = snapshots.create(
        query,
        [order_view({"order_id": "1"}), order_view({"order_id": "2"})],
        scope,
        "now",
    )
    cursor = first["next_cursor"]
    assert cursor is not None
    with (
        client_grants_context(ClientGrants(real_acc_ids=frozenset({123}))),
        pytest.raises(ClientAuthorizationError),
    ):
        snapshots.continue_page(cursor, query)
    with client_grants_context(ClientGrants(real_acc_ids=frozenset({456}))):
        assert snapshots.continue_page(cursor, query)["items"][0]["order_id"] == "2"


def test_bearer_grants_reach_workers_and_do_not_leak_to_other_requests():
    async def inspect_grants(_request):
        return JSONResponse(await anyio.to_thread.run_sync(client_authorization_status))

    app = Starlette(
        routes=[Route("/mcp", inspect_grants), Route("/operator/test", inspect_grants)]
    )
    app.add_middleware(
        server.BearerAuthMiddleware,
        auth_token=PRIMARY_TOKEN,
        operator_token=OPERATOR_TOKEN,
        restricted_auth_token=RESTRICTED_TOKEN,
    )
    with TestClient(app) as client:
        restricted = client.get(
            "/mcp", headers={"Authorization": "Bearer " + RESTRICTED_TOKEN}
        )
        assert restricted.status_code == 200
        assert restricted.json()["restricted"] is True
        assert "real_write" not in restricted.json()["permissions"]
        primary = client.get(
            "/mcp", headers={"Authorization": "Bearer " + PRIMARY_TOKEN}
        )
        assert primary.json() == {
            "restricted": False,
            "permissions": sorted(CLIENT_PERMISSIONS),
        }
        assert (
            client.get(
                "/operator/test",
                headers={"Authorization": "Bearer " + RESTRICTED_TOKEN},
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/mcp", headers={"Authorization": "Bearer " + OPERATOR_TOKEN}
            ).status_code
            == 401
        )


@pytest.fixture
def scoped_client(monkeypatch, mcp_app_context):
    monkeypatch.setattr(server, "get_services", lambda: mcp_app_context)
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    with TestClient(
        server.create_streamable_http_app(
            PRIMARY_TOKEN, restricted_auth_token=RESTRICTED_TOKEN
        )
    ) as client:
        yield client, mcp_app_context
    server.mcp._session_manager = None


def rpc(client, method, params, token=RESTRICTED_TOKEN):
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    assert response.status_code == 200
    return response.json()["result"]


def test_real_backend_rejects_restricted_live_writes_and_allows_paper(scoped_client):
    client, context = scoped_client
    arguments = {
        "code": "US.AAPL",
        "price": 10.0,
        "qty": 1,
        "trd_side": "BUY",
        "trd_env": "REAL",
    }
    denied = rpc(client, "tools/call", {"name": "place_order", "arguments": arguments})
    assert denied["isError"] is True
    assert denied["structuredContent"]["error_code"] == "CLIENT_PERMISSION_DENIED"
    context.trade_service.place_order.assert_not_called()

    def paper_receipt(**kwargs):
        assert current_client_grants() is not None
        return {"trd_env": kwargs["trd_env"], "acc_id": "123", "state": "ACKNOWLEDGED"}

    context.trade_service.place_order.side_effect = paper_receipt
    allowed = rpc(
        client,
        "tools/call",
        {
            "name": "place_order",
            "arguments": arguments | {"trd_env": "SIMULATE", "price": "10.00"},
        },
    )
    assert not allowed.get("isError", False)
    assert json.loads(allowed["content"][0]["text"])["trd_env"] == "SIMULATE"
    context.trade_service.place_order.assert_called_once()


def test_tool_discovery_hides_gateway_control_for_restricted_client(scoped_client):
    client, _ = scoped_client
    restricted = {tool["name"] for tool in rpc(client, "tools/list", {})["tools"]}
    primary = {
        tool["name"] for tool in rpc(client, "tools/list", {}, PRIMARY_TOKEN)["tools"]
    }
    assert "place_order" in restricted
    assert "unlock_trade" not in restricted
    assert "lock_trade" not in restricted
    assert "unlock_trade" in primary


def test_health_reports_current_credential_grants(scoped_client):
    client, context = scoped_client
    context.moomoo_service.start_health_check.return_value = SimpleNamespace(
        futures=(),
        remaining=lambda: 5,
        result=lambda: {"status": "connected", "trading_mode": "REAL"},
    )
    for method in (
        context.trade_service.start_journal_health,
        context.trade_service.start_real_journal_health,
    ):
        future = Future()
        future.set_result({"state": "READY"})
        method.return_value = future
    context.trade_service.collect_journal_health.return_value = {"state": "READY"}
    context.trade_service.collect_real_journal_health.return_value = {"state": "READY"}
    result = rpc(client, "tools/call", {"name": "check_health", "arguments": {}})
    grant = result["structuredContent"]["client_authorization"]
    assert grant["restricted"] is True
    assert "simulate_write" in grant["permissions"]
    assert "real_write" not in grant["permissions"]
    assert "gateway_control" not in grant["permissions"]


@pytest.mark.parametrize(
    "restricted", [PRIMARY_TOKEN, OPERATOR_TOKEN, "has space", "nonascii-é"]
)
def test_restricted_credentials_must_be_distinct_and_ascii(restricted):
    with pytest.raises(ValueError):
        server.create_streamable_http_app(
            PRIMARY_TOKEN,
            restricted_auth_token=restricted,
            operator_auth_token=OPERATOR_TOKEN,
            operator_id="fixture-operator",
        )
