"""Synthetic startup refusals only: no brokerage SDK or trading capability.

REAL/UNKNOWN are health response labels, never a configured brokerage mode.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

behavior = os.environ["FIXTURE_BEHAVIOR"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        _ = format, args

    def do_POST(self):
        expected = "Bearer synthetic-mcp-token-rotated"
        if behavior == "wrong-auth" or self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.end_headers()
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if request["method"] == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif request["method"] == "tools/list":
            result = {
                "tools": [
                    {"name": name}
                    for name in ("check_health", "get_accounts", "get_positions")
                ]
            }
        else:
            result = {"structuredContent": {"trading_mode": behavior}, "isError": False}
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request["id"], "result": result}
        ).encode()
        if behavior == "malformed":
            body = b"invalid-json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
