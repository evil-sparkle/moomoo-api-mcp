"""Isolated protocol and failure tests for the private MCP preflight."""

from __future__ import annotations

import contextlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts import private_chatgpt_preflight as preflight
from tests.mcp_helpers import ACCOUNT_ID, AUTHORIZATION, McpFixture


@pytest.fixture
def authorization_file(tmp_path: Path) -> Path:
    path = tmp_path / "mcp-authorization"
    path.write_text(AUTHORIZATION + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def invoke(
    server: McpFixture,
    authorization_file: Path,
    *,
    mode: str = "startup-safe",
    trd_env: str = "SIMULATE",
) -> list[str]:
    return preflight.run(
        url=server.url,
        authorization_file=authorization_file,
        mode=mode,
        trd_env=trd_env if mode == "full" else None,
        account_id=ACCOUNT_ID if mode == "full" else None,
    )


@pytest.mark.parametrize("trading_mode", ["READ_ONLY", "SIMULATE"])
def test_startup_safe_validates_real_mcp_results_and_authentication(
    authorization_file: Path,
    trading_mode: str,
) -> None:
    with McpFixture(trading_mode=trading_mode) as server:
        milestones = invoke(server, authorization_file)

    assert milestones == ["initialize", "tools/list", "check_health:" + trading_mode]
    assert [call["request"]["method"] for call in server.calls] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    assert {call["authorization"] for call in server.calls} == {AUTHORIZATION}
    assert server.calls[-1]["request"]["params"]["name"] == "check_health"


@pytest.mark.parametrize("trading_mode", ["READ_ONLY", "SIMULATE"])
@pytest.mark.parametrize("trd_env", ["REAL", "SIMULATE"])
def test_full_mode_validates_selected_account_and_positions(
    authorization_file: Path,
    trading_mode: str,
    trd_env: str,
) -> None:
    with McpFixture(trading_mode=trading_mode) as server:
        milestones = invoke(server, authorization_file, mode="full", trd_env=trd_env)

    assert milestones[2] == "check_health:" + trading_mode
    assert milestones[-2:] == ["get_accounts:selected", "get_positions"]
    calls = [
        call["request"]["params"]
        for call in server.calls
        if call["request"]["method"] == "tools/call"
    ]
    assert calls[-1] == {
        "name": "get_positions",
        "arguments": {"trd_env": trd_env, "acc_id": ACCOUNT_ID},
    }


@pytest.mark.parametrize("mode", ["REAL", "UNKNOWN", "simulate", "", None])
def test_startup_safe_fails_closed_unless_health_proves_allowed_mode(
    authorization_file: Path, mode: str | None
) -> None:
    with (
        McpFixture(trading_mode=mode) as server,
        pytest.raises(preflight.PreflightError, match="READ_ONLY"),
    ):
        invoke(server, authorization_file)


@pytest.mark.parametrize("trading_mode", ["READ_ONLY", "SIMULATE"])
@pytest.mark.parametrize("gateway_status", ["disconnected", "degraded"])
def test_startup_safe_tolerates_unavailable_opend_but_full_fails_honestly(
    authorization_file: Path, gateway_status: str, trading_mode: str
) -> None:
    with McpFixture(gateway_status=gateway_status, trading_mode=trading_mode) as server:
        assert invoke(server, authorization_file)[-1] == "check_health:" + trading_mode
        server.fail_account = True
        with pytest.raises(preflight.PreflightError):
            invoke(server, authorization_file, mode="full")


@pytest.mark.parametrize("health", [None, [], {}, {"trading_mode": []}])
def test_invalid_health_fails_closed(health):
    with pytest.raises(preflight.PreflightError, match="READ_ONLY or SIMULATE"):
        preflight.validate_trading_mode(health)


def test_real_backend_accepts_only_proven_restricted_client():
    health = {
        "trading_mode": "REAL",
        "client_authorization": {
            "restricted": True,
            "permissions": [
                "market_read",
                "real_read",
                "simulate_read",
                "simulate_write",
            ],
        },
    }
    assert preflight.validate_trading_mode(health) == "REAL"
    for permissions in (
        ["real_write"],
        ["gateway_control"],
        ["unknown"],
        [None],
        "real_read",
    ):
        health["client_authorization"]["permissions"] = permissions
        with pytest.raises(preflight.PreflightError):
            preflight.validate_trading_mode(health)
    health["client_authorization"] = {"restricted": False, "permissions": []}
    with pytest.raises(preflight.PreflightError):
        preflight.validate_trading_mode(health)


def test_missing_and_wrong_credentials_fail_before_any_protocol_result(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    wrong = tmp_path / "wrong"
    wrong.write_text("Bearer incorrect\n", encoding="utf-8")
    with McpFixture() as server:
        with pytest.raises(preflight.PreflightError, match="could not be read"):
            invoke(server, missing)
        with pytest.raises(preflight.PreflightError, match="HTTP 401"):
            invoke(server, wrong)
    assert len(server.calls) == 1
    assert server.calls[0]["request"]["method"] == "initialize"


@pytest.mark.parametrize(
    ("name", "override", "message"),
    [
        (
            "malformed",
            lambda _r: (200, b"not json", "application/json"),
            "malformed JSON",
        ),
        (
            "wrong content type",
            lambda _r: (200, {}, "text/plain"),
            "application/json",
        ),
        (
            "mismatched id",
            lambda _r: (
                200,
                {"jsonrpc": "2.0", "id": 999, "result": {}},
                "application/json",
            ),
            "mismatched request id",
        ),
        (
            "rpc error",
            lambda r: (
                200,
                {"jsonrpc": "2.0", "id": r["id"], "error": {"code": -1}},
                "application/json",
            ),
            "JSON-RPC error",
        ),
        (
            "missing result",
            lambda r: (
                200,
                {"jsonrpc": "2.0", "id": r["id"]},
                "application/json",
            ),
            "no result object",
        ),
    ],
)
def test_http_200_cannot_mask_invalid_protocol_results(
    authorization_file: Path, name: str, override: Any, message: str
) -> None:
    assert name
    with McpFixture() as server:
        server.override = override
        with pytest.raises(preflight.PreflightError, match=message):
            invoke(server, authorization_file)


def test_wrong_tool_list_fails_even_after_valid_initialize(
    authorization_file: Path,
) -> None:
    with McpFixture() as server:

        def omit_tools(request: dict[str, Any]):
            if request["method"] == "initialize":
                return (
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "protocolVersion": preflight.PROTOCOL_VERSION,
                            "serverInfo": {"name": "fixture", "version": "1"},
                        },
                    },
                    "application/json",
                )
            return (
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"tools": [{"name": "check_health"}]},
                },
                "application/json",
            )

        server.override = omit_tools
        with pytest.raises(preflight.PreflightError, match="omitted"):
            invoke(server, authorization_file)


