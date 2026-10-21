"""Shared wire errors guide reads while preserving order outcome uncertainty."""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError, create_model

import moomoo_mcp.server as server
from moomoo_mcp.agent_server import tool_error_result
from moomoo_mcp.services.execution_identity import (
    AdmissionEpoch,
    IdentityTokenValidationError,
    OperationId,
)
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
from tests.mcp_helpers import call


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


@pytest.mark.parametrize(
    ("arguments", "field", "constraint", "actual_length"),
    [
        (
            {"operation_id": "x" * 66, "admission_epoch": "epoch"},
            "operation_id",
            "max_length",
            66,
        ),
        (
            {"operation_id": "original", "admission_epoch": "  "},
            "admission_epoch",
            "nonblank",
            2,
        ),
        (
            {"operation_id": "original", "admission_epoch": "bad\nepoch"},
            "admission_epoch",
            "printable",
            9,
        ),
        (
            {"operation_id": 123, "admission_epoch": "epoch"},
            "operation_id",
            "strict_string",
            None,
        ),
        ({"admission_epoch": "epoch"}, "operation_id", "required", None),
    ],
)
def test_input_identity_diagnostics_are_typed_sanitized_and_invocation_scoped(
    arguments, field, constraint, actual_length
):
    model = create_model(
        "TokenInput",
        operation_id=(OperationId, ...),
        admission_epoch=(AdmissionEpoch, ...),
    )
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(arguments)
    error = ToolError("redacted input validation")
    error.__cause__ = excinfo.value
    result = tool_error_result(
        error,
        "place_order",
        input_model="TokenInput",
        consequential=True,
        read_only=False,
        input_validation=True,
    )
    data = result.structuredContent
    assert data is not None
    assert data["error_code"] == "INVALID_ARGUMENT"
    assert data["mutation_outcome"] == "NOT_SENT"
    assert data["validation_phase"] == "INPUT_VALIDATION"
    assert data["journal_record_created"] is False
    assert data["journal_record_scope"] == "THIS_INVOCATION"
    assert data["identity_status"] == "UNKNOWN"
    assert data["prior_attempt_outcome"] == "UNKNOWN"
    assert data["retryable"] is False
    issue = data["validation_issues"][0]
    assert issue["field"] == field
    assert issue["constraint"] == constraint
    assert issue["min_length"] == 1
    assert issue["max_length"] == 64
    assert issue.get("actual_length") == actual_length
    assert "input" not in issue and "ctx" not in issue and "msg" not in issue
    assert "submit-once" in data["next_action"]
    assert "exact operation ID" in data["next_action"]
    assert "cannot transfer" in data["next_action"]
    assert "x" * 66 not in json.dumps(data)


@pytest.mark.parametrize("admitted", [False, True])
def test_runtime_identity_rejection_does_not_erase_admission(admitted):
    token_error = IdentityTokenValidationError("operation_id", "max_length", 66)
    refusal = OrderNotSentError("token refused", admitted=admitted)
    refusal.__cause__ = token_error
    error = ToolError("redacted refusal")
    error.__cause__ = refusal
    result = tool_error_result(
        error,
        "place_order",
        input_model="TokenInput",
        consequential=True,
        read_only=False,
    )
    data = result.structuredContent
    assert data is not None
    assert data["error_code"] == "ORDER_NOT_SENT"
    assert data["validation_phase"] == ("ADMITTED" if admitted else "INPUT_VALIDATION")
    assert data["journal_record_created"] is admitted
    assert data["identity_status"] == "UNKNOWN"
    assert data["retryable"] is False
    if admitted:
        assert "For a confirmed first attempt" not in data["next_action"]


