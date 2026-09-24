"""Process isolation for tunnel exit and control-plane degradation."""

from __future__ import annotations

import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "tests" / "fixtures" / "tunnel_runtime_stub.py"
AUTHORIZATION = "Bearer local-mcp-fixture"


class LocalAuthenticatedMcp:
    """Minimal local path used to prove tunnel process independence."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.headers.get("Authorization") != AUTHORIZATION:
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                _ = (format, args)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> LocalAuthenticatedMcp:
        self.thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def status(self, authorization: str | None) -> int:
        headers = {} if authorization is None else {"Authorization": authorization}
        request = Request(self.url, data=b"{}", method="POST", headers=headers)
        try:
            with urlopen(request, timeout=1) as response:  # noqa: S310
                return response.status
        except HTTPError as exc:
            return exc.code


def start_stub(tmp_path: Path, state: str) -> tuple[subprocess.Popen[str], str]:
    url_file = tmp_path / f"{state}-{time.monotonic_ns()}.url"
    process = subprocess.Popen(
        [
            sys.executable,
            str(STUB),
            "--url-file",
            str(url_file),
            "--control-plane",
            state,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if url_file.exists():
            return process, url_file.read_text(encoding="utf-8")
        if process.poll() is not None:
            break
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=2)
    raise AssertionError("tunnel fixture did not become healthy")


def http_status(url: str) -> int:
    try:
        with urlopen(url, timeout=1) as response:  # noqa: S310
            return response.status
    except HTTPError as exc:
        return exc.code


def stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    process.wait(timeout=3)


def test_tunnel_exit_and_restart_do_not_stop_or_deauthenticate_local_mcp(
    tmp_path: Path,
) -> None:
    with LocalAuthenticatedMcp() as mcp:
        process, tunnel_url = start_stub(tmp_path, "available")
        assert http_status(tunnel_url + "/healthz") == 200
        assert http_status(tunnel_url + "/readyz") == 200
        assert mcp.status(None) == 401
        assert mcp.status(AUTHORIZATION) == 200

        stop(process)
        with pytest.raises(URLError):
            urlopen(tunnel_url + "/readyz", timeout=0.2)  # noqa: S310
        assert mcp.status(None) == 401
        assert mcp.status(AUTHORIZATION) == 200

        restarted, restarted_url = start_stub(tmp_path, "available")
        try:
            assert http_status(restarted_url + "/readyz") == 200
            assert mcp.status(AUTHORIZATION) == 200
        finally:
            stop(restarted)


def test_control_plane_unavailability_fails_readiness_only(tmp_path: Path) -> None:
    with LocalAuthenticatedMcp() as mcp:
        process, tunnel_url = start_stub(tmp_path, "unavailable")
        try:
            assert http_status(tunnel_url + "/healthz") == 200
            assert http_status(tunnel_url + "/readyz") == 503
            assert mcp.status(None) == 401
            assert mcp.status(AUTHORIZATION) == 200
        finally:
            stop(process)
