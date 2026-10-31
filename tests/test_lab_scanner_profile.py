"""Private scanner identity: exact wire catalogue, denials and shared budgets."""

import asyncio
import json
from threading import Event
from unittest.mock import AsyncMock, MagicMock, Mock

import jsonschema
import pandas as pd
import pytest
from mcp import types
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from moomoo import OpenQuoteContext
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.catalogue_capture import (
    LAB_SCANNER_GRANTS,
    LAB_SCANNER_TOOLS,
    verify_lab_scanner_catalogue,
)
from moomoo_mcp.client_authorization import (
    client_grants_context,
    current_client_grants,
)
from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.data_evidence import MAX_DATA_ROWS
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.tools.trading_contract import server_contract_identity
from tests.rate_limit_clock import FakeClock

PRIMARY = "synthetic-primary-profile"
SCANNER = "synthetic-market-scanner"


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


async def scanner_call(context, session, name, arguments):
    """Distinct MCP sessions, real registered JSON-RPC handler, one lifespan."""
    session.send_log_message = AsyncMock()
    token = request_ctx.set(
        RequestContext(
            request_id=id(session), meta=None, session=session, lifespan_context=context
        )
    )
    try:
        with client_grants_context(LAB_SCANNER_GRANTS):
            result = await server.mcp._mcp_server.request_handlers[
                types.CallToolRequest
            ](
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments=arguments),
                )
            )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
    finally:
        request_ctx.reset(token)


async def test_exact_scanner_catalogue_and_schema_hash():
    with client_grants_context(LAB_SCANNER_GRANTS):
        tools = await server.mcp.list_tools()
    wire = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools
    ]
    verified = verify_lab_scanner_catalogue(wire)
    assert len(tools) == 21
    assert verified["tool_names"] == sorted(LAB_SCANNER_TOOLS)
    assert verified["permissions"] == ["market_read"]
    assert verified["catalogue_verified"] is True
    assert verified["execution_authorization_verified"] is False
    assert verified["live_connection_verified"] is False
    assert (
        verified["tools_schema_sha256"]
        == server_contract_identity(tools)["tools_schema_sha256"]
    )
    verify_lab_scanner_catalogue(wire, expected_sha256=verified["tools_schema_sha256"])
    with pytest.raises(ValueError) as failure:
        verify_lab_scanner_catalogue(wire, expected_sha256="0" * 64)
    assert "approved schema hash" in str(failure.value)
    for tool in wire:
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        if "outputSchema" in tool:
            jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
    assert current_client_grants() is None


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "account",
        "future",
        "duplicate",
        "writable",
        "schema",
        "missing_filter",
        "untyped_filter",
    ],
)
async def test_scanner_verifier_fails_closed_on_drift(change):
    with client_grants_context(LAB_SCANNER_GRANTS):
        wire = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in await server.mcp.list_tools()
        ]
    if change == "missing":
        wire.pop()
    elif change in {"account", "future"}:
        wire.append(
            wire[0] | {"name": "get_accounts" if change == "account" else "future_read"}
        )
    elif change == "duplicate":
        wire.append(wire[0])
    elif change == "writable":
        wire[0]["annotations"]["destructiveHint"] = True
    elif change in {"missing_filter", "untyped_filter"}:
        chain_schema = next(
            tool["inputSchema"] for tool in wire if tool["name"] == "get_option_chain"
        )
        if change == "missing_filter":
            del chain_schema["properties"]["data_filter"]
        else:
            chain_schema["$defs"]["OptionChainFilter"]["additionalProperties"] = True
    else:
        wire[0]["inputSchema"] = {}
    with pytest.raises(ValueError):
        verify_lab_scanner_catalogue(wire)