@pytest.mark.parametrize(
    ("outer", "expected_code", "phase"),
    [
        (OrderOutcomeUnknownError("unknown"), "ORDER_OUTCOME_UNKNOWN", "DISPATCHED"),
        (
            OrderReceiptUnreadableError("acknowledged"),
            "ORDER_RECEIPT_UNREADABLE",
            "ACKNOWLEDGED",
        ),
    ],
)
def test_dispatch_evidence_wins_over_nested_identity_rejection(
    outer, expected_code, phase
):
    outer.__cause__ = IdentityTokenValidationError("operation_id", "max_length", 66)
    error = ToolError("redacted failure")
    error.__cause__ = outer
    result = tool_error_result(
        error,
        "place_order",
        input_model="TokenInput",
        consequential=True,
        read_only=False,
    )
    data = result.structuredContent
    assert data is not None
    assert data["error_code"] == expected_code
    assert data["validation_phase"] == phase
    assert data["journal_record_created"] is None
    assert data["identity_status"] == "UNKNOWN"
    assert data["retryable"] is False
    assert "validation_issues" not in data
    assert "Do not resend" in data["next_action"]


def test_service_validation_error_using_input_model_does_not_claim_non_dispatch(
    gateway_client,
):
    client, _, services = gateway_client
    tool = server.mcp._tool_manager.get_tool("place_order")
    assert tool is not None
    with pytest.raises(ValidationError) as excinfo:
        tool.fn_metadata.arg_model.model_validate({})
    services.trade_service.place_order.side_effect = excinfo.value
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
    assert data["validation_phase"] == "UNKNOWN"
    assert data["journal_record_created"] is None
    assert data["identity_status"] == "UNKNOWN"
    assert "For a confirmed first attempt" not in data["next_action"]
    assert "Do not resend" in data["next_action"]
    services.trade_service.place_order.assert_called_once()


def test_direct_typed_identity_error_after_service_call_keeps_unknown_outcome(
    gateway_client,
):
    client, _, services = gateway_client
    services.trade_service.place_order.side_effect = IdentityTokenValidationError(
        "operation_id", "max_length", 66
    )
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
    assert data["error_code"] == "ORDER_OUTCOME_UNKNOWN"
    assert data["mutation_outcome"] == "UNKNOWN"
    assert data["validation_phase"] == "UNKNOWN"
    assert data["journal_record_created"] is None
    assert "For a confirmed first attempt" not in data["next_action"]
    services.trade_service.place_order.assert_called_once()


@pytest.mark.parametrize("token", ["null", "[]", "{}", "true", '"quoted"'])
def test_wire_mcp_preserves_json_shaped_opaque_tokens(gateway_client, token):
    client, _, services = gateway_client
    services.trade_service.place_order.return_value = {"order_id": "77"}
    result = call(
        client,
        "place_order",
        {
            "code": "US.AAPL",
            "qty": 1,
            "price": 100,
            "trd_side": "BUY",
            "trd_env": "SIMULATE",
            "operation_id": token,
            "admission_epoch": token,
        },
    )
    assert result["isError"] is False
    sent = services.trade_service.place_order.call_args.kwargs
    assert sent["operation_id"] == token
    assert sent["admission_epoch"] == token


@pytest.mark.parametrize("token", ["null", "[]", "{}", "true", '"quoted"'])
async def test_direct_mcp_preserves_json_shaped_opaque_tokens(
    call_tool, mock_trade_service, token
):
    mock_trade_service.place_order.return_value = {"order_id": "77"}
    tool = server.mcp._tool_manager.get_tool("place_order")
    assert tool is not None
    parsed = tool.fn_metadata.pre_parse_json(
        {"operation_id": token, "admission_epoch": token}
    )
    assert parsed == {"operation_id": token, "admission_epoch": token}
    # The mock-backed helper calls mcp.call_tool directly, bypassing the HTTP
    # error adapter and its explicit input preflight.
    result = await call_tool(
        "place_order",
        {
            "code": "US.AAPL",
            "qty": 1,
            "price": 100,
            "trd_side": "BUY",
            "trd_env": "SIMULATE",
            "operation_id": token,
            "admission_epoch": token,
        },
    )
    assert result is not None
    passed = mock_trade_service.place_order.call_args.kwargs
    assert passed["operation_id"] == token
    assert passed["admission_epoch"] == token
