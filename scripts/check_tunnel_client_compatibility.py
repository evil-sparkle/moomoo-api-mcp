#!/usr/bin/env python3
"""Check official tunnel-client control-plane redirect credential confinement.

First verify/install the binary with deploy/tunnel-client/install.py. This check
uses only generated synthetic credentials, an embedded MCP fixture, and ephemeral
loopback HTTP servers. It contacts no OpenAI endpoint. Output contains booleans,
never headers, raw client logs or response bodies. Exit 0 means this narrow check
passed, 1 means credential diversion occurred, and 2 means inconclusive/error.
It is not a substitute for the remaining container acceptance matrix.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def check(binary: Path, *, changed_host: bool, timeout: float) -> dict[str, Any]:
    """Run one isolated real-client scenario without exposing its synthetic key."""
    key = "synthetic-" + secrets.token_hex(24)
    received = threading.Event()
    contacted = threading.Event()
    leaked = threading.Event()

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.headers.get("Authorization") == "Bearer " + key:
                leaked.set()
            received.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"commands":[]}')

    sink_host = "127.0.0.2" if changed_host else "127.0.0.1"
    sink = ThreadingHTTPServer((sink_host, 0), Sink)

    class ControlPlane(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            contacted.set()
            self.send_response(302)
            self.send_header("Location", f"http://{sink_host}:{sink.server_port}/sink")
            self.end_headers()

    control = ThreadingHTTPServer(("127.0.0.1", 0), ControlPlane)
    servers = (sink, control)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in servers
    ]
    for thread in threads:
        thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="tunnel-compat-") as directory:
            root = Path(directory)
            credential = root / "synthetic-key"
            credential.write_text(key, encoding="utf-8")
            credential.chmod(0o600)
            config = root / "fixture.yaml"
            config.write_text(
                "config_version: 1\n"
                "control_plane:\n"
                f"  base_url: http://127.0.0.1:{control.server_port}\n"
                "  tunnel_id: tunnel_0123456789abcdef0123456789abcdef\n"
                f"  api_key: file:{credential}\n"
                "  poll_timeout: 1s\n"
                "health:\n"
                "  listen_addr: 127.0.0.1:0\n"
                f"  url_file: {root}/health-url\n"
                "process:\n"
                f"  pid_file: {root}/client.pid\n"
                "log:\n  level: warn\n  format: json\n"
                "admin_ui:\n  open_browser: false\n",
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [str(binary), "run", "--config", str(config), "--embedded-mcp-stub"],
                cwd=root,
                env={"HOME": directory, "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            forced_kill = False
            try:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and process.poll() is None:
                    if received.wait(0.1):
                        break
                premature_exit = process.poll() is not None
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    forced_kill = True
                    process.kill()
                    process.wait(timeout=5)
            return {
                "scenario": "changed-host-and-port" if changed_host else "changed-port",
                "control_plane_contacted": contacted.is_set(),
                "redirect_sink_contacted": received.is_set(),
                "runtime_key_leaked": leaked.is_set(),
                "client_exited_early": premature_exit,
                "shutdown_required_kill": forced_kill,
            }
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("timeout must be greater than zero and at most 60 seconds")
    try:
        results = [
            check(args.binary.resolve(), changed_host=changed, timeout=args.timeout)
            for changed in (False, True)
        ]
    except (OSError, subprocess.SubprocessError):
        print("INCONCLUSIVE: compatibility fixture or client could not run")
        return 2
    for result in results:
        print(json.dumps(result, sort_keys=True))
    if any(result["runtime_key_leaked"] for result in results):
        print("FAIL: runtime credential reached an unapproved redirect destination")
        return 1
    if any(
        not result["control_plane_contacted"]
        or result["client_exited_early"]
        or result["shutdown_required_kill"]
        for result in results
    ):
        print("INCONCLUSIVE: client did not remain live through the scenario")
        return 2
    print("PASS: no credential diversion observed in these redirect scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