def test_http_scanner_catalogue_and_all_direct_nonmarket_calls_are_denied(
    monkeypatch, mcp_app_context
):
    monkeypatch.setattr(server, "get_services", lambda: mcp_app_context)
    monkeypatch.setattr(server.mcp, "_session_manager", None)
    with TestClient(
        server.create_streamable_http_app(
            PRIMARY, restricted_auth_token=SCANNER, restricted_grants=LAB_SCANNER_GRANTS
        )
    ) as client:

        def rpc(method, params, credential=SCANNER):
            response = client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + credential,
                    "Accept": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            assert response.status_code == 200
            return response.json()["result"]

        tools = rpc("tools/list", {})["tools"]
        verify_lab_scanner_catalogue(tools)
        primary = {tool["name"] for tool in rpc("tools/list", {}, PRIMARY)["tools"]}
        denied_names = primary - LAB_SCANNER_TOOLS
        assert {
            "get_prepared_trade",
            "prepare_trade_request",
            "place_order",
            "get_accounts",
            "get_execution",
            "unlock_trade",
        } <= denied_names
        for name in sorted(denied_names):
            for environment in ("REAL", "SIMULATE"):
                result = rpc(
                    "tools/call",
                    {
                        "name": name,
                        "arguments": {
                            "trd_env": environment,
                            "acc_id": "123",
                            "method": "place_order",
                            "parameters": {"trd_env": environment},
                        },
                    },
                )
                assert result["isError"] is True, name
                diagnostic = result["structuredContent"]
                assert diagnostic["error_code"] == "CLIENT_PERMISSION_DENIED", name
                assert diagnostic["retryable"] is False
                assert json.loads(result["content"][0]["text"]) == diagnostic
        # Every denial precedes schema validation, admission, journal and SDK code.
        assert mcp_app_context.trade_service.mock_calls == []
        assert mcp_app_context.market_data_service.mock_calls == []
        assert mcp_app_context.moomoo_service.mock_calls == []
        assert {
            tool["name"] for tool in rpc("tools/list", {})["tools"]
        } == LAB_SCANNER_TOOLS
    server.mcp._session_manager = None


@pytest.fixture
def scanner_runtime(mcp_app_context, monkeypatch):
    clock = FakeClock()
    dispatcher = BrokerRequestDispatcher(
        clock=clock,
        sleep=clock.sleep,
        deadline_sleep=clock.sleep,
        max_inflight_reads=1,
        max_pending_reads=2,
        read_timeout_seconds=10,
    )
    quote = MagicMock(spec=OpenQuoteContext)
    quote.get_option_chain.return_value = (0, pd.DataFrame())
    quote.get_market_snapshot.return_value = (0, pd.DataFrame())
    context = mcp_app_context
    context.market_data_service = MarketDataService(quote, dispatcher=dispatcher)
    build = Mock(return_value=context)
    monkeypatch.setattr(server, "_services", None)
    monkeypatch.setattr(server, "_build_services", build)
    monkeypatch.setattr(server.atexit, "register", lambda _callback: None)
    return context, quote, clock, dispatcher, build


