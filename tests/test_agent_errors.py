"""Shared wire errors guide reads while preserving order outcome uncertainty."""

import json

import pytest

import moomoo_mcp.server as server
from moomoo_mcp.services.execution_store import ExecutionConflict, ExecutionStoreError
from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderOutcomeUnknownError,
    OrderReceiptUnreadableError,
)
from moomoo_mcp.services.query_contracts import TradeQueryError
from moomoo_mcp.services.quote_errors import QuotePermissionDeniedError
from moomoo_mcp.services.rate_limit import ProviderRateLimitError, RateLimitPolicy
from moomoo_mcp.services.sdk_response import SDKResponseError
from moomoo_mcp.services.trading_controls import TradingControlError
from moomoo_mcp.services.trading_policy import TradingPolicyError
from moomoo_mcp.tools.annotations import CONSEQUENTIAL_TOOL
from moomoo_mcp.tools.serialization import IdentifierSerializationError
from tests import test_market_data_permissions
from tests.test_market_data_permissions import call

gateway_client = test_market_data_permissions.gateway_client


def failure(result):
    assert result["isError"] is True
    data = json.loads(result["content"][0]["text"])
    assert result["structuredContent"] == data
    assert data["tool"]
    assert data["operation"]
    assert data["checked_at"].endswith("Z")
    assert data["next_action"]
    return data


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValueError("invalid selected account"), "INVALID_ARGUMENT"),
        (ConnectionError("connection lost"), "CONNECTION_UNAVAILABLE"),
        (IdentifierSerializationError("invalid identifier"), "INVALID_RESPONSE"),
        (
            TradeQueryError("CURSOR_EXPIRED", "get_orders_page", "expired"),
            "CURSOR_EXPIRED",
        ),
        (TradeQueryError("CURSOR_SCOPE", "get_orders_page", "scope"), "CURSOR_SCOPE"),
        (TradeQueryError("BROKER_ERROR", "get_orders_page", "failed"), "BROKER_ERROR"),
        (TradingPolicyError("read only"), "TRADING_POLICY_REFUSED"),
        (TradingControlError("approval required"), "TRADING_CONTROLS_BLOCKED"),
        (ExecutionConflict("identity mismatch"), "EXECUTION_CONFLICT"),
        (ExecutionStoreError("blocked"), "EXECUTION_JOURNAL_BLOCKED"),
        (RuntimeError("unexpected gateway failure"), "TOOL_EXECUTION_FAILED"),
    ],
)
def test_shared_errors_across_account_queries(gateway_client, error, code):
    client, _, services = gateway_client
    services.trade_service.get_assets.side_effect = error
    data = failure(call(client, "get_assets", {}))
    assert data["error_code"] == code
    assert data["retryable"] is False
    assert "cash" not in data


def test_rate_limit_preserves_timing_for_bounded_read_retry(gateway_client):
    client, _, services = gateway_client
    services.trade_service.get_assets.side_effect = ProviderRateLimitError(
        RateLimitPolicy("Moomoo", "accinfo_query", 10, 30), 2.1
    )
    data = failure(call(client, "get_assets", {}))
    assert data["error_code"] == "RATE_LIMITED"
    assert data["retryable"] is True
    assert data["retry_after_seconds"] == 3
    assert data["operation"] == "accinfo_query"
    services.trade_service.get_assets.assert_called_once()


