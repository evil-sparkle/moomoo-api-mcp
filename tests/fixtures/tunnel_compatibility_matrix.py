"""Additional real-client cases; run in Docker --network none, never live endpoints.

Separate from the immutable historical reproduction. Emits only case IDs/counts
and booleans. A failing or inconclusive case keeps this job red. Fixture HTTP is
not evidence about the behavior of the real OpenAI HTTPS control plane.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def scenario(binary: Path, case: str, path: str, code: int = 302) -> dict:
    runtime_key = "synthetic-runtime-" + secrets.token_hex(16)
    mcp_key = "Bearer synthetic-mcp-" + secrets.token_hex(16)
    counts = dict.fromkeys(
        (
            "control",
            "mcp",
            "sink",
            "runtime_leaks",
            "mcp_leaks",
            "responses",
            "successes",
            "anonymous",
            "rejected",
        ),
        0,
    )
    done = threading.Event()
    sent = False
    revoked = False
    redirect_seen = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            _ = format, args

        def answer(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(raw)

        def handle_request(self):
            nonlocal sent, redirect_seen, revoked
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b";", 1)[0], 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
                    assert sum(map(len, chunks)) < 1024 * 1024
                data = b"".join(chunks)
            else:
                data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            auth = self.headers.get("Authorization")
            if self.server == sink:
                counts["sink"] += 1
                counts["runtime_leaks"] += auth == "Bearer " + runtime_key
                counts["mcp_leaks"] += auth == mcp_key
                if "chain" in case and self.path != "/final":
                    self.send_response(code)
                    self.send_header("Location", sink_url + "/final")
                    self.end_headers()
                else:
                    self.answer(200, {"commands": []})
                    done.set()
                return
            is_control = self.server == control
            counts["control" if is_control else "mcp"] += 1
            if is_control and auth is None:
                counts["anonymous"] += 1
            if is_control and ("wrong-key" in case or revoked):
                counts["rejected"] += 1
                self.answer(401, {})
                done.set()
                return
            expected = "Bearer " + runtime_key if is_control else mcp_key
            if auth != expected:
                counts["rejected"] += 1
                self.answer(401, {})
                return
            selected = (
                (path == "control-all" and is_control)
                or (path == "poll" and is_control and "/poll" in self.path)
                or (path == "response" and is_control and self.command == "POST")
                or (path == "metadata" and is_control and "/poll" not in self.path)
                or (path == "discovery" and not is_control and self.command == "GET")
                or (path == "startup" and not is_control and self.command == "POST")
                or (
                    path == "forward"
                    and not is_control
                    and sent
                    and b'"tools/call"' in data
                )
            )
            if selected:
                redirect_seen = True
                self.send_response(code)
                self.send_header("Location", sink_url + "/sink")
                self.end_headers()
                return
            if is_control:
                if self.command == "POST":
                    counts["responses"] += 1
                    response = json.loads(data).get("resp_json", {})
                    counts["successes"] += (
                        isinstance(response, dict)
                        and response.get("result", {})
                        .get("structuredContent", {})
                        .get("trading_mode")
                        == "READ_ONLY"
                    )
                    self.answer(200, {})
                    if "revoked" in case:
                        revoked = True
                    elif path in {"normal", "proxy", "mcp-proxy", "auth-negative"}:
                        done.set()
                elif "/poll" in self.path:
                    commands = []
                    if not sent:
                        sent = True
                        commands = [
                            {
                                "request_id": "synthetic-1",
                                "shard_token": "synthetic",
                                "command_type": "jsonrpc",
                                "channel": "main",
                                "headers": {"Accept": ["application/json"]},
                                "jsonrpc": {
                                    "jsonrpc": "2.0",
                                    "id": 99,
                                    "method": "tools/call",
                                    "params": {"name": "check_health", "arguments": {}},
                                },
                            }
                        ]
                    if commands and "MA1-wrong" in case:
                        commands[0]["headers"]["Authorization"] = [
                            "Bearer synthetic-wrong"
                        ]
                    if commands and "MA1-conflicting" in case:
                        commands[0]["headers"]["Authorization"] = [
                            mcp_key,
                            "Bearer synthetic-wrong",
                        ]
                    time.sleep(0.05)
                    self.answer(200, {"commands": commands})
                else:
                    self.answer(200, {"id": "tunnel_0123456789abcdef0123456789abcdef"})
            elif self.command == "GET":
                self.answer(404, {})
            else:
                if not data:
                    self.answer(202, {})
                    return
                request = json.loads(data)
                result = (
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "synthetic", "version": "1"},
                    }
                    if request["method"] == "initialize"
                    else {"tools": []}
                )
                if request["method"] == "tools/call":
                    result = {
                        "content": [],
                        "isError": False,
                        "structuredContent": {"trading_mode": "READ_ONLY"},
                    }
                self.answer(
                    200, {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
                )

        do_GET = handle_request
        do_POST = handle_request

    sink_address = "127.0.0.1" if "same-host-port" in case else "127.0.0.2"
    sink = ThreadingHTTPServer((sink_address, 0), Handler)
    control = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    mcp = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    sink_host = "sink.control.fixture.test" if "subdomain" in case else sink_address
    control_host = (
        "control.fixture.test"
        if "subdomain" in case or path == "proxy"
        else "127.0.0.1"
    )
    tls_directory = tempfile.TemporaryDirectory(prefix="synthetic-tls-")
    tls_root = Path(tls_directory.name)
    secure = "TLS" in case
    control_scheme = "https" if secure else "http"
    sink_scheme = "https" if secure and "downgrade" not in case else "http"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if secure:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(tls_root / "key"),
                "-out",
                str(tls_root / "ca"),
                "-days",
                "1",
                "-subj",
                "/CN=synthetic-local-fixture",
                "-addext",
                "subjectAltName=IP:127.0.0.1,IP:127.0.0.2",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        context.load_cert_chain(tls_root / "ca", tls_root / "key")
        control.socket = context.wrap_socket(control.socket, server_side=True)
        if sink_scheme == "https":
            sink.socket = context.wrap_socket(sink.socket, server_side=True)
    mcp_scheme = "https" if "MCP-TLS" in case else "http"
    if mcp_scheme == "https":
        mcp.socket = context.wrap_socket(mcp.socket, server_side=True)
    mcp_host = "127.0.0.1"
    if path == "mcp-proxy":
        mcp_host = "mcp.fixture.test"
    elif "subdomain" in case and path in {"discovery", "startup", "forward"}:
        mcp_host = "control.fixture.test"
    sink_url = f"{sink_scheme}://{sink_host}:{sink.server_port}"
    servers = (sink, control, mcp)
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="synthetic-matrix-") as directory:
            root = Path(directory)
            for name, value in (("runtime", runtime_key), ("mcp", mcp_key)):
                p = root / name
                p.write_text(value)
                p.chmod(0o600)
            config = root / "client.yaml"
            config.write_text(
                f"config_version: 1\ncontrol_plane:\n  base_url: {control_scheme}://{control_host}:{control.server_port}\n"
                f"  api_key: file:{root}/runtime\n"
                "  tunnel_id: tunnel_0123456789abcdef0123456789abcdef\n"
                "  poll_timeout: 1s\nhealth:\n  listen_addr: 127.0.0.1:0\n"
                f"  url_file: {root}/health\nprocess:\n  pid_file: {root}/pid\n"
                "admin_ui:\n  open_browser: false\n"
                "log:\n  level: warn\n  format: json\n"
                f"mcp:\n  server_urls:\n    - channel: main\n      url: {mcp_scheme}://{mcp_host}:{mcp.server_port}/mcp\n"
                f"  extra_headers:\n    Authorization: file:{root}/mcp\n"
                f"  discovery_extra_headers:\n    Authorization: file:{root}/mcp\n"
                "  startup_wait_timeout: 2s\n"
            )
            if "MA1-missing" in case:
                config.write_text(
                    config.read_text().replace(
                        f"  extra_headers:\n    Authorization: file:{root}/mcp\n", ""
                    )
                )
            environment = {"HOME": directory, "PATH": "/usr/bin:/bin"}
            if secure:
                environment["SSL_CERT_FILE"] = str(tls_root / "ca")
            if path in {"proxy", "mcp-proxy"}:
                for name in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                ):
                    environment[name] = sink_url
            process = subprocess.Popen(
                [str(binary), "run", "--config", str(config)],
                env=environment,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                done.wait(5)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if not counts["control"] and not counts["mcp"]:
                doctor = subprocess.run(
                    [str(binary), "doctor", "--config", str(config), "--json"],
                    env=environment,
                    cwd=root,
                    capture_output=True,
                    timeout=15,
                )
                try:
                    report = json.loads(doctor.stdout)
                    print(
                        json.dumps(
                            {
                                "case": case,
                                "doctor_exit": doctor.returncode,
                                "checks": [
                                    {
                                        "id": c.get("id"),
                                        "status": c.get("status"),
                                    }
                                    for c in report.get("checks", [])
                                ],
                            }
                        ),
                        flush=True,
                    )
                except (ValueError, AttributeError):
                    print(
                        json.dumps({"case": case, "doctor_exit": doctor.returncode}),
                        flush=True,
                    )
            leak = counts["runtime_leaks"] + counts["mcp_leaks"] > 0
            if leak:
                status = "FAIL"
            elif path == "auth-negative":
                status = (
                    "PASS"
                    if counts["rejected"]
                    and counts["responses"]
                    and not counts["successes"]
                    else "INCONCLUSIVE"
                )
            elif "wrong-key" in case or "revoked" in case:
                status = (
                    "PASS"
                    if counts["rejected"] and not counts["anonymous"]
                    else "INCONCLUSIVE"
                )
            elif path in {"normal", "proxy", "mcp-proxy", "auth-negative"}:
                status = "PASS" if counts["successes"] else "INCONCLUSIVE"
            else:
                status = "PASS" if redirect_seen else "INCONCLUSIVE"
            return {
                "case": case,
                "status": status,
                "redirect_exercised": redirect_seen,
                **counts,
            }
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        tls_directory.cleanup()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--case")
    args = parser.parse_args()
    cases = [
        ("CP8-normal-MD1-MF1", "normal", 302),
        ("CP8-wrong-key", "normal", 302),
        ("CP8-revoked-key", "normal", 302),
        ("MP1-inherited-MCP-proxies", "mcp-proxy", 302),
        ("CP3-subdomain", "control-all", 302),
        ("CP4-chain", "control-all", 302),
        ("CP7-inherited-proxies", "proxy", 302),
    ]
    cases += [
        (f"CP4-code-{code}", "control-all", code) for code in (301, 303, 307, 308)
    ]
    cases += [
        ("CP5-TLS-normal", "normal", 302),
        ("CP5-TLS-redirect", "control-all", 302),
        ("CP5-TLS-downgrade", "control-all", 302),
    ]
    cases += [(f"CP6-{path}", path, 302) for path in ("poll", "metadata", "response")]
    cases += [
        (f"MD2-{path}-{code}", path, code)
        for path in ("discovery", "startup")
        for code in (301, 302, 303, 307, 308)
    ]
    cases += [
        (f"MF2-forward-{code}", "forward", code) for code in (301, 302, 303, 307, 308)
    ]
    cases += [
        (f"{group}-same-host-port-{code}", path, code)
        for group, path in (
            ("MD2-startup", "startup"),
            ("MD2-discovery", "discovery"),
            ("MF2", "forward"),
        )
        for code in (301, 302, 303, 307, 308)
    ]
    cases += [
        (f"{group}-{variant}", path, 302)
        for group, path in (
            ("MD2", "discovery"),
            ("MD2-startup", "startup"),
            ("MF2", "forward"),
        )
        for variant in ("subdomain", "chain", "MCP-TLS-redirect", "MCP-TLS-downgrade")
    ]
    cases += [
        (f"MA1-{kind}", "auth-negative", 302)
        for kind in ("missing", "wrong", "conflicting")
    ]
    results = []
    for case, path, code in cases:
        if args.case and args.case != case:
            continue
        result = scenario(args.binary, case, path, code)
        print(json.dumps(result, sort_keys=True), flush=True)
        results.append(result)
    if not results:
        parser.error("unknown scenario")
    return 1 if any(r["status"] != "PASS" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
