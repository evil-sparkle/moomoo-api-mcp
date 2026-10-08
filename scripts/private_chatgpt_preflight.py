#!/usr/bin/env python3
"""Shared authenticated MCP preflight for local and explicitly selected Docker URLs.

The managed container passes its environment-derived header directly to McpClient.
The standalone acceptance CLI accepts a protected header file, never a credential
on the command line. Diagnostics exclude response bodies and account data.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_URL = "http://127.0.0.1:8000/mcp"
COMPOSE_URL = "http://moomoo-mcp:8000/mcp"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PROTOCOL_VERSION = "2025-06-18"
REQUIRED_TOOLS = frozenset({"check_health", "get_accounts", "get_positions"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class PreflightError(Exception):
    """A safe-to-display verification failure."""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


class RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _ = req, fp, code, msg, headers, newurl
        return None


@dataclass
class McpClient:
    """Small stateless Streamable HTTP client for acceptance probes."""

    url: str
    authorization: str
    timeout: float = 10.0
    next_id: int = 1
    allow_compose_mcp: bool = False

    def __post_init__(self) -> None:
        validate_url(self.url, allow_compose_mcp=self.allow_compose_mcp)
        validate_timeout(self.timeout)

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode()
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": self.authorization,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with build_opener(ProxyHandler({}), RefuseRedirects()).open(
                request, timeout=self.timeout
            ) as response:
                status = response.status
                content_type = response.headers.get_content_type()
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise PreflightError("MCP response exceeded size limit.")
        except HTTPError as exc:
            raise PreflightError(
                f"MCP endpoint returned HTTP {exc.code}.",
                transient=500 <= exc.code < 600,
            ) from None
        except (URLError, TimeoutError, OSError):
            raise PreflightError(
                "MCP endpoint is unreachable.", transient=True
            ) from None
        if status != 200:
            raise PreflightError(f"MCP endpoint returned HTTP {status}.")
        if content_type != "application/json":
            raise PreflightError("MCP endpoint did not return application/json.")
        try:
            message: Any = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            raise PreflightError("MCP endpoint returned malformed JSON.") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise PreflightError("MCP endpoint returned an invalid JSON-RPC envelope.")
        if message.get("id") != request_id or isinstance(message.get("id"), bool):
            raise PreflightError("MCP endpoint returned a mismatched request id.")
        if "error" in message:
            raise PreflightError(f"MCP {method} returned a JSON-RPC error.")
        result = message.get("result")
        if not isinstance(result, dict):
            raise PreflightError(f"MCP {method} returned no result object.")
        return result

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "private-chatgpt-preflight",
                    "version": "1",
                },
            },
        )
        if not isinstance(result.get("protocolVersion"), str):
            raise PreflightError("MCP initialize returned no protocol version.")
        if not isinstance(result.get("serverInfo"), dict):
            raise PreflightError("MCP initialize returned no server information.")

    def list_tools(self) -> set[str]:
        result = self.request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise PreflightError("MCP tools/list returned no tool list.")
        names: set[str] = set()
        for tool in tools:
            if isinstance(tool, dict):
                name = tool.get("name")
                if isinstance(name, str):
                    names.add(name)
        missing = REQUIRED_TOOLS - names
        if missing:
            raise PreflightError("MCP tools/list omitted required read-only tools.")
        return names

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            raise PreflightError(f"MCP tool {name} returned an error result.")
        if "structuredContent" not in result:
            raise PreflightError(f"MCP tool {name} returned no structured content.")
        structured = result["structuredContent"]
        # FastMCP wraps list results in {"result": ...}; dict results are direct.
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured


def read_authorization(path: Path) -> str:
    """Read one complete Bearer header from a protected file."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise PreflightError("Authorization credential could not be read.") from None
    if not value.startswith("Bearer ") or not value[7:].strip():
        raise PreflightError("Authorization credential is not a Bearer header.")
    if any(character < " " or character > "~" for character in value):
        raise PreflightError("Authorization credential is not a safe HTTP header.")
    return value


def validate_url(url: str, *, allow_compose_mcp: bool = False) -> None:
    if allow_compose_mcp and url == COMPOSE_URL:
        return
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise PreflightError("MCP URL must be loopback HTTP at /mcp.") from None
    if (
        parts.scheme != "http"
        or (port is not None and not 1 <= port <= 65535)
        or parts.hostname not in LOOPBACK_HOSTS
        or parts.path != "/mcp"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise PreflightError("MCP URL must be loopback HTTP at /mcp.")


def validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise PreflightError("Timeout must be finite and greater than zero.")


def validate_trading_mode(health: Any) -> str:
    """Require server-enforced read-only or paper-only access before forwarding."""
    mode = health.get("trading_mode") if isinstance(health, dict) else None
    if not isinstance(mode, str) or mode not in {"READ_ONLY", "SIMULATE"}:
        raise PreflightError(
            "check_health did not prove READ_ONLY or SIMULATE operation."
        )
    return mode


def run(
    *,
    url: str,
    authorization_file: Path,
    mode: str,
    trd_env: str | None = None,
    account_id: str | None = None,
    timeout: float = 10.0,
    allow_compose_mcp: bool = False,
) -> list[str]:
    """Run startup-safe or full acceptance and return safe milestone names."""
    validate_url(url, allow_compose_mcp=allow_compose_mcp)
    validate_timeout(timeout)
    authorization = read_authorization(authorization_file)
    client = McpClient(
        url=url,
        authorization=authorization,
        timeout=timeout,
        allow_compose_mcp=allow_compose_mcp,
    )
    client.initialize()
    milestones = ["initialize"]
    client.list_tools()
    milestones.append("tools/list")
    health = client.call_tool("check_health", {})
    trading_mode = validate_trading_mode(health)
    milestones.append("check_health:" + trading_mode)
    if mode == "startup-safe":
        return milestones
    if trd_env not in {"REAL", "SIMULATE"} or not account_id:
        raise PreflightError("Full mode requires a trading environment and account id.")
    accounts = client.call_tool("get_accounts", {"trd_env": trd_env})
    if not isinstance(accounts, list) or not any(
        isinstance(account, dict) and str(account.get("acc_id")) == account_id
        for account in accounts
    ):
        raise PreflightError("The selected account was not returned by get_accounts.")
    milestones.append("get_accounts:selected")
    positions = client.call_tool(
        "get_positions", {"trd_env": trd_env, "acc_id": account_id}
    )
    if not isinstance(positions, list):
        raise PreflightError("get_positions returned an unexpected result type.")
    milestones.append("get_positions")
    return milestones


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--allow-compose-mcp", action="store_true")
    parser.add_argument("--authorization-file", required=True, type=Path)
    parser.add_argument("--mode", choices=("startup-safe", "full"), required=True)
    parser.add_argument("--trd-env", choices=("REAL", "SIMULATE"))
    parser.add_argument("--account-id")
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        milestones = run(
            url=args.url,
            allow_compose_mcp=args.allow_compose_mcp,
            authorization_file=args.authorization_file,
            mode=args.mode,
            trd_env=args.trd_env,
            account_id=args.account_id,
            timeout=args.timeout,
        )
    except PreflightError as exc:
        print(f"private_chatgpt_preflight: FAIL: {exc}", file=sys.stderr)
        return 1
    print("private_chatgpt_preflight: PASS: " + ", ".join(milestones))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
