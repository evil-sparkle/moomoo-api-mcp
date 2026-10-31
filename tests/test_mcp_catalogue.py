"""Catalogue capture is profile-neutral, bounded and never dispatches tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import mcp_catalogue as catalogue
from scripts.private_chatgpt_preflight import McpClient, PreflightError
from tests.mcp_helpers import AUTHORIZATION, McpFixture


def tool(name: str) -> dict:
    return {
        "name": name,
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    }


def client(pages: list[dict]) -> Mock:
    mocked = Mock(spec=McpClient)
    mocked.request.side_effect = pages
    return mocked


def test_market_only_pagination_preserves_definitions_and_server_hash() -> None:
    definitions = [tool("snapshot"), tool("expirations")]
    definitions[0]["outputSchema"] = {"type": "object"}
    mocked = client(
        [
            {"tools": [definitions[0]], "nextCursor": "next-page"},
            {"tools": [definitions[1]]},
        ]
    )
    result = catalogue.capture(mocked)
    expected = sorted(definitions, key=lambda item: item["name"])
    digest = hashlib.sha256(
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert result["tools"] == expected
    assert result["tools_schema_sha256"] == digest
    assert result["tool_count"] == 2
    assert result["pages"] == 2
    assert result["tools_called"] is False
    assert result["authorization_profile_verified"] is False
    mocked.initialize.assert_called_once_with()
    assert [call.args for call in mocked.request.call_args_list] == [
        ("tools/list", {}),
        ("tools/list", {"cursor": "next-page"}),
    ]
    mocked.call_tool.assert_not_called()


@pytest.mark.parametrize(
    "pages",
    [
        [{}],
        [{"tools": [None]}],
        [{"tools": [{"name": "missing_schema"}]}],
        [{"tools": [tool("")]}],
        [{"tools": [tool(" leading")]}],
        [{"tools": [tool("same"), tool("same")]}],
        [{"tools": [tool("same")], "nextCursor": "x"}, {"tools": [tool("same")]}],
        [{"tools": [], "nextCursor": ""}],
        [{"tools": [], "nextCursor": True}],
        [{"tools": [], "nextCursor": "x"}, {"tools": [], "nextCursor": "x"}],
        [{"tools": [], "nextCursor": "x" * (catalogue.MAX_CURSOR_CHARS + 1)}],
        [{"tools": [{**tool("bad"), "extra": float("nan")}]}],
    ],
)
def test_invalid_incomplete_catalogues_fail_closed(pages: list[dict]) -> None:
    mocked = client(pages)
    with pytest.raises(PreflightError):
        catalogue.capture(mocked)
    mocked.call_tool.assert_not_called()


def test_page_tool_and_total_byte_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalogue, "MAX_PAGES", 2)
    with pytest.raises(PreflightError) as error:
        catalogue.capture(
            client([{"tools": [], "nextCursor": "a"}, {"tools": [], "nextCursor": "b"}])
        )
    assert "page limit" in str(error.value)
    monkeypatch.setattr(catalogue, "MAX_TOOLS", 1)
    with pytest.raises(PreflightError) as error:
        catalogue.capture(client([{"tools": [tool("a"), tool("b")]}]))
    assert "tool limit" in str(error.value)
    monkeypatch.setattr(catalogue, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(PreflightError) as error:
        catalogue.capture(client([{"tools": [tool("x" * 128)]}]))
    assert "total size limit" in str(error.value)


def test_cli_makes_only_initialize_and_list_requests(tmp_path: Path) -> None:
    authorization = tmp_path / "synthetic-header"
    authorization.write_text(AUTHORIZATION)
    output = tmp_path / "catalogue.json"
    with McpFixture() as server:

        def response(request: dict) -> tuple:
            assert request["method"] in {"initialize", "tools/list"}
            result = (
                {"tools": [tool("market_only")]}
                if request["method"] == "tools/list"
                else {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "fixture"},
                }
            )
            return (
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": result,
                },
                "application/json",
            )

        server.override = response
        assert (
            catalogue.main(
                [
                    "--url",
                    server.url,
                    "--authorization-file",
                    str(authorization),
                    "--output",
                    str(output),
                ]
            )
            == 0
        )
    assert [call["request"]["method"] for call in server.calls] == [
        "initialize",
        "tools/list",
    ]
    record = json.loads(output.read_text())
    assert record["tool_count"] == 1
    assert AUTHORIZATION not in output.read_text()
    assert output.stat().st_mode & 0o777 == 0o600


def test_export_refuses_overwrite_symlink_and_non_json(tmp_path: Path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("keep")
    link = tmp_path / "link.json"
    link.symlink_to(existing)
    for path in [existing, link, tmp_path / "other.txt"]:
        with pytest.raises(PreflightError):
            catalogue.write_capture(path, {})
    assert existing.read_text() == "keep"


@pytest.mark.parametrize("drift", [False, True])
async def test_cli_lab_scanner_verifies_exact_catalogue_without_claiming_execution(
    tmp_path, monkeypatch, drift
):
    from moomoo_mcp.catalogue_capture import LAB_SCANNER_GRANTS
    from moomoo_mcp.client_authorization import client_grants_context
    from moomoo_mcp.server import mcp

    with client_grants_context(LAB_SCANNER_GRANTS):
        tools = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in await mcp.list_tools()
        ]
    digest = hashlib.sha256(catalogue.canonical_tools(tools)).hexdigest()
    mocked = client([{"tools": tools}])
    monkeypatch.setattr(catalogue, "McpClient", lambda **_kwargs: mocked)
    authorization = tmp_path / "synthetic-header"
    authorization.write_text(AUTHORIZATION)
    output = tmp_path / "scanner.json"
    status = catalogue.main(
        [
            "--authorization-file",
            str(authorization),
            "--output",
            str(output),
            "--profile",
            "lab-scanner",
            "--expected-tools-sha256",
            "0" * 64 if drift else digest,
        ]
    )
    mocked.call_tool.assert_not_called()
    if drift:
        assert status == 1
        assert not output.exists()
    else:
        assert status == 0
        record = json.loads(output.read_text())
        assert record["profile_verification"]["catalogue_verified"] is True
        assert (
            record["profile_verification"]["execution_authorization_verified"] is False
        )
        assert record["authorization_profile_verified"] is False
        assert record["tools_called"] is False
