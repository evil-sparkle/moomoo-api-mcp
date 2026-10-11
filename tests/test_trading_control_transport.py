"""Trusted operator transport, configuration and MCP review boundaries.

SQLite stores are local temporary fixtures; every broker service is fake.
"""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.services.order_errors import OrderNotSentError
from moomoo_mcp.services.real_execution import RealExecution
from moomoo_mcp.services.trade_preparation import mutation_context
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_controls import TradingControlError, TradingControls
from moomoo_mcp.services.trading_policy import (
    TradingMode,
    TradingModeConfigError,
    TradingPolicy,
)
from moomoo_mcp.settings import load_settings

MCP_TOKEN = "ordinary-mcp-test-token"
OPERATOR_TOKEN = "separate-operator-test-token"
OPERATOR_ID = "configured-test-operator"
ACCOUNT = "987654321098765431"


@pytest.fixture(autouse=True)
def isolated_transport():
    security = server.mcp.settings.transport_security
    server.mcp._session_manager = None
    yield
    server.mcp._session_manager = None
    server.mcp.settings.transport_security = security


def settings_environment(tmp_path, mode="SIMULATE"):
    values = {
        "MCP_AUTH_TOKEN": MCP_TOKEN,
        "MOOMOO_TRADING_MODE": mode,
        "MOOMOO_SIMULATED_ACC_IDS": "123",
        "MOOMOO_JOURNAL_PATH": str(tmp_path / "paper" / "execution.db"),
    }
    if mode == "REAL":
        values.update(
            MOOMOO_REAL_ACC_IDS="456",
            MOOMOO_REAL_JOURNAL_PATH=str(tmp_path / "real" / "execution.db"),
        )
    return values


@pytest.mark.parametrize(
    "configuration",
    [
        {"MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:100"},
        {
            "MOOMOO_OPERATOR_AUTH_TOKEN": OPERATOR_TOKEN,
            "MOOMOO_OPERATOR_ID": OPERATOR_ID,
        },
        {
            "MOOMOO_REQUIRE_TRADE_APPROVAL": "1",
            "MOOMOO_OPERATOR_AUTH_TOKEN": OPERATOR_TOKEN,
            "MOOMOO_OPERATOR_ID": OPERATOR_ID,
        },
    ],
)
def test_controls_settings_require_explicit_storage(tmp_path, configuration):
    with pytest.raises(TradingModeConfigError) as error:
        load_settings(settings_environment(tmp_path) | configuration)
    assert "MOOMOO_TRADING_CONTROLS_PATH" in str(error.value)


def test_approval_requires_separate_operator_channel(tmp_path):
    values = settings_environment(tmp_path) | {
        "MOOMOO_REQUIRE_TRADE_APPROVAL": "1",
        "MOOMOO_TRADING_CONTROLS_PATH": str(tmp_path / "controls.db"),
    }
    with pytest.raises(TradingModeConfigError) as error:
        load_settings(values)
    assert "MOOMOO_OPERATOR_AUTH_TOKEN" in str(error.value)


def test_operator_token_without_mcp_auth_fails_settings_before_broker(tmp_path):
    values = settings_environment(tmp_path) | {
        "MOOMOO_TRADING_CONTROLS_PATH": str(tmp_path / "controls.db"),
        "MOOMOO_OPERATOR_AUTH_TOKEN": OPERATOR_TOKEN,
        "MOOMOO_OPERATOR_ID": OPERATOR_ID,
    }
    values.pop("MCP_AUTH_TOKEN")
    with (
        patch.object(server, "MoomooService") as quotes,
        patch.object(server, "TradeService") as trades,
        pytest.raises(TradingModeConfigError) as error,
    ):
        load_settings(values)
    assert "MCP_AUTH_TOKEN" in str(error.value)
    quotes.assert_not_called()
    trades.assert_not_called()


@pytest.mark.parametrize("journal", ["MOOMOO_JOURNAL_PATH", "MOOMOO_REAL_JOURNAL_PATH"])
def test_controls_cannot_alias_environment_journal(tmp_path, journal):
    values = settings_environment(tmp_path, "REAL")
    values["MOOMOO_TRADING_CONTROLS_PATH"] = values[journal]
    with pytest.raises(TradingModeConfigError) as error:
        load_settings(values)
    assert "distinct storage" in str(error.value)


