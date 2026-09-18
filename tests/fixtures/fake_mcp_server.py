"""A stand-in MCP endpoint for tests/test_compose_config_resolution.py.

It runs in the container Compose starts from the real compose files, so the
MCP_AUTH_TOKEN it sees is exactly what Compose gave the container. It checks
the bearer token the way moomoo_mcp.server's BearerAuthMiddleware does, records
every Authorization header it receives, and answers initialize with a valid
result. No gateway, broker or trading code: only the part verification talks to.
"""

import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORD = "/tmp/authorization.jsonl"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        header = self.headers.get("Authorization")
        with open(RECORD, "a") as record:
            record.write(json.dumps({"authorization": header}) + "\n")
        expected = os.environ.get("MCP_AUTH_TOKEN", "").strip()
        if expected:
            supplied = header or ""
            if not (
                supplied.startswith("Bearer ") and supplied[7:].strip() == expected
            ):
                self.reply(401, {"detail": "Unauthorized"})
                return
        self.reply(
            200,
            {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-moomoo-api-mcp", "version": "0"},
                },
            },
        )

    def reply(self, status, message):
        body = json.dumps(message).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    # PID 1 ignores SIGTERM unless it asks for it; without this every
    # recreate would wait out Docker's stop timeout.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
