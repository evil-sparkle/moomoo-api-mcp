"""Client persistence evidence using temporary files and synthetic transports."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from examples import trading_client as recipe


@pytest.fixture
def parameters():
    return {
        "code": "US.FIXTURE",
        "price": "1.00",
        "qty": 1,
        "trd_side": "BUY",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "session": "N/A",
        "adjust_limit": 0,
        "aux_price": None,
        "trail_type": None,
        "trail_value": None,
        "trail_spread": None,
        "remark": "",
        "trd_env": "SIMULATE",
        "acc_id": "900000000000000001",
        "admission_epoch": "fixture-epoch-0001",
    }


@pytest.fixture
def intent(tmp_path, parameters):
    path = tmp_path / "authorized-intent.json"
    recipe.persist_authorized_intent(path, "place_order", parameters)
    return path


def test_one_uuid_is_persisted_without_overwrite(tmp_path, parameters, monkeypatch):
    generate = Mock(return_value=Mock(hex="0123456789abcdef0123456789abcdef"))
    monkeypatch.setattr(recipe.uuid, "uuid4", generate)
    path = tmp_path / "intent.json"
    record = recipe.persist_authorized_intent(path, "place_order", parameters)
    assert record["parameters"]["operation_id"] == generate.return_value.hex
    assert record["parameters"] == parameters | {
        "operation_id": generate.return_value.hex
    }
    parameters["qty"] = 99
    record["parameters"]["price"] = "99.00"
    assert recipe.load_intent(path)["parameters"]["qty"] == 1
    assert recipe.load_intent(path)["parameters"]["price"] == "1.00"
    with pytest.raises(FileExistsError):
        recipe.persist_authorized_intent(path, "place_order", parameters)
    generate.assert_called_once_with()
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(list(tmp_path.iterdir())) == 1


async def test_exact_request_and_attempt_are_durable_before_transport(
    intent, monkeypatch
):
    synchronized = []
    fsync = os.fsync

    def observe_sync(descriptor):
        fsync(descriptor)
        synchronized.append(descriptor)

    monkeypatch.setattr(recipe.os, "fsync", observe_sync)

    async def transport(method, arguments):
        marker = intent.with_name(intent.name + ".attempt.json")
        assert json.loads(marker.read_text()) == {
            "method": method,
            "parameters": arguments,
        }
        assert arguments == recipe.load_intent(intent)["parameters"]
        assert len(synchronized) == 2  # complete file and directory entry
        return {"state": "ACKNOWLEDGED", "fill_status": "UNKNOWN"}

    result = await recipe.submit_once(intent, transport)
    assert result["state"] == "ACKNOWLEDGED"


@pytest.mark.parametrize(
    "failure", [TimeoutError, ConnectionError, asyncio.CancelledError]
)
async def test_uncertain_attempt_never_reinvokes_or_changes_identity(intent, failure):
    before = recipe.load_intent(intent)
    transport = AsyncMock(side_effect=failure)
    with pytest.raises(failure):
        await recipe.submit_once(intent, transport)
    with pytest.raises(recipe.AttemptAlreadyStarted):
        await recipe.submit_once(intent, transport)
    assert recipe.load_intent(intent) == before
    transport.assert_awaited_once_with(before["method"], before["parameters"])


async def test_tool_error_result_is_returned_once_without_retry(intent):
    error = {"isError": True, "error_code": "ORDER_NOT_SENT", "retryable": False}
    transport = AsyncMock(return_value=error)
    assert await recipe.submit_once(intent, transport) == error
    with pytest.raises(recipe.AttemptAlreadyStarted):
        await recipe.submit_once(intent, transport)
    transport.assert_awaited_once()


async def test_persistence_failure_prevents_transport_and_retains_claim(
    intent, monkeypatch
):
    sync_calls = 0
    fsync = os.fsync

    def fail_directory_sync(descriptor):
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise OSError("fixture directory sync failure")
        fsync(descriptor)

    transport = AsyncMock()
    monkeypatch.setattr(recipe.os, "fsync", fail_directory_sync)
    with pytest.raises(OSError) as failure:
        await recipe.submit_once(intent, transport)
    assert "fixture directory sync failure" in str(failure.value)
    transport.assert_not_awaited()
    monkeypatch.setattr(recipe.os, "fsync", fsync)
    with pytest.raises(recipe.AttemptAlreadyStarted):
        await recipe.submit_once(intent, transport)
    transport.assert_not_awaited()


async def test_concurrent_invocations_claim_one_attempt(intent):
    transport = AsyncMock(return_value={"state": "IN_FLIGHT"})
    outcomes = await asyncio.gather(
        recipe.submit_once(intent, transport),
        recipe.submit_once(intent, transport),
        return_exceptions=True,
    )
    assert sum(isinstance(item, recipe.AttemptAlreadyStarted) for item in outcomes) == 1
    transport.assert_awaited_once()


@pytest.fixture
def approved(intent):
    original = recipe.load_intent(intent)
    return original | {
        "request_id": "fixture-approval-0001",
        "status": "APPROVED",
        "consumed_at": None,
        "parameters": original["parameters"],
        "review": {"fixture": "reviewed exact request"},
    }


async def test_approved_expanded_request_is_persisted_before_dispatch(intent, approved):
    async def transport(method, arguments):
        marker = json.loads(Path(str(intent) + ".attempt.json").read_text())
        assert marker["prepared_trade"] == approved
        assert marker["method"] == method == "place_order"
        assert (
            marker["parameters"]
            == arguments
            == approved["parameters"] | {"approval_id": approved["request_id"]}
        )
        return {"state": "ACKNOWLEDGED"}

    await recipe.submit_once(intent, transport, prepared_trade=approved)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("operation_id", "fixture-other-operation"),
        ("admission_epoch", "fixture-other-epoch"),
        ("acc_id", "900000000000000002"),
        ("trd_env", "REAL"),
        ("qty", 2),
        ("session", "RTH"),
    ],
)
async def test_changed_approved_request_cannot_transfer_authorization(
    intent, approved, key, value
):
    approved["parameters"][key] = value
    transport = AsyncMock()
    with pytest.raises(ValueError) as failure:
        await recipe.submit_once(intent, transport, prepared_trade=approved)
    assert "original exact authorized intent" in str(failure.value)
    transport.assert_not_awaited()
    assert not Path(str(intent) + ".attempt.json").exists()


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "EXPIRED"])
async def test_unapproved_request_does_not_dispatch(intent, approved, status):
    approved["status"] = status
    transport = AsyncMock()
    with pytest.raises(ValueError) as failure:
        await recipe.submit_once(intent, transport, prepared_trade=approved)
    assert "original exact authorized intent" in str(failure.value)
    transport.assert_not_awaited()


@pytest.mark.parametrize(
    ("key", "value"),
    [("operation_id", "x" * 66), ("acc_id", "0"), ("trd_env", "PAPER")],
)
def test_no_implicit_correction_of_invalid_input(tmp_path, parameters, key, value):
    parameters[key] = value
    path = tmp_path / "intent.json"
    with pytest.raises(ValueError):
        recipe.persist_authorized_intent(path, "place_order", parameters)
    assert not path.exists()


@pytest.mark.parametrize(
    ("field", "added"),
    [("order_type", "MARKET"), ("time_in_force", "GTC"), ("adjust_limit", 0)],
)
async def test_approval_cannot_add_unpersisted_default_arguments(
    tmp_path, parameters, field, added
):
    parameters.update(trd_env="REAL", price=1.0)
    parameters.pop(field)
    path = tmp_path / "incomplete-authorized-intent.json"
    original = recipe.persist_authorized_intent(path, "place_order", parameters)
    approved = original | {
        "request_id": "fixture-approved-incomplete-request",
        "status": "APPROVED",
        "consumed_at": None,
        "parameters": original["parameters"] | {field: added},
    }
    transport = AsyncMock()
    with pytest.raises(ValueError):
        await recipe.submit_once(path, transport, prepared_trade=approved)
    transport.assert_not_awaited()
    assert not Path(str(path) + ".attempt.json").exists()


async def test_approval_comparison_preserves_json_value_types(intent, approved):
    approved["parameters"] = approved["parameters"] | {"qty": True}
    transport = AsyncMock()
    with pytest.raises(ValueError):
        await recipe.submit_once(intent, transport, prepared_trade=approved)
    transport.assert_not_awaited()