@pytest.mark.parametrize(
    ("operator_token", "operator_id"),
    [
        (MCP_TOKEN, OPERATOR_ID),
        ("has whitespace", OPERATOR_ID),
        ("nonascii-令牌", OPERATOR_ID),
        (OPERATOR_TOKEN, ""),
        (OPERATOR_TOKEN, "x" * 129),
        (OPERATOR_TOKEN, "identity\nwith-newline"),
    ],
)
def test_operator_config_rejects_untrusted_credentials_and_identity(
    tmp_path, operator_token, operator_id
):
    values = settings_environment(tmp_path) | {
        "MOOMOO_TRADING_CONTROLS_PATH": str(tmp_path / "controls.db"),
        "MOOMOO_OPERATOR_AUTH_TOKEN": operator_token,
        "MOOMOO_OPERATOR_ID": operator_id,
    }
    with pytest.raises(TradingModeConfigError):
        load_settings(values)


def test_read_only_ignores_controls_storage_and_creation_without_opening(tmp_path):
    path = tmp_path / "must-not-exist" / "controls.db"
    settings = load_settings(
        {
            "MOOMOO_TRADING_MODE": "READ_ONLY",
            "MOOMOO_TRADING_CONTROLS_PATH": str(path),
            "MOOMOO_CREATE_TRADING_CONTROLS": "invalid-but-unused",
        }
    )
    assert settings.controls_path is None
    assert settings.create_controls is False
    with (
        patch.object(server, "TradingControls") as controls,
        patch.object(server, "MoomooService"),
        patch.object(server, "TradeService"),
        patch.object(server, "MarketDataService"),
    ):
        context = server._build_services(settings)
        context.trade_service.close()
        context.moomoo_service.close()
    controls.assert_not_called()
    assert not path.exists()


def test_missing_controls_storage_fails_before_broker_construction(tmp_path):
    values = settings_environment(tmp_path) | {
        "MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY": "USD:100",
        "MOOMOO_TRADING_CONTROLS_PATH": str(tmp_path / "uninitialized-controls.db"),
    }
    settings = load_settings(values)
    with (
        patch.object(server, "_open_execution_stores", return_value=(None, None)),
        patch.object(server, "MoomooService") as quotes,
        patch.object(server, "TradeService") as trades,
        pytest.raises(TradingControlError),
    ):
        server._build_services(settings)
    quotes.assert_not_called()
    trades.assert_not_called()


def test_metadata_only_controls_database_is_not_ready(tmp_path):
    path = tmp_path / "incomplete-controls.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE metadata (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES (1)")
    with pytest.raises(TradingControlError) as error:
        TradingControls(path)
    assert "storage is unavailable" in str(error.value)


@pytest.fixture
def review_store(tmp_path):
    clock = [1000.0]
    store = TradingControls(
        tmp_path / "controls.db", create=True, clock=lambda: clock[0]
    )
    parameters = {
        "order_id": "12345",
        "trd_env": "REAL",
        "acc_id": ACCOUNT,
        "operation_id": "stable-cancel-operation",
        "admission_epoch": "fixture-admission-epoch",
    }
    request = store.prepare(
        "cancel_order", parameters, {"target": {"order_id": "12345"}}
    )
    yield store, request, clock
    store.close()


def operator_app():
    return server.create_streamable_http_app(
        auth_token=MCP_TOKEN,
        allow_chatgpt_tunnel_host=False,
        operator_auth_token=OPERATOR_TOKEN,
        operator_id=OPERATOR_ID,
    )


def headers(token=OPERATOR_TOKEN):
    return {"Authorization": f"Bearer {token}", "Host": "127.0.0.1:8000"}


