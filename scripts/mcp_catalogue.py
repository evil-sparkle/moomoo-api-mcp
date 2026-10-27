#!/usr/bin/env python3
"""Capture an authenticated MCP catalogue without calling any tool or broker API.

Run as ``python -m scripts.mcp_catalogue`` from the repository root. The existing
preflight transport confines credentials to an explicitly selected loopback MCP
endpoint, refuses redirects and bounds each response. Credentials are supplied
by the operator through that transport's protected-file mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.private_chatgpt_preflight import (
    DEFAULT_URL,
    MAX_RESPONSE_BYTES,
    McpClient,
    PreflightError,
    read_authorization,
    validate_timeout,
    validate_url,
)

MAX_PAGES = 16
MAX_TOOLS = 512
MAX_CURSOR_CHARS = 4096


def canonical_tools(tools: list[dict[str, Any]]) -> bytes:
    """Use the server's name-sorted, compact JSON schema hash convention."""
    try:
        return json.dumps(
            sorted(tools, key=lambda tool: tool["name"]),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (ValueError, TypeError, RecursionError):
        raise PreflightError("MCP catalogue contains invalid JSON values.") from None


def capture(client: McpClient) -> dict[str, Any]:
    """Initialize and retrieve a bounded complete tools/list; never tools/call."""
    started = datetime.now(timezone.utc).isoformat()
    client.initialize()
    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    cursors: set[str] = set()
    params: dict[str, Any] = {}
    for page_number in range(1, MAX_PAGES + 1):
        result = client.request("tools/list", params)
        page = result.get("tools")
        if not isinstance(page, list):
            raise PreflightError("MCP tools/list returned no tool list.")
        for tool in page:
            if not isinstance(tool, dict):
                raise PreflightError("MCP tools/list returned an invalid definition.")
            name = tool.get("name")
            if (
                not isinstance(name, str)
                or not name
                or len(name) > 128
                or name.strip() != name
                or not name.isprintable()
                or not isinstance(tool.get("inputSchema"), dict)
            ):
                raise PreflightError("MCP tools/list returned an invalid definition.")
            if name in names:
                raise PreflightError("MCP tools/list returned duplicate tool names.")
            names.add(name)
            tools.append(tool)
            if len(tools) > MAX_TOOLS:
                raise PreflightError("MCP catalogue exceeded the tool limit.")
        encoded = canonical_tools(tools)
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise PreflightError("MCP catalogue exceeded the total size limit.")
        cursor = result.get("nextCursor")
        if cursor is None:
            return {
                "catalogue_schema_version": 1,
                "started_at": started,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pages": page_number,
                "tool_count": len(tools),
                "tools_schema_sha256": hashlib.sha256(encoded).hexdigest(),
                "tools": sorted(tools, key=lambda tool: tool["name"]),
                "tools_called": False,
                "authorization_profile_verified": False,
            }
        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > MAX_CURSOR_CHARS
            or cursor in cursors
        ):
            raise PreflightError("MCP tools/list returned invalid pagination.")
        cursors.add(cursor)
        params = {"cursor": cursor}
    raise PreflightError("MCP catalogue exceeded the page limit.")


def write_capture(path: Path, record: dict[str, Any]) -> None:
    """Create a private new JSON artifact without overwriting existing files."""
    if path.suffix != ".json":
        raise PreflightError("Catalogue output must be a new .json file.")
    data = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise PreflightError("Catalogue output could not be created.") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(data)
    except OSError:
        raise PreflightError("Catalogue output could not be written.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--authorization-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        validate_url(args.url)
        validate_timeout(args.timeout)
        if args.output.suffix != ".json" or args.output.exists():
            raise PreflightError("Catalogue output must be a new .json file.")
        client = McpClient(
            url=args.url,
            authorization=read_authorization(args.authorization_file),
            timeout=args.timeout,
        )
        record = capture(client)
        write_capture(args.output, record)
    except PreflightError as exc:
        print(f"mcp_catalogue: FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        f"mcp_catalogue: PASS: {record['tool_count']} tools, "
        f"sha256 {record['tools_schema_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