async def test_scanner_sessions_share_process_admission_workers_and_deadlines(
    scanner_runtime,
):
    context, quote, clock, dispatcher, build = scanner_runtime
    entered, release = Event(), Event()

    def sdk(**_kwargs):
        assert current_client_grants() == LAB_SCANNER_GRANTS
        entered.set()
        assert release.wait(5)
        return 0, pd.DataFrame([{"code": "US.EXACT261030C00001000"}])

    quote.get_option_chain.side_effect = sdk
    sessions = [MagicMock() for _ in range(3)]
    with client_grants_context(LAB_SCANNER_GRANTS):
        async with server.app_lifespan(server.mcp) as first:
            async with server.app_lifespan(server.mcp) as second:
                assert first is second is context
    build.assert_called_once()
    active = asyncio.create_task(
        scanner_call(
            context,
            sessions[0],
            "get_option_chain",
            {"code": "US.X", "include_evidence": True},
        )
    )
    queued = None
    try:
        await wait_until(entered.is_set)
        queued = asyncio.create_task(
            scanner_call(
                context,
                sessions[1],
                "get_market_snapshot_evidence",
                {"codes": ["US.X"]},
            )
        )
        await wait_until(lambda: dispatcher.read_requests_pending == 2)
        rejected = await scanner_call(
            context, sessions[2], "get_instrument_facts", {"codes": ["US.X"]}
        )
        assert rejected["isError"] is True
        diagnostic = rejected["structuredContent"]
        assert diagnostic["error_code"] == "READ_OVERLOADED"
        assert diagnostic["pending_reads"] == diagnostic["max_pending_reads"] == 2
        assert diagnostic["admission_rejected"] is True
        assert dispatcher.read_workers_in_flight == 1
        quote.get_market_snapshot.assert_not_called()
        await clock.wait_for_sleepers(3)
        clock.advance(10)
        results = await asyncio.wait_for(asyncio.gather(active, queued), 2)
        assert [r["structuredContent"]["error_code"] for r in results] == [
            "READ_DEADLINE_EXCEEDED"
        ] * 2
        assert results[0]["structuredContent"]["timing"]["sdk_in_flight"] is True
        assert results[1]["structuredContent"]["timing"]["sdk_calls_started"] == 0
        assert all(
            r["structuredContent"]["underlying_sdk_cancelled"] is False for r in results
        )
        assert dispatcher.read_requests_pending == 0
        assert dispatcher.read_workers_in_flight == 1
    finally:
        release.set()
        for task in (active, queued):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (active, queued) if task), return_exceptions=True
        )
        await wait_until(lambda: dispatcher.read_workers_in_flight == 0)
    assert not context.market_data_service.instrument_adapter._chain_metadata
    assert current_client_grants() is None


async def test_new_sessions_unfiltered_fallback_still_shares_filtered_chain_quota(
    scanner_runtime,
):
    context, quote, clock, dispatcher, _build = scanner_runtime
    for _ in range(10):
        result = await scanner_call(
            context,
            MagicMock(),
            "get_option_chain",
            {
                "code": "US.X",
                "data_filter": {"delta_min": 0.1},
                "include_evidence": True,
            },
        )
        assert result["structuredContent"]["result"]["data"] == []
    waiting = asyncio.create_task(
        scanner_call(
            context,
            MagicMock(),
            "get_option_chain",
            {
                "code": "US.X",
                "start": "2026-10-30",
                "end": "2026-10-30",
                "include_evidence": True,
            },
        )
    )
    await clock.wait_for_sleepers(2)
    assert dispatcher.read_requests_pending == 1
    assert dispatcher.read_workers_in_flight == 0
    clock.advance(5)
    refused = await asyncio.wait_for(waiting, 2)
    assert refused["structuredContent"]["error_code"] == "RATE_LIMITED"
    assert refused["structuredContent"]["retry_after_seconds"] > 0
    assert quote.get_option_chain.call_count == 10
    assert dispatcher.read_requests_pending == 0
    clock.advance(25.102)
    success = await scanner_call(
        context,
        MagicMock(),
        "get_option_chain",
        {"code": "US.X", "include_evidence": True},
    )
    assert success["structuredContent"]["result"]["scope"]["data_filter"] is None
    assert quote.get_option_chain.call_count == 11
    assert "data_filter" not in quote.get_option_chain.call_args.kwargs


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_instrument_facts", {"codes": ["US.X"]}),
        ("get_market_snapshot_evidence", {"codes": ["US.X"]}),
        ("get_option_chain", {"code": "US.X", "include_evidence": True}),
    ],
)
async def test_scanner_unavailable_connection_is_not_empty_success(
    scanner_runtime, name, args
):
    context, quote, _clock, dispatcher, _build = scanner_runtime
    context.market_data_service.quote_ctx = None
    result = await scanner_call(context, MagicMock(), name, args)
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "CONNECTION_UNAVAILABLE"
    assert "data" not in result["structuredContent"]
    assert quote.mock_calls == []
    assert dispatcher.read_requests_pending == dispatcher.read_workers_in_flight == 0


