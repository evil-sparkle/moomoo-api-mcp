"""PID 1 for the optional tunnel. Never relay client logs or MCP response bodies."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from private_chatgpt_preflight import (  # pyright: ignore[reportMissingImports]
    COMPOSE_URL,
    McpClient,
    PreflightError,
    RefuseRedirects,
)

RUNTIME = Path("/run/moomoo-chatgpt-tunnel")
BINARY = "/usr/local/bin/tunnel-client"
CONFIG = "/etc/tunnel-client.yaml"
CONFIG_DIGEST = Path("/opt/tunnel/config.sha256")


def report(message: str) -> None:
    print("chatgpt-tunnel: " + message, flush=True)


def verify_config() -> None:
    expected = CONFIG_DIGEST.read_text().strip()
    actual = hashlib.sha256(Path(CONFIG).read_bytes()).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise ValueError("unapproved tunnel configuration")


def authorization_header() -> str:
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if not token.strip() or any(
        character < " " or character > "~" for character in token
    ):
        raise PreflightError(
            "MCP_AUTH_TOKEN must contain a valid ordinary bearer token."
        )
    # Match the MCP settings parser's normalization of the shared token.
    return "Bearer " + token.strip()


def child_environment() -> dict[str, str]:
    tunnel_id = os.environ.get("CHATGPT_TUNNEL_ID", "").strip()
    if not re.fullmatch(r"tunnel_[a-zA-Z0-9_-]{1,128}", tunnel_id):
        raise ValueError("invalid tunnel identifier")
    key = os.environ.get("CHATGPT_TUNNEL_API_KEY", "")
    if not key.strip() or any(character < " " or character > "~" for character in key):
        raise ValueError("invalid tunnel runtime key")
    return {
        "HOME": str(RUNTIME),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "CONTROL_PLANE_TUNNEL_ID": tunnel_id,
        "CONTROL_PLANE_API_KEY": key.strip(),
        "MCP_AUTHORIZATION": authorization_header(),
    }


def gate(stop: threading.Event, *, deadline_seconds: float = 90) -> bool:
    deadline = time.monotonic() + deadline_seconds
    try:
        authorization = authorization_header()
    except PreflightError as exc:
        report(str(exc))
        return False
    while not stop.is_set():
        try:
            client = McpClient(
                COMPOSE_URL, authorization, timeout=10, allow_compose_mcp=True
            )
            for operation in (
                client.initialize,
                client.list_tools,
                lambda client=client: client.call_tool("check_health", {}),
            ):
                if stop.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report("MCP startup deadline exhausted")
                    return False
                client.timeout = min(10, remaining)
                result = operation()
            if (
                not isinstance(result, dict)
                or result.get("trading_mode") != "READ_ONLY"
            ):
                raise PreflightError("check_health did not prove READ_ONLY operation.")
            if stop.is_set():
                return False
            report("MCP available; READ_ONLY startup verified")
            return True
        except PreflightError as exc:
            if not exc.transient:
                report(str(exc))
                return False
            if time.monotonic() >= deadline:
                report("MCP startup deadline exhausted")
                return False
            report("MCP unavailable; bounded startup retry")
            stop.wait(min(3, max(0, deadline - time.monotonic())))
    return False


def probe(path: str) -> bool:
    try:
        with build_opener(ProxyHandler({}), RefuseRedirects()).open(
            "http://127.0.0.1:8080/" + path, timeout=2
        ) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def stop_child(child: subprocess.Popen, signum: int = signal.SIGTERM) -> None:
    if child.poll() is None:
        child.send_signal(signum)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    # As PID 1 reap adopted descendants as well as the direct child.
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) == 2 and sys.argv[1] == "diagnostics":
        report("process liveness: " + ("up" if probe("healthz") else "down"))
        report(
            "client startup readiness: " + ("ready" if probe("readyz") else "not ready")
        )
        report("control-plane forwarding: not proven by local health endpoints")
        return 0 if gate(threading.Event(), deadline_seconds=15) else 1
    stop = threading.Event()
    received = [signal.SIGTERM]

    def stopping(signum, _frame):
        received[0] = signum
        stop.set()

    signal.signal(signal.SIGTERM, stopping)
    signal.signal(signal.SIGINT, stopping)
    try:
        verify_config()
        environment = child_environment()
        if not gate(stop):
            return 0 if stop.is_set() else 1
        # Official doctor performs unauthenticated metadata probes. The authenticated
        # READ_ONLY preflight above is the gate; run parses the immutable config.
        child = subprocess.Popen(
            [BINARY, "run", "--config", CONFIG],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        failures = 0
        try:
            while not stop.wait(1):
                if child.poll() is not None:
                    report("client exited; restarting container required")
                    return 1
                if time.monotonic() - started < 30:
                    continue
                alive = probe("healthz")
                failures = 0 if alive else failures + 1
                report(
                    "liveness="
                    + ("up" if alive else "down")
                    + " client-startup-readiness="
                    + ("ready" if probe("readyz") else "not-ready")
                )
                if failures >= 3:
                    report("client liveness exhausted; exiting for independent restart")
                    return 1
                if stop.wait(9):
                    break
            return 0
        finally:
            stop_child(child, received[0])
    except (OSError, ValueError, PreflightError, subprocess.SubprocessError):
        report("startup failed; inspect environment settings and configuration")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
