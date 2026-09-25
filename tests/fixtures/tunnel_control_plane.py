"""Disposable synthetic control plane; not OpenAI acceptance or a credential proxy."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

lock = threading.Lock()
stats = {"authenticated": 0, "rejected": 0, "responses": 0, "forwarded_ok": 0}
pending = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "synthetic-control-plane", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "check_health", "arguments": {}},
    },
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        _ = format, args

    def answer(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authenticated(self):
        accepted = (
            self.headers.get("Authorization")
            == "Bearer " + Path("/expected/control-plane-api-key").read_text().strip()
        )
        with lock:
            stats["authenticated" if accepted else "rejected"] += 1
        if not accepted:
            self.answer(401, {})
        return accepted

    def do_GET(self):
        if self.path == "/stats":
            self.answer(200, stats)
            return
        if not self.authenticated():
            return
        if not self.path.split("?", 1)[0].endswith("/poll"):
            self.answer(
                200,
                {
                    "id": "tunnel_0123456789abcdef0123456789abcdef",
                    "name": "synthetic fixture",
                    "description": "local only",
                },
            )
            return
        command = None
        with lock:
            if pending:
                command = pending.pop(0)
        time.sleep(0.1)
        self.answer(
            200,
            {
                "commands": []
                if command is None
                else [
                    {
                        "request_id": "fixture-" + str(command["id"]),
                        "shard_token": "synthetic-shard",
                        "command_type": "jsonrpc",
                        "channel": "main",
                        "created_at": "2026-09-25T00:00:00Z",
                        "headers": {"Accept": ["application/json"]},
                        "jsonrpc": command,
                    }
                ]
            },
        )

    def do_POST(self):
        if self.path == "/enqueue":
            with lock:
                pending.append(
                    {
                        "jsonrpc": "2.0",
                        "id": stats["responses"] + 10,
                        "method": "tools/list",
                        "params": {},
                    }
                )
            self.answer(200, {})
            return
        if not self.authenticated():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path.endswith("/response"):
            with lock:
                stats["responses"] += 1
                response = body.get("resp_json", {})
                if isinstance(response, dict) and "result" in response:
                    stats["forwarded_ok"] += 1
        self.answer(200, {})


ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