@pytest.mark.parametrize(
    "filters",
    [
        None,
        {},
        {"delta_min": 0.1234, "implied_volatility_max": 80.5, "open_interest_min": 0},
    ],
)
async def test_chain_evidence_exact_applied_scope_codes_coverage_and_timing(
    scanner_runtime, filters
):
    context, quote, clock, _dispatcher, _build = scanner_runtime
    rows = [
        {
            "code": "US.Exact1261130C00001000",
            "lot_size": 1,
            "strike_price": 1.0,
            "option_standard_type": "NON_STANDARD",
            "option_settlement_mode": "PM",
        },
        {
            "code": "US.Exact261130C00002000",
            "lot_size": 100,
            "strike_price": 2.0,
            "option_standard_type": "STANDARD",
            "option_settlement_mode": None,
        },
    ]

    def sdk(**_kwargs):
        clock.advance(2)
        return 0, pd.DataFrame(rows)

    quote.get_option_chain.side_effect = sdk
    arguments = {
        "code": " US.Exact ",
        "start": "2026-11-30",
        "end": "2026-11-30",
        "option_type": "call",
        "option_cond_type": "outside",
        "include_evidence": True,
    }
    if filters is not None:
        arguments["data_filter"] = filters
    response = await scanner_call(context, MagicMock(), "get_option_chain", arguments)
    assert response["isError"] is False
    result = response["structuredContent"]["result"]
    assert result["data"] == rows
    assert result["coverage"] == {
        "requested": [],
        "returned": [row["code"] for row in rows],
        "missing": [],
        "complete_response": True,
        "provider_history_complete": None,
        "truncated": False,
        "total_returned": 2,
    }
    assert result["scope"] == {
        "code": "US.Exact",
        "start": "2026-11-30",
        "end": "2026-11-30",
        "option_type": "CALL",
        "option_cond_type": "OUTSIDE",
        "data_filter": filters or None,
    }
    kwargs = quote.get_option_chain.call_args.kwargs
    assert kwargs["code"] == "US.Exact"
    assert kwargs["option_type"] == "CALL" and kwargs["option_cond_type"] == "OUTSIDE"
    if filters:
        assert all(
            getattr(kwargs["data_filter"], field) == value
            for field, value in filters.items()
        )
    else:
        assert "data_filter" not in kwargs
    timing = result["timing"]
    assert timing["total_seconds"] == timing["sdk_seconds"] == 2
    assert timing["sdk_calls_started"] == timing["sdk_calls_completed"] == 1
    assert timing["sdk_in_flight"] is False
    assert result["delay_status"] == result["entitlement_status"] == "UNKNOWN"
    quote.subscribe.assert_not_called()


