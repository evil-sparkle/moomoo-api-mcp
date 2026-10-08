"""Disposable synthetic control plane; not OpenAI acceptance or a credential proxy."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
runtime_key = [os.environ["FIXTURE_RUNTIME_KEY"]]
stats = {
    "authenticated": 0,
    "rejected": 0,
    "responses": 0,
    "forwarded_ok": 0,
    "proxy_hits": 0,
}
forwarded_modes: list[str] = []
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
        with lock:
            accepted = self.headers.get("Authorization") == "Bearer " + runtime_key[0]
            stats["authenticated" if accepted else "rejected"] += 1
        if not accepted:
            self.answer(401, {})
        return accepted

    def do_GET(self):
        if self.path == "/stats":
            self.answer(200, {**stats, "forwarded_modes": forwarded_modes})
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
                        "headers": command.pop(
                            "_headers", {"Accept": ["application/json"]}
                        ),
                        "jsonrpc": command,
                    }
                ]
            },
        )

    def do_POST(self):
        if self.path == "/rotate-runtime":
            with lock:
                runtime_key[0] = "synthetic-runtime-key-rotated"
            self.answer(200, {})
            return
        if self.path in {
            "/enqueue",
            "/enqueue-health",
            "/enqueue-wrong",
            "/enqueue-conflict",
        }:
            with lock:
                pending.append(
                    {
                        "jsonrpc": "2.0",
                        "id": stats["responses"] + 10,
                        "method": "tools/list",
                        "params": {},
                    }
                )
                if self.path == "/enqueue-wrong":
                    pending[-1]["_headers"] = {
                        "Authorization": ["Bearer synthetic-wrong"]
                    }
                elif self.path == "/enqueue-conflict":
                    pending[-1]["_headers"] = {
                        "Authorization": [
                            "Bearer synthetic-mcp-token",
                            "Bearer synthetic-conflict",
                        ]
                    }
                elif self.path == "/enqueue-health":
                    pending[-1]["method"] = "tools/call"
                    pending[-1]["params"] = {"name": "check_health", "arguments": {}}
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
                    content = response["result"].get("structuredContent", {})
                    mode = (
                        content.get("trading_mode")
                        if isinstance(content, dict)
                        else None
                    )
                    if isinstance(mode, str) and mode in {"READ_ONLY", "SIMULATE"}:
                        forwarded_modes.append(mode)
        self.answer(200, {})


class PoisonProxy(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        _ = format, args

    def do_GET(self):
        with lock:
            stats["proxy_hits"] += 1
        self.send_response(502)
        self.end_headers()

    do_POST = do_GET
    do_CONNECT = do_GET


proxy = ThreadingHTTPServer(("0.0.0.0", 8082), PoisonProxy)
threading.Thread(target=proxy.serve_forever, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
