"""Read-only MCP checks run inside the service; credentials never leave it."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

URL = "http://127.0.0.1:8000/mcp"


class RefuseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def request(
    method: str, params: dict, identifier: str, *, authenticated: bool = True
) -> dict:
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not token.isascii() or not token.isprintable():
        raise ValueError("Authentication configuration is invalid.")
    body = json.dumps(
        {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
    ).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer " + token
    query = Request(
        URL,
        data=body,
        headers=headers,
    )
    opener = build_opener(ProxyHandler({}), RefuseRedirects())
    with opener.open(query, timeout=5) as response:
        if (
            response.status != 200
            or response.headers.get_content_type() != "application/json"
        ):
            raise ValueError("Unexpected MCP HTTP response.")
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("MCP response exceeded the size bound.")
    message = json.loads(raw)
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or message.get("id") != identifier
        or "error" in message
        or not isinstance(message.get("result"), dict)
    ):
        raise ValueError("Invalid MCP response.")
    return message["result"]


def health(expected_mode: str = "READ_ONLY") -> dict:
    result = request(
        "tools/call", {"name": "check_health", "arguments": {}}, "deploy-health"
    )
    structured = result.get("structuredContent")
    if (
        result.get("isError")
        or not isinstance(structured, dict)
        or expected_mode not in {"READ_ONLY", "SIMULATE", "REAL"}
        or structured.get("trading_mode") != expected_mode
    ):
        raise ValueError("MCP did not prove the selected trading mode.")
    return structured


def check(gateway: bool = False, expected_mode: str = "READ_ONLY") -> None:
    if gateway:
        value = health(expected_mode)
        quote = value.get("quote") or {}
        trade = value.get("trade") or {}
        if (
            value.get("status") != "connected"
            or quote.get("status") != "ok"
            or quote.get("logged_in") is not True
            or trade.get("status") != "ok"
        ):
            raise ValueError("OpenD readiness is unconfirmed.")
        return
    try:
        request("tools/list", {}, "deploy-unauthenticated", authenticated=False)
    except HTTPError as error:
        if error.code != 401:
            raise ValueError("Unauthenticated MCP must return HTTP 401.") from None
    else:
        raise ValueError("Unauthenticated MCP access was accepted.")
    result = request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ansible-deploy", "version": "1"},
        },
        "deploy-initialize",
    )
    if not isinstance(result.get("protocolVersion"), str) or not isinstance(
        result.get("serverInfo"), dict
    ):
        raise ValueError("Invalid MCP initialize result.")
    tools = request("tools/list", {}, "deploy-tools").get("tools")
    if not isinstance(tools, list):
        raise ValueError("Invalid tool list.")
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    if not {"check_health", "get_accounts", "get_positions"}.issubset(names):
        raise ValueError("Required read-only tools are missing.")
    health(expected_mode)


def main() -> int:
    gateway = len(sys.argv) > 1 and sys.argv[1] == "gateway"
    expected_mode = (
        sys.argv[2]
        if len(sys.argv) > 2 and sys.argv[1] in {"application", "gateway"}
        else "READ_ONLY"
    )
    deadline = time.monotonic() + (60 if gateway else 90)
    while True:
        try:
            check(gateway, expected_mode)
            print(
                "OpenD ready: quote login and quote/trade probes verified."
                if gateway
                else "Authenticated MCP and " + expected_mode + " mode verified."
            )
            return 0
        except HTTPError as error:
            if error.code < 500 and not gateway:
                break
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(3, remaining))
    print(
        "OpenD readiness is unconfirmed."
        if gateway
        else "Authenticated MCP deployment verification failed."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
