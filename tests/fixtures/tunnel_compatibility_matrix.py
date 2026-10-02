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
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
            "fixture_errors",
            "body_leaks",
            "cross_credential",
            "mcp_anonymous",
            "target_hits",
            "redirect_hops",
            "proxy_connects",
            "forward_dispatches",
            "auth_retry_anonymous",
            "discovery_successes",
            "list_successes",
            "oauth_requests",
            "oauth_anonymous",
            "proxy_forward_hits",
            "proxy_discovery_hits",
        ),
        0,
    )
    done = threading.Event()
    sent = False
    revoked = False
    redirect_seen = False
    observed = set()
    sink_methods = set()
    loop_hops = {}
    missing_key = "missing-key" in case
    auth_case = case.split("-auth-", 1)[-1] if "-auth-" in case else ""
    proxy_variable = case.split("-var-", 1)[-1] if "-var-" in case else None
    proxy_path = path == "proxy" or path.startswith("mcp-proxy")
    retry_case = "-retry-" in case
    same_origin = "same-origin" in case or "loop" in case
    wait_seconds = 8 if retry_case else 5

    class FixtureServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            _ = request, client_address
            if not isinstance(
                sys.exc_info()[1],
                (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError),
            ):
                counts["fixture_errors"] += 1

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
            if self.command == "CONNECT":
                counts["proxy_connects"] += 1
                counts["runtime_leaks"] += auth == "Bearer " + runtime_key
                counts["mcp_leaks"] += auth == mcp_key
                self.answer(502, {})
                if path not in {"mcp-proxy-forward", "mcp-proxy-discovery"}:
                    done.set()
                return
            if self.server == sink:
                counts["sink"] += 1
                sink_methods.add(self.command)
                counts["body_leaks"] += bool(data)

                counts["runtime_leaks"] += auth == "Bearer " + runtime_key
                counts["mcp_leaks"] += auth == mcp_key
                if path in {"mcp-proxy-forward", "mcp-proxy-discovery"}:
                    # The sink emulates responses locally; it never forwards traffic.
                    counts["proxy_forward_hits"] += b'"tools/call"' in data
                    counts["proxy_discovery_hits"] += ".well-known/" in self.path
                else:
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
            counts["cross_credential"] += (
                auth == mcp_key if is_control else auth == "Bearer " + runtime_key
            )
            if not is_control and auth is None:
                counts["mcp_anonymous"] += 1
            if not is_control and ".well-known/" in self.path:
                counts["oauth_requests"] += 1
                counts["oauth_anonymous"] += auth is None
            endpoint = (
                "poll"
                if is_control and "/poll" in self.path
                else "response"
                if is_control and "/response" in self.path
                else "metadata"
                if is_control
                else "discovery"
                if self.command == "GET"
                else "forward"
                if b'"tools/call"' in data
                else "startup"
            )
            if not is_control and data:
                method = json.loads(data).get("method")
                detailed = {
                    "initialize": "initialize",
                    "tools/list": "tools-list",
                    "notifications/initialized": "notification",
                }.get(method)
                if detailed:
                    observed.add(f"{detailed}:{self.command}:body")
                    if path in {"initialize", "tools-list", "notification"}:
                        endpoint = detailed
            observed.add(f"{endpoint}:{self.command}:{'body' if data else 'empty'}")
            if is_control and auth is None:
                counts["anonymous"] += 1
            selected_auth = endpoint == path and bool(auth_case or retry_case)
            if selected_auth:
                counts["target_hits"] += 1
                if counts["target_hits"] > 1 and auth is None:
                    counts["auth_retry_anonymous"] += 1
                if auth_case or counts["target_hits"] == 1:
                    counts["rejected"] += 1
                    self.answer(int(auth_case or case.rsplit("-", 1)[-1]), {})
                    return
            if is_control and ("wrong-key" in case or revoked):
                counts["rejected"] += 1
                self.answer(401, {})
                done.set()
                return
            expected = "Bearer " + runtime_key if is_control else mcp_key
            if auth != expected or len(self.headers.get_all("Authorization", [])) != 1:
                counts["rejected"] += 1
                self.answer(401, {})
                return
            selected = (
                (
                    path in {"initialize", "tools-list", "notification"}
                    and endpoint == path
                )
                or (path == "control-all" and is_control)
                or (path == "poll" and is_control and "/poll" in self.path)
                or (path == "response" and is_control and self.command == "POST")
                or (path == "metadata" and is_control and endpoint == "metadata")
                or (path == "discovery" and not is_control and self.command == "GET")
                or (path == "startup" and not is_control and self.command == "POST")
                or (
                    path == "forward"
                    and not is_control
                    and sent
                    and b'"tools/call"' in data
                )
            )
            if (same_origin and self.path.startswith("/hop/")) or (
                self.path == "/approved-target" and same_origin
            ):
                selected = True
            if selected and not (auth_case or retry_case):
                redirect_seen = True
                counts["target_hits"] += 1
                if same_origin:
                    counts["redirect_hops"] += 1
                    if self.path == "/approved-target":
                        self.answer(200, {"commands": []})
                        done.set()
                        return
                    hop = (
                        int(self.path.rsplit("/", 1)[-1]) if "/hop/" in self.path else 0
                    )
                    loop_hops[hop] = loop_hops.get(hop, 0) + 1
                    target = f"/hop/{hop + 1}" if "loop" in case else "/approved-target"
                elif "chain" in case and not self.path.startswith("/hop/"):
                    target = "/hop/1"
                else:
                    target = sink_url + "/sink"
                self.send_response(code)
                self.send_header("Location", target)
                self.end_headers()
                return
            if self.path.startswith("/hop/") and "chain" in case:
                self.send_response(code)
                self.send_header("Location", sink_url + "/sink")
                self.end_headers()
                return
            if is_control:
                if self.command == "POST":
                    counts["responses"] += 1
                    envelope = json.loads(data)
                    response = envelope.get("resp_json", {})
                    counts["list_successes"] += (
                        isinstance(response, dict)
                        and response.get("result", {}).get("tools") == []
                    )
                    if envelope.get("resp_type") == "oauth_discovery_response":
                        counts["discovery_successes"] += (
                            isinstance(response, dict)
                            and response.get("resource") == mcp_url
                        )
                        done.set()
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
                    elif (
                        (
                            path in {"normal", "proxy", "auth-negative"}
                            or path.startswith("mcp-proxy")
                        )
                        or retry_case
                    ) and (not retry_case or counts["target_hits"] >= 2):
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
                    if commands and (path == "tools-list" or "MD1-tools-list" in case):
                        commands[0]["jsonrpc"]["method"] = "tools/list"
                        commands[0]["jsonrpc"]["params"] = {}
                    if commands and (
                        "oauth" in case
                        or path == "mcp-proxy-discovery"
                        or "static-negative-discovery" in case
                    ):
                        commands[0].pop("jsonrpc")
                        commands[0]["command_type"] = "oauth_discovery"
                    if commands and "MA1-wrong" in case:
                        commands[0]["headers"]["Authorization"] = [
                            "Bearer synthetic-wrong"
                        ]
                    if commands and "MA1-conflicting" in case:
                        commands[0]["headers"]["Authorization"] = [
                            mcp_key,
                            "Bearer synthetic-wrong",
                        ]
                    if commands and "MA1-ambiguity-" in case:
                        kind = case.split("MA1-ambiguity-", 1)[1]
                        variants = {
                            "duplicate-valid": [mcp_key, mcp_key],
                            "wrong-first": ["Bearer synthetic-wrong", mcp_key],
                            "comma": [mcp_key + ", Bearer synthetic-wrong"],
                            "empty": [""],
                            "basic": ["Basic synthetic-wrong"],
                            "mixed-case": ["Bearer synthetic-wrong"],
                        }
                        header = (
                            "authorization" if kind == "mixed-case" else "Authorization"
                        )
                        commands[0]["headers"][header] = variants[kind]
                    time.sleep(0.05)
                    self.answer(200, {"commands": commands})
                else:
                    self.answer(200, {"id": "tunnel_0123456789abcdef0123456789abcdef"})
            elif self.command == "GET":
                if ".well-known/" in self.path:
                    self.answer(200, {"resource": mcp_url})
                else:
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
                    counts["forward_dispatches"] += 1
                    if "response-lost" in case:
                        self.close_connection = True
                        return
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
        do_CONNECT = handle_request

    sink_address = "127.0.0.1" if "same-host-port" in case else "127.0.0.2"
    sink = FixtureServer((sink_address, 0), Handler)
    control = FixtureServer(("127.0.0.1", 0), Handler)
    mcp = FixtureServer(("127.0.0.1", 0), Handler)
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
    sink_scheme = (
        "https" if secure and "downgrade" not in case and not proxy_path else "http"
    )
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
                "subjectAltName=IP:127.0.0.1,IP:127.0.0.2,DNS:control.fixture.test,DNS:mcp.fixture.test,DNS:sink.control.fixture.test",
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
    if path.startswith("mcp-proxy"):
        mcp_host = "mcp.fixture.test"
    elif "subdomain" in case and path in {
        "discovery",
        "startup",
        "forward",
        "initialize",
        "tools-list",
        "notification",
    }:
        mcp_host = "control.fixture.test"
    sink_url = f"{sink_scheme}://{sink_host}:{sink.server_port}"
    mcp_url = f"{mcp_scheme}://{mcp_host}:{mcp.server_port}/mcp"
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
            if "static-negative" in case:
                scope = (
                    "discovery_extra_headers"
                    if "discovery" in case
                    else "extra_headers"
                )
                block = f"  {scope}:\n    Authorization: file:{root}/mcp\n"
                if "missing" in case:
                    text = config.read_text().replace(block, "")
                    if "discovery" in case:
                        # Discovery inherits runtime headers when no override exists.
                        # Remove both to exercise genuinely missing authentication.
                        text = text.replace(
                            f"  extra_headers:\n    Authorization: file:{root}/mcp\n",
                            "",
                        )
                    config.write_text(text)
                else:
                    wrong = root / "wrong"
                    wrong.write_text("Bearer synthetic-wrong")
                    wrong.chmod(0o600)
                    config.write_text(
                        config.read_text().replace(
                            block, block.replace("/mcp\n", "/wrong\n")
                        )
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
            if proxy_path:
                for name in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                ):
                    environment[name] = sink_url
            if proxy_variable:
                environment = {
                    k: v for k, v in environment.items() if "proxy" not in k.lower()
                }
                if proxy_variable in {"NO_PROXY", "no_proxy"}:
                    environment["HTTP_PROXY"] = sink_url
                    environment["HTTPS_PROXY"] = sink_url
                    environment[proxy_variable] = (
                        "control.fixture.test,mcp.fixture.test"
                    )
                else:
                    environment[proxy_variable] = sink_url
            if missing_key:
                if "missing-key-file" in case:
                    (root / "runtime").unlink()
                elif "missing-key-empty" in case:
                    (root / "runtime").write_text("")
                else:
                    config.write_text(
                        config.read_text().replace(
                            f"  api_key: file:{root}/runtime\n", ""
                        )
                    )
            process = subprocess.Popen(
                [
                    str(binary),
                    "doctor" if "doctor" in case else "run",
                    "--config",
                    str(config),
                ],
                env=environment,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline and process.poll() is None:
                    if done.wait(0.05):
                        break
                natural_exit = process.poll()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            leak = any(
                counts[k]
                for k in (
                    "runtime_leaks",
                    "mcp_leaks",
                    "body_leaks",
                    "cross_credential",
                )
            )
            if (
                counts["fixture_errors"]
                or (
                    path == "mcp-proxy-forward"
                    and proxy_variable in {"HTTP_PROXY", "http_proxy"}
                    and not counts["proxy_forward_hits"]
                )
                or (
                    path == "mcp-proxy-discovery"
                    and proxy_variable in {"HTTP_PROXY", "http_proxy"}
                    and not counts["proxy_discovery_hits"]
                )
            ):
                status = "INCONCLUSIVE"
            elif leak:
                status = "FAIL"
            elif (same_origin or "chain" in case) and counts["mcp_anonymous"]:
                status = "FAIL"  # redirected request lost required MCP authentication
            elif counts["fixture_errors"]:
                status = "INCONCLUSIVE"
            elif "static-negative" in case:
                exercised = (
                    counts["oauth_requests"]
                    if "discovery" in case
                    else counts["responses"]
                )
                status = (
                    "INCONCLUSIVE"
                    if not exercised
                    else (
                        "PASS"
                        if counts["rejected"]
                        and not counts["forward_dispatches"]
                        and not counts["discovery_successes"]
                        else "FAIL"
                    )
                )
            elif missing_key:
                status = (
                    "PASS"
                    if natural_exit not in (None, 0) and not counts["control"]
                    else "FAIL"
                )
            elif counts["auth_retry_anonymous"]:
                status = "FAIL"
            elif "doctor" in case:
                status = (
                    "FAIL"
                    if counts["oauth_anonymous"]
                    else ("PASS" if counts["oauth_requests"] else "INCONCLUSIVE")
                )
            elif "MD1-tools-list" in case:
                status = "PASS" if counts["list_successes"] else "FAIL"
            elif (
                "oauth" in case
                or path == "mcp-proxy-discovery"
                or "static-negative-discovery" in case
            ):
                status = (
                    "PASS"
                    if counts["discovery_successes"] and not counts["oauth_anonymous"]
                    else "FAIL"
                )
            elif "response-lost" in case:
                status = (
                    "PASS"
                    if counts["forward_dispatches"] == 1
                    and counts["responses"]
                    and not counts["successes"]
                    else "FAIL"
                )
            elif auth_case or retry_case:
                status = (
                    "PASS"
                    if counts["target_hits"]
                    and not counts["auth_retry_anonymous"]
                    and not counts["anonymous"]
                    else "INCONCLUSIVE"
                )
            elif "loop" in case:
                status = (
                    "PASS"
                    if redirect_seen and max(loop_hops, default=100) <= 9
                    else "FAIL"
                )
            elif same_origin:
                status = (
                    "PASS"
                    if redirect_seen and counts["redirect_hops"] >= 2
                    else "INCONCLUSIVE"
                )
            elif proxy_variable and counts["proxy_connects"]:
                # Refused anonymous CONNECT; not proof of HTTPS header visibility.
                status = "PASS"
            elif path == "auth-negative":
                status = (
                    "PASS"
                    if counts["rejected"]
                    and counts["responses"]
                    and not counts["successes"]
                    and not counts["forward_dispatches"]
                    else "FAIL"
                )
            elif "wrong-key" in case or "revoked" in case:
                status = (
                    "PASS"
                    if counts["rejected"] and not counts["anonymous"]
                    else "INCONCLUSIVE"
                )
            elif path in {"normal", "proxy", "auth-negative"} or path.startswith(
                "mcp-proxy"
            ):
                status = "PASS" if counts["successes"] else "INCONCLUSIVE"
            else:
                status = "PASS" if redirect_seen else "INCONCLUSIVE"
            return {
                "case": case,
                "status": status,
                "redirect_exercised": redirect_seen,
                "observed_requests": sorted(observed),
                "sink_methods": sorted(sink_methods),
                "max_loop_hop": max(loop_hops, default=0),
                "natural_exit": natural_exit,
                **counts,
            }
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        tls_directory.cleanup()


def case_definitions():
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
    # Explicit finite cross-product for every HTTP endpoint used by this integration.
    for group, path in (
        ("CP6", "metadata"),
        ("CP6", "poll"),
        ("CP6", "response"),
        ("MD2", "discovery"),
        ("MD2", "initialize"),
        ("MD2", "tools-list"),
        ("MD2", "notification"),
        ("MF2", "forward"),
    ):
        for variant in (
            "changed-host",
            "same-host-port",
            "subdomain",
            "same-origin",
            "chain",
            "loop",
            "TLS-redirect",
            "TLS-downgrade",
        ):
            if group != "CP6" and "TLS" in variant:
                variant = "MCP-" + variant
            for code in (301, 302, 303, 307, 308):
                cases.append((f"{group}-complete-{path}-{variant}-{code}", path, code))
    for group, path in (("CP7", "proxy"), ("MP1", "mcp-proxy")):
        for scheme in ("HTTP", "TLS" if path == "proxy" else "MCP-TLS"):
            for variable in (
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
                "ALL_PROXY",
                "all_proxy",
                "NO_PROXY",
                "no_proxy",
            ):
                cases.append((f"{group}-{scheme}-var-{variable}", path, 302))
    for path in ("mcp-proxy-forward", "mcp-proxy-discovery"):
        for variable in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
            "NO_PROXY",
            "no_proxy",
        ):
            cases.append((f"MP1-{path}-var-{variable}", path, 302))
    for kind in ("omitted", "empty", "file"):
        cases.append((f"CP8-missing-key-{kind}", "normal", 302))
    for path in ("metadata", "poll", "response", "discovery", "initialize", "forward"):
        for status in (401, 403):
            cases.append((f"AUTH-{path}-auth-{status}", path, 302))
        for status in (429, 503):
            cases.append((f"RETRY-{path}-retry-{status}", path, 302))
    for kind in (
        "duplicate-valid",
        "wrong-first",
        "comma",
        "empty",
        "basic",
        "mixed-case",
    ):
        cases.append((f"MA1-ambiguity-{kind}", "auth-negative", 302))
    for scope in ("discovery", "runtime"):
        for kind in ("missing", "wrong"):
            cases.append((f"MA1-static-negative-{scope}-{kind}", "normal", 302))
    cases += [
        ("MD1-oauth-authenticated", "normal", 302),
        ("MD1-tools-list-authenticated", "normal", 302),
        ("MD1-doctor-authenticated", "normal", 302),
        ("MA1-forward-response-lost", "normal", 302),
    ]
    return cases


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--case")
    parser.add_argument("--match", help="Run case IDs containing this text")
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    cases = [
        row
        for row in case_definitions()
        if (not args.case or row[0] == args.case)
        and (not args.match or args.match in row[0])
    ]
    if not cases:
        parser.error("unknown scenario")

    def run(row):
        return scenario(args.binary, *row)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(run, cases):
            print(json.dumps(result, sort_keys=True), flush=True)
            results.append(result)
    print(
        json.dumps(
            {
                "summary": {
                    status: sum(r["status"] == status for r in results)
                    for status in ("PASS", "FAIL", "INCONCLUSIVE")
                },
                "total": len(results),
            }
        ),
        flush=True,
    )
    return 1 if any(r["status"] != "PASS" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
