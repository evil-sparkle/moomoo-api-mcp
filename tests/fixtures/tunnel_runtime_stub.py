#!/usr/bin/env python3
"""Loopback health/readiness fixture for tunnel service lifecycle tests."""

from __future__ import annotations

import argparse
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url-file", required=True, type=Path)
    parser.add_argument(
        "--control-plane", choices=("available", "unavailable"), required=True
    )
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._answer(200, {"status": "healthy"})
            elif self.path == "/readyz" and args.control_plane == "available":
                self._answer(200, {"status": "ready"})
            elif self.path == "/readyz":
                self._answer(503, {"status": "not_ready"})
            else:
                self._answer(404, {"status": "not_found"})

        def _answer(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            _ = (format, args)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    args.url_file.write_text(
        f"http://127.0.0.1:{server.server_address[1]}", encoding="utf-8"
    )

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever(poll_interval=0.05)
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
