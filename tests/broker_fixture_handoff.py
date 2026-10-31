"""Offline synthetic wire generation against the exact reviewed broker source.

Run: .venv/bin/python -m tests.broker_fixture_handoff
No services are started, SDK contexts constructed, credentials used or sockets opened.
"""

import asyncio
import hashlib
import inspect
import json
import subprocess
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
from mcp import types
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from moomoo import OpenQuoteContext

from moomoo_mcp.catalogue_capture import verify_lab_scanner_catalogue
from moomoo_mcp.client_authorization import ClientGrants, client_grants_context
from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.tools.trading_contract import server_contract_identity

SOURCE = "7bd67b8ecb646e5d04d35a29abfe14f3d494c7a3"
ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "examples" / "broker-fixtures" / SOURCE
NORMAL = "US.SYNTH261030C100000"
ADJUSTED = "US.SYNTH1261030C100000"
MISSING = "US.SYNTH_MISSING"
SELECTED = (
    "get_option_expiration_date",
    "get_option_chain",
    "get_market_snapshot_evidence",
    "get_instrument_facts",
)
REQUESTED = "2026-10-26T14:30:00Z"
RECEIVED = "2026-10-26T14:30:00.250Z"


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def source_manifest(*, verify_git: bool = False) -> dict:
    """Verify committed pinned hashes offline; historical Git is manual-only."""
    manifest = json.loads((DESTINATION / "manifest.json").read_text())
    assert manifest["fixture_kind"] == "SYNTHETIC"
    assert manifest["source_commit"] == SOURCE
    assert manifest["source_url"] == (
        "https://github.com/evil-sparkle/moomoo-api-mcp/tree/" + SOURCE
    ), "fixture source URL must identify the pinned source commit"
    for record in manifest["runtime_files"]:
        current = (ROOT / record["path"]).read_bytes()
        assert hashlib.sha256(current).hexdigest() == record["sha256"], record["path"]
    for name, expected in manifest["serialization_packages"].items():
        assert version(name) == expected, name
    if verify_git:
        paths = (
            git("ls-tree", "-r", "--name-only", SOURCE, "src/moomoo_mcp")
            .decode()
            .splitlines()
        )
        assert paths == [record["path"] for record in manifest["runtime_files"]]
        assert (
            git("rev-parse", SOURCE + ":src/moomoo_mcp").decode().strip()
            == manifest["source_tree"]
        )
        for record in manifest["runtime_files"]:
            pinned = git("show", SOURCE + ":" + record["path"])
            assert hashlib.sha256(pinned).hexdigest() == record["sha256"]
        for name, field in (
            ("uv.lock", "pinned_uv_lock_sha256"),
            ("pyproject.toml", "pinned_pyproject_sha256"),
        ):
            assert (
                hashlib.sha256(git("show", SOURCE + ":" + name)).hexdigest()
                == manifest[field]
            )
    derived = {
        "fixture_files_sha256",
        "selected_python_signatures",
        "catalogue_identity",
    }
    return {key: value for key, value in manifest.items() if key not in derived}


def snapshot(code=NORMAL, **overrides) -> dict:
    return {
        "code": code,
        "stock_owner": "US.SYNTH",
        "option_valid": True,
        "option_type": "CALL",
        "strike_time": "2026-10-30",
        "option_strike_price": 100.0,
        "lot_size": 100,
        "option_contract_multiplier": 100.0,
        "option_contract_size": 100.0,
        "option_area_type": "AMERICAN",
        "last_price": 2.1,
        "bid_price": 2.0,
        "ask_price": 2.2,
        "bid_vol": 5,
        "ask_vol": 7,
        "update_time": "2026-10-26 10:29:59.123",
        **overrides,
    }


def chain_row(code=NORMAL, **overrides) -> dict:
    return {
        "code": code,
        "stock_owner": "US.SYNTH",
        "stock_type": "DRVT",
        "option_type": "CALL",
        "strike_time": "2026-10-30",
        "strike_price": 100.0,
        "lot_size": 100,
        "option_standard_type": "STANDARD",
        "option_settlement_mode": "N/A",
        "suspension": False,
        **overrides,
    }


