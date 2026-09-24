"""Isolated protocol and failure tests for the private MCP preflight."""

from __future__ import annotations

import contextlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts import private_chatgpt_preflight as preflight

AUTHORIZATION = "Bearer fixture-mcp-credential"
ACCOUNT_ID = "9007199254740993"


class McpFixture:
    """A strict authenticated MCP endpoint backed only by fixture data."""

    def __init__(
        self,
        *,
        trading_mode: str | None = "READ_ONLY",
        gateway_status: str = "connected",
        authorization: str = AUTHORIZATION,
        port: int = 0,
    ) -> None:
        self.trading_mode = trading_mode
        self.gateway_status = gateway_status
        self.authorization = authorization
        self.calls: list[dict[str, Any]] = []
        self.override: Any = None
        self.fail_account = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    request = json.loads(self.rfile.read(length))
                except ValueError:
                    request = {}
                fixture.calls.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "host": self.headers.get("Host"),
                        "mcp_session_id": self.headers.get("Mcp-Session-Id"),
                        "request": request,
                    }
                )
                if self.headers.get("Authorization") != fixture.authorization:
                    self._answer(401, {"detail": "Unauthorized"})
                    return
                if fixture.override is not None:
                    status, body, content_type = fixture.override(request)
                    self._answer(status, body, content_type)
                    return
                request_id = request.get("id")
                method = request.get("method")
                if method == "initialize":
                    result = {
                        "protocolVersion": preflight.PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {"name": "check_health"},
                            {"name": "get_accounts"},
                            {"name": "get_positions"},
                        ]
                    }
                elif method == "tools/call":
                    params = request.get("params", {})
                    name = params.get("name")
                    if name == "check_health":
                        health = {"status": fixture.gateway_status}
                        if fixture.trading_mode is not None:
                            health["trading_mode"] = fixture.trading_mode
                        result = {
                            "content": [],
                            "structuredContent": health,
                            "isError": False,
                        }
                    elif name == "get_accounts":
                        if fixture.fail_account:
                            result = {"content": [], "isError": True}
                            self._answer(
                                200,
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "result": result,
                                },
                            )
                            return
                        result = {
                            "content": [],
                            "structuredContent": {
                                "result": [
                                    {
                                        "acc_id": ACCOUNT_ID,
                                        "trd_env": "SIMULATE",
                                    }
                                ]
                            },
                            "isError": False,
                        }
                    elif name == "get_positions":
                        result = {
                            "content": [],
                            "structuredContent": {"result": []},
                            "isError": False,
                        }
                    else:
                        result = {"content": [], "isError": True}
                else:
                    self._answer(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "unknown"},
                        },
                    )
                    return
                self._answer(
                    200,
                    {"jsonrpc": "2.0", "id": request_id, "result": result},
                )

            def _answer(
                self,
                status: int,
                body: Any,
                content_type: str = "application/json",
            ) -> None:
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: Any) -> None:
                _ = (format, args)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> McpFixture:
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


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
) -> list[str]:
    return preflight.run(
        url=server.url,
        authorization_file=authorization_file,
        mode=mode,
        trd_env="SIMULATE" if mode == "full" else None,
        account_id=ACCOUNT_ID if mode == "full" else None,
    )


def test_startup_safe_validates_real_mcp_results_and_authentication(
    authorization_file: Path,
) -> None:
    with McpFixture() as server:
        milestones = invoke(server, authorization_file)

    assert milestones == ["initialize", "tools/list", "check_health:READ_ONLY"]
    assert [call["request"]["method"] for call in server.calls] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    assert {call["authorization"] for call in server.calls} == {AUTHORIZATION}
    assert server.calls[-1]["request"]["params"]["name"] == "check_health"


def test_full_mode_validates_selected_account_and_positions(
    authorization_file: Path,
) -> None:
    with McpFixture() as server:
        milestones = invoke(server, authorization_file, mode="full")

    assert milestones[-2:] == ["get_accounts:selected", "get_positions"]
    calls = [
        call["request"]["params"]
        for call in server.calls
        if call["request"]["method"] == "tools/call"
    ]
    assert calls[-1] == {
        "name": "get_positions",
        "arguments": {"trd_env": "SIMULATE", "acc_id": ACCOUNT_ID},
    }


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL", None])
def test_startup_safe_fails_closed_unless_health_proves_read_only(
    authorization_file: Path, mode: str | None
) -> None:
    with (
        McpFixture(trading_mode=mode) as server,
        pytest.raises(preflight.PreflightError, match="READ_ONLY"),
    ):
        invoke(server, authorization_file)


@pytest.mark.parametrize("gateway_status", ["disconnected", "degraded"])
def test_startup_safe_tolerates_unavailable_opend_but_full_fails_honestly(
    authorization_file: Path, gateway_status: str
) -> None:
    with McpFixture(gateway_status=gateway_status) as server:
        assert invoke(server, authorization_file)[-1] == "check_health:READ_ONLY"
        server.fail_account = True
        with pytest.raises(preflight.PreflightError):
            invoke(server, authorization_file, mode="full")


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