def fake_context(store):
    return SimpleNamespace(
        trade_service=SimpleNamespace(trading_controls=store, trade_ctx=MagicMock())
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("credential", [None, MCP_TOKEN, "wrong-token", ""])
def test_operator_routes_refuse_nonoperator_before_services(method, credential):
    request_headers = {"Host": "127.0.0.1:8000"}
    if credential is not None:
        request_headers["Authorization"] = f"Bearer {credential}"
    with (
        patch.object(server, "get_services") as services,
        TestClient(operator_app()) as client,
    ):
        response = client.request(
            method,
            "/operator/approvals/fixture-request",
            headers=request_headers,
            json={"decision": "APPROVED", "reason": "reviewed"}
            if method == "POST"
            else None,
        )
    assert response.status_code == 401
    services.assert_not_called()


def test_disabled_operator_route_is_not_exposed_and_empty_bearer_is_not_valid():
    app = server.create_streamable_http_app(
        auth_token=MCP_TOKEN, allow_chatgpt_tunnel_host=False
    )
    with patch.object(server, "get_services") as services, TestClient(app) as client:
        absent = client.get(
            "/operator/approvals/fixture-request", headers=headers(MCP_TOKEN)
        )
        empty = client.get("/operator/approvals/fixture-request", headers=headers(""))
    assert absent.status_code == 404
    assert empty.status_code == 401
    services.assert_not_called()


def test_operator_get_post_audit_identity_and_decision_are_immutable(review_store):
    store, prepared, clock = review_store
    context = fake_context(store)
    target = "/operator/approvals/" + prepared["request_id"]
    with (
        patch.object(server, "get_services", return_value=context),
        TestClient(operator_app()) as client,
    ):
        before = client.get(target, headers=headers())
        assert before.json()["status"] == "PENDING"
        assert before.json()["method"] == "cancel_order"
        assert "method" not in before.json()["parameters"]
        assert before.json()["parameters"]["acc_id"] == ACCOUNT
        clock[0] += 1
        decision = client.post(
            target,
            headers=headers(),
            json={"decision": "APPROVED", "reason": "Independent operator review"},
        )
        assert decision.status_code == 200
        result = decision.json()
        assert result["operator_id"] == OPERATOR_ID
        assert result["decided_at"] == clock[0]
        assert result["fingerprint"] == before.json()["fingerprint"]
        assert result["reason"] == "Independent operator review"
        repeated = client.post(
            target,
            headers=headers(),
            json={"decision": "REJECTED", "reason": "Attempted revision"},
        )
        assert repeated.status_code == 409
        final = client.get(target, headers=headers()).json()
    assert final["status"] == "APPROVED"
    assert final["reason"] == result["reason"]
    context.trade_service.trade_ctx.unlock_trade.assert_not_called()
    context.trade_service.trade_ctx.place_order.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "APPROVED"},
        {
            "decision": "APPROVED",
            "reason": "Reviewed",
            "operator_id": "caller-identity",
        },
        {"decision": "APPROVED", "reason": "Reviewed", "approved": True},
        {"decision": True, "reason": "Reviewed"},
        {"decision": "approved", "reason": "Reviewed"},
        {"decision": "APPROVED", "reason": " "},
        {"decision": "APPROVED", "reason": "x" * 2001},
        ["APPROVED", "Reviewed"],
    ],
)
def test_operator_decision_exact_fields_and_limits_fail_closed(review_store, payload):
    store, prepared, _ = review_store
    with (
        patch.object(server, "get_services", return_value=fake_context(store)),
        TestClient(operator_app()) as client,
    ):
        response = client.post(
            "/operator/approvals/" + prepared["request_id"],
            headers=headers(),
            json=payload,
        )
    assert response.status_code == 409
    assert store.get(prepared["request_id"])["status"] == "PENDING"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    ("extra_headers", "status"),
    [
        ({"Host": "untrusted.example"}, 421),
        ({"Origin": "https://untrusted.example"}, 403),
    ],
)
def test_operator_preserves_host_origin_protection(method, extra_headers, status):
    with (
        patch.object(server, "get_services") as services,
        TestClient(operator_app()) as client,
    ):
        response = client.request(
            method,
            "/operator/approvals/fixture-request",
            headers=headers() | extra_headers,
            json={"decision": "APPROVED", "reason": "Reviewed"}
            if method == "POST"
            else None,
        )
    assert response.status_code == status
    services.assert_not_called()


def test_operator_body_bound_is_checked_before_decision(review_store):
    store, prepared, _ = review_store
    payload = json.dumps({"decision": "APPROVED", "reason": "x" * 9000})
    with (
        patch.object(server, "get_services", return_value=fake_context(store)),
        TestClient(operator_app()) as client,
    ):
        response = client.post(
            "/operator/approvals/" + prepared["request_id"],
            headers=headers() | {"Content-Type": "application/json"},
            content=payload,
        )
    assert response.status_code == 413
    assert store.get(prepared["request_id"])["status"] == "PENDING"


def test_expired_request_cannot_receive_operator_decision(review_store):
    store, prepared, clock = review_store
    clock[0] = prepared["expires_at"]
    with (
        patch.object(server, "get_services", return_value=fake_context(store)),
        TestClient(operator_app()) as client,
    ):
        target = "/operator/approvals/" + prepared["request_id"]
        assert client.get(target, headers=headers()).json()["status"] == "EXPIRED"
        response = client.post(
            target,
            headers=headers(),
            json={"decision": "APPROVED", "reason": "Too late"},
        )
    assert response.status_code == 409
    assert store.get(prepared["request_id"])["operator_id"] is None