@pytest.mark.parametrize(
    ("outer", "inner", "code", "outcome"),
    [
        (
            OrderNotSentError("not sent"),
            QuotePermissionDeniedError(
                "get_market_snapshot", "No quote permission", ["SG.XND"]
            ),
            "ORDER_NOT_SENT",
            "NOT_SENT",
        ),
        (
            OrderNotSentError("not sent"),
            ProviderRateLimitError(RateLimitPolicy("Moomoo", "place_order", 15, 30), 5),
            "ORDER_NOT_SENT",
            "NOT_SENT",
        ),
        (
            OrderOutcomeUnknownError("unknown"),
            ValueError("bad response"),
            "ORDER_OUTCOME_UNKNOWN",
            "UNKNOWN",
        ),
        (
            OrderReceiptUnreadableError("acknowledged"),
            ValueError("bad receipt"),
            "ORDER_RECEIPT_UNREADABLE",
            "ACKNOWLEDGED",
        ),
    ],
)
def test_outer_order_outcome_overrides_nested_errors(
    gateway_client, outer, inner, code, outcome
):
    client, _, services = gateway_client
    outer.__cause__ = inner
    services.trade_service.place_order.side_effect = outer
    data = failure(
        call(
            client,
            "place_order",
            {
                "code": "US.AAPL",
                "qty": 1,
                "price": 100,
                "trd_side": "BUY",
                "trd_env": "SIMULATE",
            },
        )
    )
    assert data["error_code"] == code
    assert data["mutation_outcome"] == outcome
    assert data["retryable"] is False
    assert "another available market-data" not in json.dumps(data)
    services.trade_service.place_order.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("lost response"),
        IdentifierSerializationError("unreadable identifier"),
        SDKResponseError("unexpected response shape"),
    ],
)
def test_unknown_mutation_error_never_encourages_resend(gateway_client, error):
    client, _, services = gateway_client
    services.trade_service.place_order.side_effect = error
    data = failure(
        call(
            client,
            "place_order",
            {
                "code": "US.AAPL",
                "qty": 1,
                "price": 100,
                "trd_side": "BUY",
                "trd_env": "SIMULATE",
            },
        )
    )
    assert data["error_code"] in {"ORDER_OUTCOME_UNKNOWN", "INVALID_RESPONSE"}
    assert data["mutation_outcome"] == "UNKNOWN"
    assert data["retryable"] is False
    assert "Do not resend" in data["next_action"]


def test_unknown_unlock_outcome_guides_gateway_lock_inspection(gateway_client):
    client, _, services = gateway_client
    services.trade_service.unlock_trade.side_effect = RuntimeError("lost response")
    data = failure(call(client, "unlock_trade", {"password": "synthetic-password"}))
    assert data["error_code"] == "MUTATION_OUTCOME_UNKNOWN"
    assert data["mutation_outcome"] == "UNKNOWN"
    assert data["retryable"] is False
    assert "lock_trade" in data["next_action"]
    assert "broker orders" not in data["next_action"]


def test_structured_unknown_outcome_is_preserved_as_success(gateway_client):
    client, _, services = gateway_client
    outcome = {
        "state": "UNKNOWN_OUTCOME",
        "operation_id": "original",
        "sent": "POSSIBLY_SENT",
    }
    services.trade_service.place_order.return_value = outcome
    result = call(
        client,
        "place_order",
        {
            "code": "US.AAPL",
            "qty": 1,
            "price": 100,
            "trd_side": "BUY",
            "trd_env": "SIMULATE",
        },
    )
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == outcome
    assert "structuredContent" not in result


def test_unreadable_order_identifier_after_service_success_is_uncertain(gateway_client):
    client, _, services = gateway_client
    services.trade_service.place_order.return_value = {"order_id": 1.5}
    data = failure(
        call(
            client,
            "place_order",
            {
                "code": "US.AAPL",
                "qty": 1,
                "price": 100,
                "trd_side": "BUY",
                "trd_env": "SIMULATE",
            },
        )
    )
    assert data["error_code"] == "INVALID_RESPONSE"
    assert data["mutation_outcome"] == "UNKNOWN"
    assert "Do not resend" in data["next_action"]


def test_invalid_sensitive_arguments_do_not_echo_credentials(gateway_client):
    client, _, services = gateway_client
    secret = "synthetic-private-password"
    result = call(client, "unlock_trade", {"password": {"secret": secret}})
    data = failure(result)
    assert data["error_code"] == "INVALID_ARGUMENT"
    assert secret not in json.dumps(result)
    services.trade_service.unlock_trade.assert_not_called()


def test_unknown_tool_has_discovery_action(gateway_client):
    client, _, _ = gateway_client
    data = failure(call(client, "nonexistent_tool", {}))
    assert data["error_code"] == "UNKNOWN_TOOL"
    assert "tools/list" in data["next_action"]


def test_output_validation_after_mutation_preserves_uncertainty(gateway_client):
    client, _, _ = gateway_client
    name = "_test_unreadable_mutation_output"

    @server.mcp.tool(name=name, annotations=CONSEQUENTIAL_TOOL)
    async def invalid_output() -> dict[str, int]:
        return {"order_id": "not-an-integer"}  # type: ignore[return-value]

    try:
        data = failure(call(client, name, {}))
        assert data["error_code"] == "INVALID_RESPONSE"
        assert data["mutation_outcome"] == "UNKNOWN"
        assert data["retryable"] is False
        assert "Do not resend" in data["next_action"]
        assert "not-an-integer" not in json.dumps(data)
    finally:
        server.mcp._tool_manager.remove_tool(name)
