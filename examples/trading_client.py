"""Illustrative caller persistence; no transport, broker connection or retry loop.

Use an existing private directory on durable local POSIX storage. This example
owns one immutable authorized intent and one submission-attempt file. The server
continues to own execution, approvals, deduplication and recovery.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

MUTATIONS = {"place_order", "place_combo_order", "modify_order", "cancel_order"}
ToolCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


class AttemptAlreadyStarted(RuntimeError):
    """Inspect the original execution; a previous attempt may have been sent."""


def _persist_exclusive(path: Path, value: dict[str, Any]) -> None:
    """Publish complete JSON without overwrite, then sync the directory entry."""
    encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".intent-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # link() fails if the destination exists; replace() would overwrite it.
        os.link(temporary, path)
        temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def persist_authorized_intent(
    path: Path, method: str, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Call once after explicit authorization, before preparation or submission.

    The caller supplies the exact account, environment, current admission epoch
    and authorized tool arguments. Never use a new path to replace an uncertain
    operation. Existing files are read with load_intent(), never regenerated.
    """
    if path.exists():
        raise FileExistsError(path)
    if method not in MUTATIONS:
        raise ValueError("Use an individual trading mutation")
    if "operation_id" in parameters or "approval_id" in parameters:
        raise ValueError("Identity is generated once; approval is attached separately")
    account = parameters.get("acc_id")
    if (
        not isinstance(account, str)
        or not account.isascii()
        or not account.isdecimal()
        or account.startswith("0")
    ):
        raise ValueError("Supply an exact nonzero decimal string account ID")
    if parameters.get("trd_env") not in {"REAL", "SIMULATE"}:
        raise ValueError("Supply the explicitly authorized environment")
    epoch = parameters.get("admission_epoch")
    if (
        not isinstance(epoch, str)
        or not epoch.strip()
        or not epoch.isprintable()
        or len(epoch) > 64
    ):
        raise ValueError("Supply the current environment admission epoch")
    # Freeze a JSON copy before allocating the single operation ID.
    arguments = json.loads(json.dumps(parameters, allow_nan=False))
    arguments["operation_id"] = uuid.uuid4().hex
    intent = {"method": method, "parameters": arguments}
    _persist_exclusive(path, intent)
    return load_intent(path)


def load_intent(path: Path) -> dict[str, Any]:
    """Reload the original exact request after restart without allocating an ID."""
    return json.loads(path.read_text(encoding="utf-8"))


async def submit_once(
    path: Path,
    call_tool: ToolCall,
    *,
    prepared_trade: dict[str, Any] | None = None,
) -> Any:
    """Durably claim one attempt, then invoke the injected transport exactly once.

    Pass an APPROVED get_prepared_trade response when operator approval applies.
    Its complete parameters must exactly match the persisted request, including
    all defaults authorized before persistence. Added or changed fields fail closed.
    The server remains responsible for trusted approval, expiry and fresh checks.
    Errors, cancellations, lost responses and crashes never clear the attempt.
    """
    intent = load_intent(path)
    method, parameters = intent["method"], intent["parameters"]
    attempt = {"method": method, "parameters": parameters}
    if prepared_trade is not None:
        approved = prepared_trade["parameters"]
        if (
            prepared_trade.get("status") != "APPROVED"
            or prepared_trade.get("consumed_at") is not None
            or prepared_trade.get("method") != method
            or json.dumps(approved, sort_keys=True, allow_nan=False)
            != json.dumps(parameters, sort_keys=True, allow_nan=False)
            or not isinstance(prepared_trade.get("request_id"), str)
            or not prepared_trade["request_id"]
        ):
            raise ValueError("Approval must match the original exact authorized intent")
        parameters = approved | {"approval_id": prepared_trade["request_id"]}
        attempt = {
            "method": method,
            "parameters": parameters,
            "prepared_trade": prepared_trade,
        }
    marker = path.with_name(path.name + ".attempt.json")
    try:
        _persist_exclusive(marker, attempt)
    except FileExistsError as exc:
        raise AttemptAlreadyStarted(
            "Submission was already attempted; inspect the original operation_id"
        ) from exc
    return await call_tool(method, parameters)