async def call_wire(
    name: str,
    arguments: dict,
    index: int,
    snapshots=None,
    references=None,
    contracts=None,
    expiries=None,
    failure=None,
    discover_first=False,
) -> dict:
    quote = MagicMock(spec=OpenQuoteContext)
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame(snapshots or []),
    )
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame(references or []),
    )
    quote.get_option_chain.return_value = (
        (-1, failure) if failure else (0, pd.DataFrame(contracts or []))
    )
    quote.get_option_expiration_date.return_value = (
        0,
        pd.DataFrame(expiries or []),
    )
    # Reproducible duration evidence, not a performance measurement.
    service = MarketDataService(
        quote, dispatcher=BrokerRequestDispatcher(clock=lambda: 0)
    )
    trade = MagicMock(spec=TradeService)
    base = MagicMock(spec=MoomooService)
    session = MagicMock()
    session.send_log_message = AsyncMock()
    context = AppContext(
        moomoo_service=base, trade_service=trade, market_data_service=service
    )
    token = request_ctx.set(
        RequestContext(
            request_id=index, meta=None, session=session, lifespan_context=context
        )
    )
    try:
        prior_discovery = None
        if discover_first:
            prior_request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="get_option_chain",
                    arguments={
                        "code": "US.SYNTH",
                        "start": "2026-10-30",
                        "end": "2026-10-30",
                        "include_evidence": True,
                    },
                ),
            )
            prior_result = await mcp._mcp_server.request_handlers[
                types.CallToolRequest
            ](prior_request)
            prior_discovery = {
                "request": {
                    "jsonrpc": "2.0",
                    "id": "discovery-" + str(index),
                    **prior_request.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                },
                "response": types.JSONRPCResponse(
                    jsonrpc="2.0",
                    id="discovery-" + str(index),
                    result=prior_result.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                ).model_dump(mode="json", by_alias=True, exclude_none=True),
            }
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        result = await mcp._mcp_server.request_handlers[types.CallToolRequest](request)
        response = types.JSONRPCResponse(
            jsonrpc="2.0",
            id=index,
            result=result.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    finally:
        request_ctx.reset(token)
    assert trade.mock_calls == [] and base.mock_calls == []
    allowed = {
        "get_market_snapshot",
        "get_stock_basicinfo",
        "get_option_chain",
        "get_option_expiration_date",
    }
    assert all(call[0] in allowed for call in quote.mock_calls)
    wire = {
        "request": {
            "jsonrpc": "2.0",
            "id": index,
            **request.model_dump(mode="json", by_alias=True, exclude_none=True),
        },
        "response": response.model_dump(mode="json", by_alias=True, exclude_none=True),
        "broker_double_methods": [call[0] for call in quote.mock_calls],
    }
    if prior_discovery is not None:
        wire["prior_discovery"] = prior_discovery
    return wire


async def generate(*, verify_git: bool = False) -> dict[str, dict]:
    manifest = source_manifest(verify_git=verify_git)
    fixtures = {}
    cases = [
        (
            "expiry.normal",
            SELECTED[0],
            {"code": "US.SYNTH"},
            {
                "expiries": [
                    {
                        "strike_time": "2026-10-30",
                        "option_expiry_date_distance": 4,
                        "expiration_cycle": "WEEK",
                    }
                ]
            },
        ),
        ("expiry.empty", SELECTED[0], {"code": "US.SYNTH"}, {}),
        (
            "chain.normal_adjusted",
            SELECTED[1],
            {
                "code": "US.SYNTH",
                "start": "2026-10-30",
                "end": "2026-10-30",
                "data_filter": {"delta_min": 0.1, "delta_max": 0.9},
                "include_evidence": True,
            },
            {
                "contracts": [
                    chain_row(),
                    chain_row(
                        ADJUSTED, option_standard_type="NON_STANDARD", lot_size=150
                    ),
                ]
            },
        ),
        (
            "chain.empty",
            SELECTED[1],
            {
                "code": "US.SYNTH",
                "start": "2026-10-30",
                "end": "2026-10-30",
                "include_evidence": True,
            },
            {},
        ),
        (
            "chain.permission_error",
            SELECTED[1],
            {"code": "US.SYNTH", "include_evidence": True},
            {"failure": "Quote permission denied"},
        ),
        (
            "snapshot.normal",
            SELECTED[2],
            {"codes": [NORMAL]},
            {"snapshots": [snapshot()]},
        ),
        (
            "snapshot.partial_null_zero",
            SELECTED[2],
            {"codes": [NORMAL, MISSING]},
            {
                "snapshots": [
                    snapshot(
                        bid_price=0.0,
                        ask_price=None,
                        bid_vol=0,
                        ask_vol=None,
                        last_price=None,
                    )
                ]
            },
        ),
        (
            "snapshot.missing_timestamp",
            SELECTED[2],
            {"codes": [NORMAL]},
            {
                "snapshots": [
                    {k: v for k, v in snapshot().items() if k != "update_time"}
                ]
            },
        ),
        (
            "facts.standard_incomplete",
            SELECTED[3],
            {"codes": [NORMAL]},
            {
                "snapshots": [snapshot()],
                "references": [{"code": NORMAL, "stock_type": "DRVT", "lot_size": 100}],
            },
        ),
        (
            "facts.adjusted_non100",
            SELECTED[3],
            {"codes": [ADJUSTED]},
            {
                "snapshots": [
                    snapshot(
                        ADJUSTED,
                        option_contract_multiplier=25.0,
                        option_contract_size=150.0,
                        lot_size=1,
                    )
                ],
                "references": [{"code": ADJUSTED, "stock_type": "DRVT", "lot_size": 1}],
            },
        ),
        (
            "facts.conflict",
            SELECTED[3],
            {"codes": [NORMAL]},
            {
                "snapshots": [snapshot(lot_size=100)],
                "references": [{"code": NORMAL, "stock_type": "DRVT", "lot_size": 1}],
            },
        ),
        (
            "facts.zero_unknown",
            SELECTED[3],
            {"codes": [NORMAL]},
            {
                "snapshots": [
                    snapshot(option_contract_multiplier=0.0, option_contract_size=0.0)
                ],
                "references": [{"code": NORMAL, "stock_type": "DRVT", "lot_size": 100}],
            },
        ),
        ("facts.missing_sources", SELECTED[3], {"codes": [MISSING]}, {}),
        (
            "chain.unfiltered_expiry",
            SELECTED[1],
            {
                "code": "US.SYNTH",
                "start": "2026-10-30",
                "end": "2026-10-30",
                "include_evidence": True,
            },
            {
                "contracts": [
                    chain_row(),
                    chain_row(
                        ADJUSTED, option_standard_type="NON_STANDARD", lot_size=1
                    ),
                ]
            },
        ),
        (
            "facts.chain_enriched",
            SELECTED[3],
            {"codes": [ADJUSTED]},
            {
                "discover_first": True,
                "contracts": [
                    chain_row(
                        ADJUSTED,
                        option_standard_type="NON_STANDARD",
                        lot_size=1,
                        option_settlement_mode="PM",
                    )
                ],
                "snapshots": [
                    snapshot(
                        ADJUSTED,
                        lot_size=1,
                        option_contract_size=150,
                        option_contract_multiplier=25,
                    )
                ],
                "references": [{"code": ADJUSTED, "stock_type": "DRVT", "lot_size": 1}],
            },
        ),
    ]
    with ExitStack() as stack:
        stack.enter_context(
            client_grants_context(ClientGrants(frozenset({"market_read"})))
        )
        for target in (
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "moomoo_mcp.server.get_services",
            "moomoo.OpenQuoteContext.__init__",
            "moomoo.OpenSecTradeContext.__init__",
        ):
            stack.enter_context(
                patch(
                    target,
                    side_effect=AssertionError(
                        "Offline fixture attempted a runtime connection/service"
                    ),
                )
            )
        for module, clock in (
            ("moomoo_mcp.tools.market_data", REQUESTED),
            ("moomoo_mcp.services.market_data_service", "2026-10-26T14:30:00.050Z"),
            ("moomoo_mcp.services.broker_data_reads", REQUESTED),
            ("moomoo_mcp.services.instruments", "2026-10-26T14:30:00.100Z"),
            ("moomoo_mcp.services.data_evidence", RECEIVED),
            ("moomoo_mcp.services.quote_errors", RECEIVED),
            ("moomoo_mcp.agent_server", RECEIVED),
        ):
            stack.enter_context(patch(module + ".utc_now_iso", return_value=clock))
        catalog = await mcp._mcp_server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        catalog_wire = types.JSONRPCResponse(
            jsonrpc="2.0",
            id="catalog",
            result=catalog.model_dump(mode="json", by_alias=True, exclude_none=True),
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        fixtures["catalogue.market_read.json"] = {
            "fixture_kind": "SYNTHETIC",
            "permissions": ["market_read"],
            "response": catalog_wire,
            "profile_verification": verify_lab_scanner_catalogue(
                catalog_wire["result"]["tools"]
            ),
        }
        manifest["catalogue_identity"] = server_contract_identity(
            await mcp.list_tools()
        )
        signatures = {}
        for name in SELECTED:
            tool = mcp._tool_manager.get_tool(name)
            assert tool is not None
            signatures[name] = str(inspect.signature(tool.fn))
        manifest["selected_python_signatures"] = signatures
        for index, (case_id, name, arguments, inputs) in enumerate(cases, 1):
            wire = await call_wire(name, arguments, index, **inputs)
            fixtures[case_id + ".json"] = {
                "fixture_kind": "SYNTHETIC",
                "case_id": case_id,
                "permissions": ["market_read"],
                **wire,
            }
    manifest["fixture_files_sha256"] = {
        name: hashlib.sha256(encode(value)).hexdigest()
        for name, value in fixtures.items()
    }
    fixtures["manifest.json"] = manifest
    return fixtures


def encode(value: dict) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def write() -> None:
    fixtures = asyncio.run(generate(verify_git=True))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, value in fixtures.items():
        payload = encode(value)
        assert len(payload) <= 128 * 1024
        (DESTINATION / name).write_bytes(payload)
    print(f"Wrote {len(fixtures)} SYNTHETIC files to {DESTINATION}")


if __name__ == "__main__":
    write()