def test_missing_controls_operator_store_returns_unavailable_without_broker():
    context = fake_context(None)
    with (
        patch.object(server, "get_services", return_value=context),
        TestClient(operator_app()) as client,
    ):
        response = client.get("/operator/approvals/fixture-request", headers=headers())
    assert response.status_code == 503
    context.trade_service.trade_ctx.unlock_trade.assert_not_called()
    context.trade_service.trade_ctx.place_order.assert_not_called()


def test_approved_closed_storage_blocks_real_dispatch_before_unlock(review_store):
    store, prepared, _ = review_store
    store.decide(
        prepared["request_id"],
        "APPROVED",
        operator_id=OPERATOR_ID,
        reason="Reviewed cancellation",
    )
    service = TradeService(
        policy=TradingPolicy(
            TradingMode.REAL,
            real_acc_ids=frozenset({int(ACCOUNT)}),
            require_approval=True,
        ),
        trade_password="test-only-trade-password",
        trading_controls=store,
    )
    context = MagicMock()
    service.trade_ctx = context
    service.real = MagicMock(spec=RealExecution)
    store.close()
    with (
        mutation_context(
            service, "cancel_order", prepared["parameters"], prepared["request_id"]
        ),
        pytest.raises(OrderNotSentError) as error,
    ):
        service._dispatch_write(
            "REAL",
            "cancel_order",
            context.modify_order,
            lambda data: data,
            adds_exposure=False,
            sdk_operation="modify_order",
            account_id=int(ACCOUNT),
            journal_request={},
        )
    assert "Trading controls storage is blocked" in str(error.value)
    context.unlock_trade.assert_not_called()
    context.modify_order.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_review_tools_do_not_offer_an_approval_mutation():
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert not any(
        name.startswith("approve") or name.startswith("decide") for name in tools
    )
    prepare = tools["prepare_trade_request"].annotations
    preview = tools["preview_order"].annotations
    status = tools["get_prepared_trade"].annotations
    assert prepare is not None and prepare.readOnlyHint is False
    assert prepare.destructiveHint is False
    assert preview is not None and preview.readOnlyHint is True
    assert status is not None and status.readOnlyHint is True


@pytest.mark.asyncio
async def test_prepare_tool_forwards_exact_original_parameters(
    call_tool, mock_trade_service
):
    parameters = {
        "order_id": "12345",
        "trd_env": "REAL",
        "acc_id": ACCOUNT,
        "operation_id": "stable-operation",
        "admission_epoch": "original-epoch",
    }
    mock_trade_service.prepare_trade_request.return_value = {
        "request_id": "request-fixture",
        "method": "cancel_order",
        "parameters": parameters,
    }
    result = await call_tool(
        "prepare_trade_request",
        {"method": "cancel_order", "parameters": parameters, "ttl_seconds": 90},
    )
    assert mock_trade_service.prepare_trade_request.called, result.text
    assert mock_trade_service.prepare_trade_request.call_args.kwargs == {
        "method": "cancel_order",
        "parameters": parameters,
        "ttl_seconds": 90,
    }
    assert result.json["parameters"] == parameters
    mock_trade_service.cancel_order.assert_not_called()
    mock_trade_service.unlock_trade.assert_not_called()


@pytest.mark.asyncio
async def test_preview_tool_forwards_exact_session_price_and_account(
    call_tool, mock_trade_service
):
    mock_trade_service.preview_order.return_value = {
        "acc_id": int(ACCOUNT),
        "maximum_quantities": {},
    }
    arguments = {
        "code": "US.AAPL",
        "price": "123.45",
        "qty": 3,
        "trd_side": "BUY",
        "trd_env": "SIMULATE",
        "acc_id": ACCOUNT,
        "session": "RTH",
    }
    result = await call_tool("preview_order", arguments)
    assert mock_trade_service.preview_order.called, result.text
    passed = mock_trade_service.preview_order.call_args.kwargs
    assert all(passed[name] == value for name, value in arguments.items())
    assert passed["order_type"] == "NORMAL" and passed["time_in_force"] == "DAY"
    assert result.json["acc_id"] == ACCOUNT
    mock_trade_service.place_order.assert_not_called()
    mock_trade_service.unlock_trade.assert_not_called()
