"""Synthetic component coverage, separate from the official-client release gate."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import moomoo_mcp.server as server
from moomoo_mcp.settings import load_settings
from scripts import private_chatgpt_preflight as preflight


@pytest.mark.parametrize(
    "value,expected", [(None, False), ("", False), ("0", False), ("1", True)]
)
def test_host_setting(value, expected):
    env = {} if value is None else {"MCP_ALLOW_CHATGPT_TUNNEL_HOST": value}
    assert load_settings(env).allow_chatgpt_tunnel_host is expected


@pytest.mark.parametrize("value", ["true", "*", "yes", "2"])
def test_invalid_host_setting(value):
    with pytest.raises(ValueError, match="MCP_ALLOW_CHATGPT_TUNNEL_HOST"):
        load_settings({"MCP_ALLOW_CHATGPT_TUNNEL_HOST": value})


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "host", ["moomoo-mcp:8000", "moomoo-mcp:8001", "other:8000", "127.0.0.1:8000"]
)
def test_exact_host(enabled, host):
    server.mcp._session_manager = None
    app = server.create_streamable_http_app(
        auth_token="synthetic", allow_chatgpt_tunnel_host=enabled
    )
    with patch.object(server, "get_services"), TestClient(app) as client:
        result = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": "Bearer synthetic",
                "Host": host,
                "Accept": "application/json",
            },
        )
    assert result.status_code == (
        200
        if host == "127.0.0.1:8000" or enabled and host == "moomoo-mcp:8000"
        else 421
    )
    server.mcp._session_manager = None


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Bearer synthetic"), ("Authorization", "Bearer wrong")],
        [("Authorization", "Bearer wrong"), ("Authorization", "Bearer synthetic")],
        [("Authorization", "Bearer synthetic"), ("Authorization", "Bearer synthetic")],
    ],
)
def test_duplicate_bearers_rejected(headers):
    server.mcp._session_manager = None
    app = server.create_streamable_http_app(auth_token="synthetic")
    with patch.object(server, "get_services") as services, TestClient(app) as client:
        assert client.post("/mcp", headers=headers).status_code == 401
        services.assert_not_called()
    server.mcp._session_manager = None


@pytest.mark.parametrize(
    "url",
    [
        "http://moomoo-mcp:8001/mcp",
        "http://moomoo-mcp/mcp",
        "http://moomoo-mcp:8000/mcp?x=1",
        "http://moomoo-mcp:8000/mcp#x",
        "http://x@moomoo-mcp:8000/mcp",
        "http://10.0.0.1:8000/mcp",
        "http://alias:8000/mcp",
        "http://moomoo-mcp:8000/other",
    ],
)
def test_only_exact_compose_url(url):
    with pytest.raises(preflight.PreflightError):
        preflight.validate_url(url, allow_compose_mcp=True)


def test_compose_url_requires_explicit_opt_in():
    with pytest.raises(preflight.PreflightError):
        preflight.validate_url(preflight.COMPOSE_URL)
    preflight.validate_url(preflight.COMPOSE_URL, allow_compose_mcp=True)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_never_forward_bearer(status):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(status)
            self.send_header("Location", "/sink")
            self.end_headers()

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            _ = format, args

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(preflight.PreflightError, match=f"HTTP {status}"):
            preflight.McpClient(
                f"http://127.0.0.1:{httpd.server_port}/mcp", "Bearer synthetic"
            ).initialize()
        assert hits == ["/mcp"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_opt_in_does_not_trust_origins():
    server.mcp._session_manager = None
    app = server.create_streamable_http_app(
        auth_token="synthetic", allow_chatgpt_tunnel_host=True
    )
    with patch.object(server, "get_services"), TestClient(app) as client:
        result = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": "Bearer synthetic",
                "Host": "moomoo-mcp:8000",
                "Accept": "application/json",
                "Origin": "http://moomoo-mcp:8000",
            },
        )
        assert result.status_code == 403
    server.mcp._session_manager = None


def test_preflight_ignores_inherited_proxy(monkeypatch, tmp_path):
    from tests.test_private_chatgpt_preflight import AUTHORIZATION, McpFixture

    auth = tmp_path / "synthetic-authorization"
    auth.write_text(AUTHORIZATION)
    with McpFixture() as endpoint, McpFixture() as trap:
        for key in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            monkeypatch.setenv(key, trap.url)
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        assert (
            preflight.run(
                url=endpoint.url, authorization_file=auth, mode="startup-safe"
            )[-1]
            == "check_health:READ_ONLY"
        )
        assert trap.calls == []


def test_bounded_response(monkeypatch, tmp_path):
    from tests.test_private_chatgpt_preflight import AUTHORIZATION, McpFixture

    auth = tmp_path / "synthetic-authorization"
    auth.write_text(AUTHORIZATION)
    monkeypatch.setattr(preflight, "MAX_RESPONSE_BYTES", 128)
    with McpFixture() as endpoint:
        endpoint.override = lambda _: (200, b" " * 129, "application/json")
        with pytest.raises(preflight.PreflightError, match="size limit"):
            preflight.run(
                url=endpoint.url, authorization_file=auth, mode="startup-safe"
            )


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_preflight_refuses_unbounded_timeout_before_credential_read(tmp_path, timeout):
    with pytest.raises(preflight.PreflightError, match="Timeout"):
        preflight.run(
            url=preflight.DEFAULT_URL,
            authorization_file=tmp_path / "missing",
            mode="startup-safe",
            timeout=timeout,
        )


def test_direct_preflight_client_also_requires_exact_destination_opt_in():
    with pytest.raises(preflight.PreflightError):
        preflight.McpClient(preflight.COMPOSE_URL, "Bearer synthetic")
    preflight.McpClient(
        preflight.COMPOSE_URL, "Bearer synthetic", allow_compose_mcp=True
    )
    with pytest.raises(preflight.PreflightError):
        preflight.McpClient(
            "http://10.0.0.1:8000/mcp", "Bearer synthetic", allow_compose_mcp=True
        )
