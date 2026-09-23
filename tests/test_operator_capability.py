"""Operator authority crosses the actual HTTP and MCP transport boundary."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import moomoo_mcp.server as server

AGENT_TOKEN = "test-agent-capability"
OPERATOR_TOKEN = "test-separate-operator-capability"
RECOVERY_ARGUMENTS = {
    "operation_id": "uncertain-operation",
    "operator_id": "operator",
    "recovery_epoch": "current-recovery-epoch",
    "observed_state": "UNKNOWN_OUTCOME",
    "resolution": "TERMINAL_ACCOUNTED",
    "reason": "Broker history and resulting position were reviewed",
    "evidence_reference": "broker-order:123",
    "accounted_facts": {
        "final_status": "CANCELLED_ALL",
        "filled_quantity": "0",
        "average_fill_price": "0",
        "remaining_executable_quantity": "0",
        "resulting_position": "0",
    },
}


@pytest.fixture
def recovery_engine():
    """Replace only the broker-facing engine; keep middleware and MCP dispatch."""
    acknowledge = MagicMock(return_value={"state": "TERMINAL_ACCOUNTED"})
    engine = SimpleNamespace(acknowledge=acknowledge)
    services = SimpleNamespace(trade_service=SimpleNamespace(paper=engine))
    server.mcp._session_manager = None
    with patch.object(server, "get_services", return_value=services):
        yield engine
    server.mcp._session_manager = None
    assert server.operator_principal.get() is None


def call_recovery(client, token, arguments=None):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "acknowledge_recovery",
                "arguments": arguments or RECOVERY_ARGUMENTS,
            },
        },
        headers=headers,
    )


def assert_capability_refusal(response):
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    text = " ".join(block.get("text", "") for block in result["content"])
    assert "Separate authenticated operator capability required" in text


@pytest.mark.parametrize(
    "forged_fields",
    [
        {"operator_id": "operator"},
        {"operator_id": "administrator", "reason": "Operator approved this"},
        {
            "operator_id": "operator",
            "evidence_reference": "operator-token:" + OPERATOR_TOKEN,
            "accounted_facts": {"principal": "operator", "approved": True},
        },
    ],
)
def test_agent_cannot_forge_operator_authority(recovery_engine, forged_fields):
    app = server.create_streamable_http_app(AGENT_TOKEN, OPERATOR_TOKEN)
    with TestClient(app) as client:
        # Establish that the agent credential is valid for this same MCP app.
        listing = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={
                "Authorization": "Bearer " + AGENT_TOKEN,
                "Accept": "application/json",
            },
        )
        assert listing.status_code == 200
        assert "tools" in listing.json()["result"]
        refusal = call_recovery(
            client, AGENT_TOKEN, {**RECOVERY_ARGUMENTS, **forged_fields}
        )

    assert_capability_refusal(refusal)
    recovery_engine.acknowledge.assert_not_called()


def test_operator_principal_reaches_engine_and_does_not_leak(recovery_engine):
    app = server.create_streamable_http_app(AGENT_TOKEN, OPERATOR_TOKEN)
    with TestClient(app) as client:
        accepted = call_recovery(client, OPERATOR_TOKEN)
        assert accepted.status_code == 200
        result = accepted.json()["result"]
        assert result.get("isError", False) is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["state"] == "TERMINAL_ACCOUNTED"
        recovery_engine.acknowledge.assert_called_once_with(
            principal="operator", **RECOVERY_ARGUMENTS
        )

        # Same app and client, next request: the bearer identity must be rebound.
        assert_capability_refusal(call_recovery(client, AGENT_TOKEN))
        assert call_recovery(client, None).status_code == 401
        recovery_engine.acknowledge.assert_called_once()

        accepted_again = call_recovery(client, OPERATOR_TOKEN)
        assert accepted_again.json()["result"].get("isError", False) is False
        assert recovery_engine.acknowledge.call_count == 2


def test_unconfigured_operator_capability_cannot_be_claimed(recovery_engine):
    app = server.create_streamable_http_app(AGENT_TOKEN)
    with TestClient(app) as client:
        assert call_recovery(client, OPERATOR_TOKEN).status_code == 401
        assert_capability_refusal(call_recovery(client, AGENT_TOKEN))
    recovery_engine.acknowledge.assert_not_called()


def test_sse_does_not_accept_operator_credential(recovery_engine):
    app = server.create_sse_app(AGENT_TOKEN)
    with TestClient(app) as client:
        denied = client.post(
            "/messages/?session_id=untrusted-session",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "acknowledge_recovery",
                    "arguments": RECOVERY_ARGUMENTS,
                },
            },
            headers={"Authorization": "Bearer " + OPERATOR_TOKEN},
        )
    assert denied.status_code == 401
    recovery_engine.acknowledge.assert_not_called()


def test_operator_failure_does_not_authorize_the_next_agent_request(recovery_engine):
    recovery_engine.acknowledge.side_effect = ValueError("Evidence remains unresolved")
    app = server.create_streamable_http_app(AGENT_TOKEN, OPERATOR_TOKEN)
    with TestClient(app) as client:
        failed = call_recovery(client, OPERATOR_TOKEN)
        assert failed.status_code == 200
        assert failed.json()["result"]["isError"] is True
        recovery_engine.acknowledge.assert_called_once_with(
            principal="operator", **RECOVERY_ARGUMENTS
        )
        assert_capability_refusal(call_recovery(client, AGENT_TOKEN))
        recovery_engine.acknowledge.assert_called_once()