async def test_explicit_basic_scan_after_unsupported_filter_preserves_partial_data(
    scanner_runtime, monkeypatch
):
    context, quote, _clock, _dispatcher, _build = scanner_runtime
    quote.get_option_expiration_date.return_value = (
        0,
        pd.DataFrame([{"strike_time": "2026-11-30"}]),
    )
    discovery = await scanner_call(
        context, MagicMock(), "get_option_expiration_date", {"code": "US.X"}
    )
    expiry = discovery["structuredContent"]["result"][0]["strike_time"]
    # An SDK without optional filters must still support the basic scan. Failure
    # never triggers a hidden retry or silently strips requested conditions.
    monkeypatch.setattr(
        "moomoo_mcp.services.market_data_service.OptionDataFilter", lambda: None
    )
    args = {"code": "US.X", "start": expiry, "end": expiry, "include_evidence": True}
    unavailable = await scanner_call(
        context,
        MagicMock(),
        "get_option_chain",
        args | {"data_filter": {"delta_min": 0.1}},
    )
    assert unavailable["isError"] is True
    assert unavailable["structuredContent"]["error_code"] == "CAPABILITY_UNAVAILABLE"
    quote.get_option_chain.assert_not_called()
    empty = await scanner_call(context, MagicMock(), "get_option_chain", args)
    assert empty["isError"] is False
    assert empty["structuredContent"]["result"]["data"] == []
    assert empty["structuredContent"]["result"]["coverage"]["total_returned"] == 0
    rows = [{"code": "US.X1261130C00001000", "strike_price": None, "lot_size": 0}]
    quote.get_option_chain.return_value = (0, pd.DataFrame(rows))
    partial = await scanner_call(context, MagicMock(), "get_option_chain", args)
    assert partial["structuredContent"]["result"]["data"] == rows
    assert (
        partial["structuredContent"]["result"]["coverage"]["provider_history_complete"]
        is None
    )
    assert partial["structuredContent"]["result"]["scope"]["data_filter"] is None
    assert quote.get_option_chain.call_count == 2
    assert all(
        "data_filter" not in call.kwargs
        for call in quote.get_option_chain.call_args_list
    )


@pytest.mark.parametrize("count", [MAX_DATA_ROWS, MAX_DATA_ROWS + 1])
async def test_chain_row_bound_is_exact_and_excess_is_failure_not_silent_truncation(
    scanner_runtime, count
):
    context, quote, _clock, _dispatcher, _build = scanner_runtime
    rows = [{"code": "US.EXACT" + str(index)} for index in range(count)]
    quote.get_option_chain.return_value = (0, pd.DataFrame(rows))
    result = await scanner_call(
        context,
        MagicMock(),
        "get_option_chain",
        {"code": "US.X", "include_evidence": True},
    )
    if count == MAX_DATA_ROWS:
        assert result["isError"] is False
        payload = result["structuredContent"]["result"]
        assert payload["data"] == rows
        assert payload["coverage"]["total_returned"] == MAX_DATA_ROWS
        assert payload["coverage"]["truncated"] is False
    else:
        assert result["isError"] is True
        assert result["structuredContent"]["error_code"] == "SNAPSHOT_LIMIT"
        assert "data" not in result["structuredContent"]
        assert not context.market_data_service.instrument_adapter._chain_metadata
    quote.get_option_chain.assert_called_once()


async def test_oversized_chain_fails_instead_of_serving_partial_rows(scanner_runtime):
    context, quote, _clock, _dispatcher, _build = scanner_runtime
    quote.get_option_chain.return_value = (
        0,
        pd.DataFrame(
            [{"code": "US.X261130C00001000", "name": "x" * (2 * 1024 * 1024)}]
        ),
    )
    result = await scanner_call(
        context,
        MagicMock(),
        "get_option_chain",
        {
            "code": "US.X",
            "include_evidence": True,
        },
    )
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "SNAPSHOT_LIMIT"
    assert "data" not in result["structuredContent"]
    assert not context.market_data_service.instrument_adapter._chain_metadata
    quote.get_option_chain.assert_called_once()


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("Quote permission denied", "QUOTE_PERMISSION_DENIED"),
        ("Get Option Chain request timed out.", "TOOL_EXECUTION_FAILED"),
    ],
)
async def test_filtered_failures_never_retry_or_become_empty_success(
    scanner_runtime, failure, expected
):
    context, quote, _clock, _dispatcher, _build = scanner_runtime
    quote.get_option_chain.return_value = (-1, failure)
    result = await scanner_call(
        context,
        MagicMock(),
        "get_option_chain",
        {"code": "US.X", "data_filter": {"delta_min": 0.1}, "include_evidence": True},
    )
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == expected
    assert "data" not in result["structuredContent"]
    quote.get_option_chain.assert_called_once()
    assert not context.market_data_service.instrument_adapter._chain_metadata