def test_cli_diagnostics_never_print_credentials_or_private_results(
    authorization_file: Path,
) -> None:
    output = io.StringIO()
    with McpFixture() as server, contextlib.redirect_stdout(output):
        assert (
            preflight.main(
                [
                    "--url",
                    server.url,
                    "--authorization-file",
                    str(authorization_file),
                    "--mode",
                    "full",
                    "--trd-env",
                    "SIMULATE",
                    "--account-id",
                    ACCOUNT_ID,
                ]
            )
            == 0
        )
    text = output.getvalue()
    assert "PASS" in text
    assert AUTHORIZATION not in text
    assert ACCOUNT_ID not in text


def test_unreachable_endpoint_and_non_loopback_url_fail_closed(
    authorization_file: Path,
) -> None:
    probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    url = f"http://127.0.0.1:{probe.server_address[1]}/mcp"
    probe.server_close()
    with pytest.raises(preflight.PreflightError, match="unreachable"):
        preflight.run(
            url=url,
            authorization_file=authorization_file,
            mode="startup-safe",
            timeout=0.2,
        )
    with pytest.raises(preflight.PreflightError, match="loopback"):
        preflight.run(
            url="https://public.example/mcp",
            authorization_file=authorization_file,
            mode="startup-safe",
        )


def test_mcp_outage_and_restart_require_fresh_stateless_requests(
    authorization_file: Path,
) -> None:
    with McpFixture() as first:
        port = first.httpd.server_address[1]
        url = first.url
        assert invoke(first, authorization_file)[-1] == "check_health:READ_ONLY"
    with pytest.raises(preflight.PreflightError, match="unreachable"):
        preflight.run(
            url=url,
            authorization_file=authorization_file,
            mode="startup-safe",
            timeout=0.2,
        )
    with McpFixture(port=port) as restarted:
        assert restarted.url == url
        assert invoke(restarted, authorization_file)[-1] == "check_health:READ_ONLY"

    assert first.calls[0]["request"]["id"] == 1
    assert restarted.calls[0]["request"]["id"] == 1
    assert all(call["mcp_session_id"] is None for call in restarted.calls)
