"""Verify MCP probes against a real local HTTP server with synthetic responses."""

import contextlib
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import deployment_probe as probe  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        del args

    def do_POST(self):
        self.server.paths.append(self.path)
        if self.server.redirect:
            self.send_response(302)
            self.send_header("Location", self.server.redirect)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer synthetic-test-token":
            self.send_response(401)
            self.end_headers()
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = request["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "synthetic"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": name} for name in self.server.tools]}
        else:
            result = {"structuredContent": self.server.health}
        value = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        if self.server.malformed:
            value["id"] = "wrong-id-synthetic-secret"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.paths = []
        self.server.redirect = ""
        self.server.malformed = False
        self.server.tools = ["check_health", "get_accounts", "get_positions"]
        self.server.health = {
            "trading_mode": "READ_ONLY",
            "status": "connected",
            "quote": {"status": "ok", "logged_in": True},
            "trade": {"status": "ok"},
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.environment = patch.dict(
            os.environ, {"MCP_AUTH_TOKEN": "synthetic-test-token"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.url = patch.object(
            probe, "URL", f"http://127.0.0.1:{self.server.server_port}/mcp"
        )
        self.url.start()
        self.addCleanup(self.url.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_authentication_tools_and_gateway_are_independently_verified(self):
        probe.check()
        probe.check(gateway=True)
        self.server.health["quote"]["logged_in"] = False
        probe.check()
        with self.assertRaises(ValueError):
            probe.check(gateway=True)

    def test_refuses_real_mode_and_missing_tools(self):
        self.server.health["trading_mode"] = "REAL"
        with self.assertRaises(ValueError):
            probe.check()

    def test_explicit_mode_is_checked_in_every_deployment_profile(self):
        for mode in ["READ_ONLY", "SIMULATE", "REAL"]:
            self.server.health["trading_mode"] = mode
            probe.check(expected_mode=mode)
            probe.check(gateway=True, expected_mode=mode)
        with self.assertRaises(ValueError):
            probe.check(expected_mode="SIMULATE")
        self.server.health["trading_mode"] = "READ_ONLY"
        self.server.tools = ["check_health"]
        with self.assertRaises(ValueError):
            probe.check()

    def test_redirect_cannot_forward_credentials(self):
        self.server.redirect = (
            f"http://127.0.0.1:{self.server.server_port}/credential-sink"
        )
        with self.assertRaises(HTTPError):
            probe.request("tools/list", {}, "synthetic-id")
        self.assertEqual(self.server.paths, ["/mcp"])

    def test_failure_output_does_not_include_response_or_token(self):
        self.server.malformed = True
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            patch.object(probe.time, "monotonic", side_effect=[0, 100]),
        ):
            self.assertEqual(probe.main(), 1)
        self.assertNotIn("wrong-id-synthetic-secret", output.getvalue())
        self.assertNotIn("synthetic-test-token", output.getvalue())
