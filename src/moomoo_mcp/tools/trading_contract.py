"""Published trading inputs and non-sensitive served-contract identity.

Conditional schemas describe the existing executor requirements. Runtime admission
and approval remain authoritative; publishing a schema never grants a write.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any

from mcp.types import Tool
from pydantic import Field, StrictInt, StrictStr

from moomoo_mcp.services.execution_identity import (
    IDENTITY_TOKEN_MAX_LENGTH,
    IDENTITY_TOKEN_MIN_LENGTH,
)

CONTRACT_VERSION = "2026-10-22.1"
MUTATIONS = frozenset(
    {"place_order", "place_combo_order", "modify_order", "cancel_order"}
)
TradingEnvironment = Annotated[
    str,
    Field(
        description="Explicit REAL or SIMULATE environment. Never infer, fall back "
        "or switch environments after a refusal. Paper combos are unsupported."
    ),
]
ExactAccountId = Annotated[
    StrictStr,
    Field(
        description="Copy the exact decimal STRING from get_accounts for this "
        "environment; never convert through floating point. '0' resolves only "
        "one eligible authorized account, otherwise refuses. Prefer an explicit "
        "account and persist it with the exact intent before submission."
    ),
]
ExactBrokerId = Annotated[
    StrictStr,
    Field(
        description="Copy the exact positive decimal STRING from the broker read "
        "response in the original account and environment; no lossy numeric "
        "conversion, reconstruction or arbitrary account selection."
    ),
]
OrderPrice = Annotated[
    str | float,
    Field(
        description="SIMULATE: positive plain decimal STRING, e.g. '10.25', "
        "at most 64 characters; no floating point, sign or exponent notation. "
        "REAL: numeric price under existing order-type validation. A changed "
        "price changes the trade and its approval."
    ),
]
OrderQuantity = Annotated[
    StrictInt,
    Field(
        gt=0,
        description="Positive whole-share integer quantity; booleans and fractions "
        "are invalid. Paper scope is US stocks/ETFs, NORMAL/DAY/RTH only. "
        "Modification quantity means total order quantity, not additional shares.",
    ),
]
TimeInForce = Annotated[
    str,
    Field(
        description="DAY or GTC under existing order/account support. SIMULATE "
        "accepts DAY only. DAY validity and retention across sessions depend on "
        "the broker; neither acceptance nor a future fill is guaranteed."
    ),
]
ComboQuantity = Annotated[
    StrictInt,
    Field(
        gt=0,
        description="Positive integer package quantity. Each leg quantity is this "
        "quantity times its required positive integer qty_ratio. REAL only; "
        "paper combo mutations remain unsupported.",
    ),
]
ComboLegs = Annotated[
    list[dict],
    Field(
        description="At least two same-market legs with code, trd_side and "
        "positive integer qty_ratio. Closing position_id values must preserve "
        "the exact decimal STRING from get_positions; never round or reconstruct "
        "broker identifiers through floating point."
    ),
]
ExactApprovalId = Annotated[
    str,
    Field(
        description="request_id of the separately operator-approved prepared "
        "trade, when required. Bound to exact request, operation ID and epoch; "
        "never transfer approval to a replacement identity. Chat authorization "
        "does not grant server-side operator approval."
    ),
]
OrderRemark = Annotated[
    str,
    Field(
        description="Optional human-readable note. Keep descriptions here, not "
        "inside an oversized operation ID. Journaled placement uses its existing "
        "server-generated order_tag as broker remark and retains this note in "
        "the journal. Preserve the approved exact request."
    ),
]
ExecutionSession = Annotated[
    str,
    Field(
        description="Execution eligibility: N/A (SDK default), RTH, ETH, OVERNIGHT "
        "or ALL. SIMULATE accepts N/A or RTH and sends RTH, NORMAL/DAY only. "
        "REAL explicit sessions require verified US stocks/ETFs; non-RTH requires "
        "NORMAL limit orders, DAY/GTC. RTH is not a local market-open submission "
        "gate: authorized outside-RTH submission is possible. Broker acceptance, "
        "DAY validity, pending status and fills remain broker-dependent. No scheduler."
    ),
]


def published_input_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Add JSON Schema conditionals without changing legacy Python call defaults."""
    if name not in MUTATIONS:
        return schema
    result = deepcopy(schema)
    # Keep legacy optional signatures: disabled executors still fail closed in the
    # existing runtime. Clients can now see that admitted mutations need both IDs.
    required_token = {
        "type": "string",
        "minLength": IDENTITY_TOKEN_MIN_LENGTH,
        "maxLength": IDENTITY_TOKEN_MAX_LENGTH,
    }
    result["required"] = list(
        dict.fromkeys([*result.get("required", []), "operation_id", "admission_epoch"])
    )
    conditions: list[dict[str, Any]] = [
        {
            "properties": {
                "operation_id": dict(required_token),
                "admission_epoch": dict(required_token),
            }
        }
    ]
    result["allOf"] = conditions
    if name == "place_combo_order":
        conditions.append({"properties": {"trd_env": {"const": "REAL"}}})
        return result
    paper: dict[str, Any] = {}
    if name in {"place_order", "modify_order"}:
        paper.update(
            price={"type": "string", "minLength": 1, "maxLength": 64},
            qty={"type": "integer", "exclusiveMinimum": 0},
            adjust_limit={"const": 0},
        )
        if name == "modify_order":
            # Omitted patch fields remain supported; explicit null also means
            # omitted in this existing interface. Neither enables an empty patch.
            paper["price"] = {"anyOf": [paper["price"], {"type": "null"}]}
            paper["qty"] = {"anyOf": [paper["qty"], {"type": "null"}]}
    if name == "place_order":
        paper.update(
            order_type={"const": "NORMAL"},
            time_in_force={"const": "DAY"},
            session={"enum": ["N/A", "RTH", "NONE"]},
            trd_side={"enum": ["BUY", "SELL"]},
        )
        for field in ("aux_price", "trail_type", "trail_value", "trail_spread"):
            paper[field] = {"type": "null"}
    elif name == "modify_order":
        paper["modify_order_op"] = {"enum": ["NORMAL", "CANCEL"]}
    if paper:
        conditions.append(
            {
                "if": {
                    "properties": {"trd_env": {"const": "SIMULATE"}},
                    "required": ["trd_env"],
                },
                "then": {"properties": paper},
            }
        )
    return result


def server_contract_identity(tools: list[Tool]) -> dict[str, str]:
    """Hash exactly the visible tools/list contract, never credentials or records."""
    definitions = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in sorted(tools, key=lambda tool: tool.name)
    ]
    encoded = json.dumps(
        definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    try:
        package_version = version("moomoo-api-mcp")
    except PackageNotFoundError:
        package_version = "unknown"
    return {
        "package_version": package_version,
        "contract_version": CONTRACT_VERSION,
        # This package does not currently embed a trustworthy source revision.
        # OpenD's gateway version and a host's cached schema cannot supply it.
        "source_revision": "unknown",
        "tools_schema_sha256": hashlib.sha256(encoded).hexdigest(),
    }
