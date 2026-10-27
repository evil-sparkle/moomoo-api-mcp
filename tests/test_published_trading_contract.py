"""Actual MCP tools/list contract, without broker or external-client access."""

import hashlib
import json
from concurrent.futures import Future
from unittest.mock import MagicMock

import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from moomoo_mcp.client_authorization import ClientGrants, client_grants_context
from moomoo_mcp.server import mcp
from moomoo_mcp.services.base_service import HealthCheck
from moomoo_mcp.services.execution_identity import (
    IDENTITY_TOKEN_MAX_LENGTH,
    IDENTITY_TOKEN_MIN_LENGTH,
    AdmissionEpoch,
    IdentityTokenValidationError,
    OperationId,
    validate_token,
)
from moomoo_mcp.tools.trading_contract import (
    CONTRACT_VERSION,
    MUTATIONS,
    server_contract_identity,
)
from tests.mcp_helpers import call, rpc


def token_string_schema(schema):
    return next(item for item in schema["anyOf"] if item.get("type") == "string")


def test_http_tools_list_exposes_identity_and_parameter_contract(gateway_client):
    client, _, _ = gateway_client
    tools = {tool["name"]: tool for tool in rpc(client, "tools/list", {})["tools"]}
    for name in MUTATIONS:
        schema = tools[name]["inputSchema"]
        jsonschema.Draft202012Validator.check_schema(schema)
        for field in ("operation_id", "admission_epoch"):
            token = token_string_schema(schema["properties"][field])
            assert token["minLength"] == IDENTITY_TOKEN_MIN_LENGTH
            assert token["maxLength"] == IDENTITY_TOKEN_MAX_LENGTH
            assert field in schema["required"]
            description = token["description"].casefold()
            assert "printable" in description and "nonblank" in description
            assert "original" in description or "unchanged" in description
        assert "never" in schema["properties"]["trd_env"]["description"].casefold()
        assert "decimal" in schema["properties"]["acc_id"]["description"]
    placement = tools["place_order"]["inputSchema"]["properties"]
    assert "plain decimal STRING" in placement["price"]["description"]
    assert "whole-share" in placement["qty"]["description"]
    assert "submission" in placement["session"]["description"]
    assert "broker-dependent" in placement["session"]["description"]
    for name in ("modify_order", "cancel_order"):
        assert (
            "exact"
            in tools[name]["inputSchema"]["properties"]["order_id"]["description"]
        )


def test_machine_readable_paper_contract_is_conditional(gateway_client):
    client, _, _ = gateway_client
    tools = {tool["name"]: tool for tool in rpc(client, "tools/list", {})["tools"]}
    schema = tools["place_order"]["inputSchema"]
    valid = {
        "code": "US.AAPL",
        "price": "10.25",
        "qty": 1,
        "trd_side": "BUY",
        "trd_env": "SIMULATE",
        "operation_id": "opaque-non-uuid",
        "admission_epoch": "synthetic-epoch",
        "session": "RTH",
    }
    jsonschema.validate(valid, schema)
    for change in (
        {"price": 10.25},
        {"qty": 1.5},
        {"qty": True},
        {"qty": 0},
        {"operation_id": "x" * 65},
        {"operation_id": "x" * 66},
        {"admission_epoch": None},
        {"session": "ETH"},
        {"time_in_force": "GTC"},
        {"order_type": "MARKET"},
    ):
        assert not jsonschema.Draft202012Validator(schema).is_valid(valid | change)
    without_token = dict(valid)
    without_token.pop("operation_id")
    assert not jsonschema.Draft202012Validator(schema).is_valid(without_token)
    jsonschema.validate(valid | {"trd_env": "REAL", "price": 10.25}, schema)


@pytest.mark.parametrize("token", ["a", "x" * 64, "old-valid-id", "  exact  ", "é漢🙂"])
def test_schema_boundaries_and_runtime_share_printable_opaque_semantics(token):
    validate_token(token, token)
    for annotation in (OperationId, AdmissionEpoch):
        adapter = TypeAdapter(annotation)
        assert adapter.validate_python(token) == token
        jsonschema.validate(token, adapter.json_schema())


@pytest.mark.parametrize(
    "token", ["", " ", "\t", "x\n", "x\x00", "x" * 65, "x" * 66, 1, True, 1.5, {}, []]
)
def test_schema_model_and_runtime_reject_invalid_tokens_without_normalizing(token):
    with pytest.raises(IdentityTokenValidationError):
        validate_token(token, "synthetic-epoch")
    with pytest.raises(ValidationError):
        TypeAdapter(OperationId).validate_python(token)
    # Standard JSON Schema expresses type/length; its human-visible description
    # explicitly retains the shared runtime nonblank/printable checks, no regex.
    schema = TypeAdapter(OperationId).json_schema()
    if not isinstance(token, str) or len(token) == 0 or len(token) > 64:
        assert not jsonschema.Draft202012Validator(schema).is_valid(token)
    assert "nonblank" in schema["description"] and "printable" in schema["description"]


@pytest.mark.asyncio
async def test_contract_digest_matches_served_tools_and_preserves_visibility():
    ordinary = await mcp.list_tools()
    metadata = server_contract_identity(ordinary)
    payload = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in sorted(ordinary, key=lambda tool: tool.name)
    ]
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    assert metadata == {
        "sdk_version": "10.10.7008",
        "build_revision": None,
        "package_version": "0.2.1",
        "contract_version": CONTRACT_VERSION,
        "source_revision": "unknown",
        "tools_schema_sha256": digest,
    }
    with client_grants_context(ClientGrants()):
        restricted = await mcp.list_tools()
        restricted_metadata = server_contract_identity(restricted)
    restricted_tools = {tool.name: tool for tool in restricted}
    assert "unlock_trade" not in restricted_tools
    assert restricted_metadata["tools_schema_sha256"] != digest
    assert (
        restricted_tools["place_order"].inputSchema
        == next(tool for tool in ordinary if tool.name == "place_order").inputSchema
    )


@pytest.mark.parametrize("revision", [None, "a" * 40])
def test_health_wire_identity_matches_the_served_catalog(
    gateway_client, monkeypatch, revision
):
    monkeypatch.setattr(
        "moomoo_mcp.tools.trading_contract.embedded_source_revision", lambda: revision
    )
    client, _, services = gateway_client
    probe = MagicMock(spec=HealthCheck)
    probe.futures = []
    probe.remaining.return_value = 1
    probe.result.return_value = {"status": "disconnected", "gateway_version": None}
    services.moomoo_service.start_health_check.return_value = probe
    future = Future()
    future.set_result({"state": "DISABLED"})
    for method in ("start_journal_health", "start_real_journal_health"):
        getattr(services.trade_service, method).return_value = future
    for method in ("collect_journal_health", "collect_real_journal_health"):
        getattr(services.trade_service, method).return_value = {"state": "DISABLED"}
    definitions = sorted(
        rpc(client, "tools/list", {})["tools"], key=lambda t: t["name"]
    )
    response = call(client, "check_health", {})
    payload = response["structuredContent"]
    assert json.loads(response["content"][0]["text"]) == payload
    expected_digest = hashlib.sha256(
        json.dumps(
            definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()
    assert payload["mcp_server"]["tools_schema_sha256"] == expected_digest
    assert payload["mcp_server"]["source_revision"] == (revision or "unknown")
    assert payload["mcp_server"]["build_revision"] == revision
    assert payload["mcp_server"]["contract_version"] == CONTRACT_VERSION
    assert payload["gateway_version"] is None
