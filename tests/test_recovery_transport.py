"""Durable recovery context crosses the authenticated stateless MCP boundary."""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.paper_execution import PaperExecution
from moomoo_mcp.services.trade_service import TradeService
from tests.test_services.test_paper_execution import (
    Broker,
    Service,
    place,
    recovery_clock,
)

AGENT_TOKEN = "test-agent-capability"
OBSOLETE_TOKEN = "test-obsolete-operator-capability"


@pytest.fixture
def recovery_engine(tmp_path):
    store = ExecutionStore(tmp_path / "execution.db", create=True)
    broker = Broker(tmp_path / "execution.db")
    service = Service(broker)
    paper = PaperExecution(
        cast(TradeService, cast(object, service)), store, frozenset({123})
    )
    services = SimpleNamespace(trade_service=SimpleNamespace(paper=paper))
    server.mcp._session_manager = None
    with patch.object(server, "get_services", return_value=services):
        yield paper, broker, store
    paper.recovery.stop()
    store.close()
    server.mcp._session_manager = None


def rpc(client, name, arguments=None, token: str | None = AGENT_TOKEN):
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


def payload(response):
    assert response.status_code == 200
    result = response.json()["result"]
    assert not result.get("isError", False), result
    decoded = json.loads(result["content"][0]["text"])
    if "structuredContent" in result:
        assert result["structuredContent"] == decoded
    return decoded


def test_normal_auth_returns_verified_recovery_and_operator_tool_is_removed(
    recovery_engine,
):
    paper, broker, store = recovery_engine
    broker.failure = "lost_response"
    original = place(paper)
    with TestClient(server.create_streamable_http_app(AGENT_TOKEN)) as client:
        before = payload(rpc(client, "get_execution", {"operation_id": "place"}))
        assert before["state"] == "UNKNOWN_OUTCOME"
        assert before["order_tag"] == original["order_tag"]
        recovered = payload(
            rpc(client, "reconcile_execution", {"operation_id": "place"})
        )
        assert recovered["recovery"]["disposition"] == "BROKER_CONFIRMED"
        assert recovered["broker_order_id"] == "9007199254740993"
        assert recovered["accounted_facts"]["remaining_executable_quantity"] == "2"
        assert (
            payload(rpc(client, "get_execution", {"operation_id": "place"}))
            == recovered
        )
        assert (
            rpc(
                client, "get_execution", {"operation_id": "place"}, token=None
            ).status_code
            == 401
        )
        assert (
            rpc(
                client, "get_execution", {"operation_id": "place"}, token=OBSOLETE_TOKEN
            ).status_code
            == 401
        )
        removed = rpc(
            client,
            "acknowledge_recovery",
            {"operation_id": "place", "operator_id": "operator"},
        )
        assert removed.json()["result"]["isError"]
        listing = client.post(
            "/mcp",
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + AGENT_TOKEN,
            },
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert "acknowledge_recovery" not in {
            tool["name"] for tool in listing.json()["result"]["tools"]
        }
    assert store.health()["state"] == "READY"
    assert len(broker.calls) == 1


def test_absence_policy_context_is_returned_across_stateless_requests(recovery_engine):
    paper, broker, store = recovery_engine
    now = recovery_clock(paper)
    broker.failure = "timeout"
    place(paper)
    result = {}
    with TestClient(server.create_streamable_http_app(AGENT_TOKEN)) as client:
        for _ in range(5):
            row = store.lookup("place")
            assert row is not None
            now[0] = row["next_recovery_at"]
            response = rpc(client, "reconcile_execution", {"operation_id": "place"})
            assert "mcp-session-id" not in response.headers
            result = payload(response)
    assert result["state"] == "UNKNOWN_OUTCOME"
    assert result["recovery"]["disposition"] == "ASSUMED_NOT_PLACED_AFTER_RETRIES"
    assert result["recovery"]["successful_negative_checks"] == 5
    assert result["recovery"]["absence_proven"] is False
    assert result["recovery"]["original_operation_replay_allowed"] is False
    assert result["recovery_updates"][0]["operation_id"] == "place"
    assert [check["completed_at"] for check in result["recovery_checks"]] == [
        0,
        5,
        10,
        20,
        40,
    ]
    assert store.health()["state"] == "READY"
    assert len(broker.calls) == 1
