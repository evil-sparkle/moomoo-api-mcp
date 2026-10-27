"""Pinned synthetic source fixtures match real MCP codecs and consumer boundaries."""

import hashlib
import json
from unittest.mock import patch

import jsonschema
import pytest
from mcp import types

from tests.broker_fixture_handoff import (
    ADJUSTED,
    DESTINATION,
    MISSING,
    ROOT,
    SOURCE,
    encode,
    generate,
    source_manifest,
)


def read(name: str) -> dict:
    return json.loads((DESTINATION / (name + ".json")).read_text())


def payload(name: str) -> dict:
    return read(name)["response"]["result"]["structuredContent"]


@pytest.mark.asyncio
async def test_replay_matches_real_registered_handlers_without_runtime_io():
    regenerated = await generate()
    assert {path.name for path in DESTINATION.glob("*.json")} == set(regenerated)
    for name, fixture in regenerated.items():
        content = (DESTINATION / name).read_bytes()
        assert content == encode(fixture), name
        assert len(content) < 128 * 1024
    assert sum(len(encode(value)) for value in regenerated.values()) < 256 * 1024


def test_exact_source_and_hashes_are_separate_from_payload_identity():
    manifest = read("manifest")
    assert manifest["fixture_kind"] == "SYNTHETIC"
    assert manifest["source_commit"] == SOURCE
    for record in manifest["runtime_files"]:
        content = (ROOT / record["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == record["sha256"]
    for name, digest in manifest["fixture_files_sha256"].items():
        assert hashlib.sha256((DESTINATION / name).read_bytes()).hexdigest() == digest
    assert manifest["catalogue_identity"]["source_revision"] == "unknown"
    assert payload("snapshot.normal")["source_revision"] is None
    assert payload("chain.normal_adjusted")["result"]["source_revision"] is None


def test_catalogue_contains_only_the_actual_market_profile_and_valid_schemas():
    fixture = read("catalogue.market_read")
    assert fixture["permissions"] == ["market_read"]
    tools = {tool["name"]: tool for tool in fixture["response"]["result"]["tools"]}
    assert (
        not {
            "get_accounts",
            "get_assets",
            "get_positions",
            "get_order_fees",
            "place_order",
            "place_combo_order",
            "unlock_trade",
            "lock_trade",
        }
        & tools.keys()
    )
    for tool in tools.values():
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        if "outputSchema" in tool:
            jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
    for path in sorted(DESTINATION.glob("*.json")):
        fixture = json.loads(path.read_text())
        if "request" not in fixture:
            continue
        request = fixture["request"]
        response = fixture["response"]
        assert (
            types.JSONRPCResponse.model_validate(response).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            == response
        )
        tool = tools[request["params"]["name"]]
        jsonschema.validate(request["params"]["arguments"], tool["inputSchema"])
        if not response["result"]["isError"]:
            jsonschema.validate(
                response["result"]["structuredContent"], tool["outputSchema"]
            )
        assert fixture["fixture_kind"] == "SYNTHETIC"


def test_legacy_expiry_and_chain_have_distinct_wire_contracts():
    expiry = payload("expiry.normal")
    assert list(expiry) == ["result"] and isinstance(expiry["result"], list)
    assert "schema_version" not in expiry and "data" not in expiry
    assert payload("expiry.empty")["result"] == []
    result = payload("chain.normal_adjusted")["result"]
    assert result["source_method"] == "get_option_chain"
    assert result["scope"]["data_filter"] == {"delta_min": 0.1, "delta_max": 0.9}
    assert result["data"][1]["code"] == ADJUSTED
    assert result["data"][1]["option_standard_type"] == "NON_STANDARD"
    assert result["data"][1]["option_settlement_mode"] == "N/A"
    assert payload("chain.empty")["result"]["data"] == []
    failure = read("chain.permission_error")["response"]["result"]
    assert failure["isError"] is True
    assert failure["structuredContent"]["error_code"] == "QUOTE_PERMISSION_DENIED"


def test_raw_provider_time_zero_null_and_partial_are_not_promoted_to_quality():
    result = payload("snapshot.partial_null_zero")
    row = result["data"][0]
    assert row["bid_price"] == 0 and row["bid_vol"] == 0
    assert row["ask_price"] is None and row["ask_vol"] is None
    assert row["update_time"] == "2026-10-26 10:29:59.123"
    assert result["requested_at"].endswith("Z") and result["received_at"].endswith("Z")
    assert result["delay_status"] == result["entitlement_status"] == "UNKNOWN"
    assert result["coverage"]["missing"] == [MISSING]
    assert result["coverage"]["complete_response"] is False
    assert result["errors"][MISSING] == "NOT_RETURNED_CAUSE_UNKNOWN"
    assert "update_time" not in payload("snapshot.missing_timestamp")["data"][0]
    for field in ("bid_age", "ask_age", "timezone", "is_realtime"):
        assert field not in result and field not in row


def test_contract_economics_conflicts_and_missing_lifecycle_facts():
    facts = payload("facts.adjusted_non100")["data"][0]["facts"]
    assert facts["monetary_multiplier"]["value"] == 25
    assert facts["deliverable_size"]["value"] == 150
    assert facts["lot_size"]["value"] == 1
    assert facts["exercise_style"]["value"] == "AMERICAN"
    for field in (
        "currency",
        "settlement_mode",
        "settlement_cutoff",
        "standard_status",
        "deliverable_description",
    ):
        assert facts[field]["state"] == "UNKNOWN" and facts[field]["value"] is None
    # Chain NON_STANDARD is not fabricated into the separate basicinfo/snapshot route.
    conflict = payload("facts.conflict")["data"][0]["facts"]["lot_size"]
    assert conflict["state"] == "CONFLICT" and conflict["value"] is None
    assert {item["value"] for item in conflict["evidence"]} == {1, 100}
    zero = payload("facts.zero_unknown")["data"][0]["facts"]
    assert zero["monetary_multiplier"]["state"] == "UNKNOWN"
    assert zero["monetary_multiplier"]["value"] is None
    missing = payload("facts.missing_sources")
    assert missing["coverage"]["missing"] == [MISSING]
    assert missing["data"][0]["snapshot_present"] is False
    assert missing["data"][0]["reference_present"] is False
    for name in (
        "facts.standard_incomplete",
        "facts.adjusted_non100",
        "facts.conflict",
        "facts.zero_unknown",
        "facts.missing_sources",
    ):
        assert payload(name)["data"][0]["compatibility_verified"] is False


@pytest.mark.asyncio
async def test_shallow_checkout_replays_without_any_historical_git_access():
    with patch(
        "tests.broker_fixture_handoff.git",
        side_effect=FileNotFoundError("No Git history in this checkout"),
    ) as unavailable:
        result = await generate()
        assert (
            encode(result["manifest.json"])
            == (DESTINATION / "manifest.json").read_bytes()
        )
        unavailable.assert_not_called()


def test_manual_historical_verification_fails_explicitly_without_source_object():
    before = (DESTINATION / "manifest.json").read_bytes()
    with (
        patch(
            "tests.broker_fixture_handoff.git",
            side_effect=FileNotFoundError("Pinned historical object unavailable"),
        ),
        pytest.raises(FileNotFoundError),
    ):
        source_manifest(verify_git=True)
    assert (DESTINATION / "manifest.json").read_bytes() == before
