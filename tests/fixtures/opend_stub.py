#!/app/.venv/bin/python
"""Stand in for the OpenD gateway in the container smoke test.

Accepts and logs connections on the interface and port the real gateway would,
taking both from the command line the supervisor built, so a supervisor that
widened the listener or dialled the wrong address fails the smoke test rather
than passing it. Logging each connection is the point: the MCP server's own SDK
dialling in is what proves the two halves reach each other, which a probe run
from the test script would not.

Writes its pid so the smoke test can kill this process specifically -- the
gateway dying while the container keeps serving is the behaviour under test,
and `docker restart` cannot express it.
"""

import os
import signal
import socketserver
import sys
import threading

PID_FILE = "/tmp/opend-stub.pid"

# ThreadingTCPServer handles each connection on its own thread, and print() is
# not atomic: CI caught it emitting "CONNECT 127.0.0.1CONNECT\n 127.0.0.1",
# which would make the smoke test's line counting undercount reconnections and
# pass for the wrong reason. One lock, one write, one whole line.
_OUTPUT_LOCK = threading.Lock()


def say(line: str) -> None:
    with _OUTPUT_LOCK:
        sys.stdout.write(f"{line}\n")
        sys.stdout.flush()


def listen_address(argv: list[str]) -> tuple[str, int]:
    host = "127.0.0.1"
    for arg in argv:
        if arg.startswith("-api_ip="):
            host = arg.split("=", 1)[1]
    return host, 11111


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        say(f"CONNECT {self.client_address[0]}")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    # Without this the stub sits out the supervisor's whole stop timeout and
    # only dies to SIGKILL, turning every shutdown into a slow one.
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))

    with open(PID_FILE, "w") as handle:
        handle.write(str(os.getpid()))

    host, port = listen_address(sys.argv[1:])
    say(f"LISTENING {host}:{port}")
    Server((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
