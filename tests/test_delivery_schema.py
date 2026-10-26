"""Selected read delivery contracts: declarations, registry and HTTP profiles.

No broker calls: authenticated tools/list is served against mocked services.
This is not evidence of a hosted connector import or deployed image identity.
"""

import ast
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from moomoo import OpenSecTradeContext
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.client_authorization import ClientGrants, client_grants_context
from moomoo_mcp.services.trade_queries import PAGE_OPERATIONS, SDK_QUERY_OPERATIONS
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.tools.trading_contract import server_contract_identity

SELECTED_READS = {
    "get_order",
    "get_order_fees",
    "get_orders_page",
    "get_deals_page",
    "get_history_orders_page",
    "get_history_deals_page",
    "preview_order",
    "get_trading_capabilities",
    "get_trade_events",
}

# Only reviewed SDK operations, not an expose-everything SDK inventory.
SELECTED_QUERIES = {
    "get_orders_page": "order_list_query",
    "get_deals_page": "deal_list_query",
    "get_history_orders_page": "history_order_list_query",
    "get_history_deals_page": "history_deal_list_query",
    "get_order_fees": "order_fee_query",
}
FIXTURE_PRIMARY = "delivery-fixture-primary"
FIXTURE_RESTRICTED = "delivery-fixture-restricted"


def declared_tools():
    """Inspect decorators without importing otherwise orphaned tool modules."""
    declarations = {}
    directory = Path(server.__file__).parent / "tools"
    for path in sorted(directory.glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                if (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "mcp"
                    and function.attr == "tool"
                ):
                    assert node.name not in declarations, node.name
                    declarations[node.name] = (path, node)
    return declarations


@pytest.mark.asyncio
async def test_every_declared_tool_is_registered_without_orphans():
    with client_grants_context(None):
        registered = {tool.name: tool for tool in await server.mcp.list_tools()}
    declarations = declared_tools()
    assert declarations.keys() == registered.keys()
    assert registered.keys() >= SELECTED_READS
    startup_imports = {
        alias.name
        for node in ast.parse(Path(server.__file__).read_text()).body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    # conftest also imports some tools: it must not mask a missing startup import.
    for path, _ in declarations.values():
        assert "moomoo_mcp.tools." + path.stem in startup_imports, path.name
    for name in SELECTED_READS:
        assert callable(getattr(TradeService, name, None)), name
        tool = registered[name]
        assert tool.description, name
        assert tool.annotations is not None, name
        assert tool.annotations.readOnlyHint is True, name
        assert tool.annotations.destructiveHint is False, name
        assert tool.annotations.idempotentHint is True, name
        jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
        # Legacy preview_order returns an unstructured dict; do not change its
        # public contract merely to require an output schema on every read.
        if name != "preview_order":
            assert tool.outputSchema is not None, name
        if tool.outputSchema is not None:
            jsonschema.Draft202012Validator.check_schema(tool.outputSchema)
        # A registered wrapper must still call its selected service interface.
        _, node = declarations[name]
        assert any(
            isinstance(child, ast.Attribute) and child.attr == name
            for child in ast.walk(node)
        ), name


def test_selected_query_operations_remain_explicit_and_sdk_backed():
    assert SELECTED_QUERIES.items() >= PAGE_OPERATIONS.items()
    assert set(SELECTED_QUERIES.values()) == SDK_QUERY_OPERATIONS
    for operation in SELECTED_QUERIES.values():
        assert callable(getattr(OpenSecTradeContext, operation, None)), operation


def test_delivery_document_names_every_selected_read():
    document = (
        Path(__file__).parents[1] / "docs" / "mcp-delivery-reconciliation.md"
    ).read_text()
    for name in SELECTED_READS:
        assert "| `" + name + "` |" in document, name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions, visible",
    [
        (None, True),  # primary, full-policy credential
        ({"market_read", "real_read", "simulate_read", "simulate_write"}, True),
        ({"real_read"}, True),
        ({"simulate_read"}, True),  # visibility is not SDK/environment support
        ({"market_read"}, False),
        ({"gateway_control"}, False),
        ({"real_write", "simulate_write"}, False),
        (set(), False),
    ],
)
async def test_selected_reads_have_identical_schema_through_http_profiles(
    monkeypatch, mcp_app_context, permissions, visible
):
    grants = None if permissions is None else ClientGrants(frozenset(permissions))
    with client_grants_context(grants):
        local = await server.mcp.list_tools()
        expected = {
            tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in local
        }
        expected_identity = server_contract_identity(local)
    monkeypatch.setattr(server, "get_services", lambda: mcp_app_context)
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    app = server.create_streamable_http_app(
        FIXTURE_PRIMARY,
        restricted_auth_token=FIXTURE_RESTRICTED,
        restricted_grants=grants,
    )
    credential = FIXTURE_PRIMARY if grants is None else FIXTURE_RESTRICTED
    try:
        with TestClient(app) as client:
            response = client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + credential,
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 200
            result = response.json()["result"]
            assert not result.get("nextCursor"), "Inspect every schema page"
            served = {tool["name"]: tool for tool in result["tools"]}
            assert served == expected
            assert (SELECTED_READS & served.keys()) == (
                SELECTED_READS if visible else set()
            )
            if grants is not None and "gateway_control" not in grants.permissions:
                assert "unlock_trade" not in served
                assert "lock_trade" not in served
            if permissions in ({"real_read"}, {"simulate_read"}, {"market_read"}):
                assert not {"place_order", "place_combo_order", "cancel_order"} & (
                    served.keys()
                )
            # Compute from the actual wire definitions, independent of list order.
            payload = sorted(result["tools"], key=lambda tool: tool["name"])
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            assert digest == expected_identity["tools_schema_sha256"]
    finally:
        server.mcp._session_manager = None
    for service in (
        mcp_app_context.moomoo_service,
        mcp_app_context.trade_service,
        mcp_app_context.market_data_service,
    ):
        assert service.mock_calls == []
