"""Offline verification of the Lab scanner's exact market-read catalogue.

A matching catalogue is not proof of execution authorization, connectivity,
quote rights or deployment identity. Those need separate acceptance evidence.
"""

import hashlib
import json
from typing import Any

from moomoo_mcp.client_authorization import ClientGrants

LAB_SCANNER_GRANTS = ClientGrants(
    permissions=frozenset({"market_read"}),
    real_acc_ids=frozenset(),
    simulate_acc_ids=frozenset(),
)

# Intentionally independent of MARKET_TOOLS: adding a permission classification
# must not silently expand the scanner's accepted catalogue.
LAB_SCANNER_TOOLS = frozenset(
    {
        "check_health",
        "get_market_snapshot_evidence",
        "get_instrument_facts",
        "get_history_kl_quota",
        "get_rehab",
        "get_corporate_actions_dividends",
        "get_earnings_calendar",
        "get_option_volatility",
        "get_stock_quote",
        "get_historical_klines",
        "get_historical_klines_page",
        "get_option_expiration_date",
        "get_option_chain",
        "get_market_snapshot",
        "get_order_book",
        "get_market_state",
        "get_trading_days",
        "get_subscriptions",
        "get_user_security_group",
        "get_user_security",
        "get_margin_ratio",
    }
)


def _verify_scanner_inputs(name: str, schema: dict[str, Any]) -> None:
    """Reject stale core discovery/facts schemas even without a saved hash."""
    if name not in {
        "get_option_expiration_date",
        "get_option_chain",
        "get_market_snapshot_evidence",
        "get_instrument_facts",
    }:
        return
    properties = schema.get("properties", {})
    argument = (
        "codes"
        if name in {"get_market_snapshot_evidence", "get_instrument_facts"}
        else "code"
    )
    if not isinstance(properties, dict) or schema.get("required") != [argument]:
        raise ValueError("Scanner core input schema is incompatible")
    if not all(isinstance(value, dict) for value in properties.values()):
        raise ValueError("Scanner core input properties are invalid")
    value = properties.get(argument, {})
    if argument == "code":
        compatible = value.get("type") == "string"
    else:
        compatible = (
            value.get("type") == "array"
            and isinstance(value.get("items"), dict)
            and value["items"].get("type") == "string"
            and value.get("minItems") == 1
            and value.get("maxItems") == 100
        )
    if not compatible:
        raise ValueError("Scanner core security-code schema is incompatible")
    if name == "get_option_chain":
        from moomoo_mcp.services.data_evidence import OptionChainFilter

        if (
            not {
                "start",
                "end",
                "option_type",
                "option_cond_type",
                "include_evidence",
                "data_filter",
            }
            <= properties.keys()
        ):
            raise ValueError("Scanner chain schema omits optional discovery inputs")
        if (
            properties["include_evidence"].get("type") != "boolean"
            or properties["include_evidence"].get("default") is not False
            or properties["data_filter"].get("anyOf")
            != [{"$ref": "#/$defs/OptionChainFilter"}, {"type": "null"}]
            or not isinstance(schema.get("$defs"), dict)
            or schema["$defs"].get("OptionChainFilter")
            != OptionChainFilter.model_json_schema()
        ):
            raise ValueError("Scanner chain evidence/filter schema is incompatible")


def verify_lab_scanner_catalogue(
    tools: list[dict[str, Any]], *, expected_sha256: str | None = None
) -> dict[str, Any]:
    """Require exact names/safety hints, optionally an approved schema hash.

    Accepts a complete tools/list (after bounded pagination), never invokes a
    tool. The hash convention matches server_contract_identity and the CLI.
    """
    if not isinstance(tools, list):
        raise ValueError("Catalogue must be a complete tool list")
    names = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Catalogue contains an invalid tool definition")
        name = tool.get("name")
        if not isinstance(name, str):
            raise ValueError("Catalogue contains an invalid tool name")
        names.append(name)
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("Catalogue contains an invalid input schema")
        _verify_scanner_inputs(name, schema)
        annotations = tool.get("annotations", {})
        if (
            not isinstance(annotations, dict)
            or annotations.get("readOnlyHint")
            is not (name not in {"get_stock_quote", "get_order_book"})
            or annotations.get("destructiveHint") is not False
        ):
            raise ValueError("Scanner catalogue contains unexpected safety hints")
    if len(set(names)) != len(names) or set(names) != LAB_SCANNER_TOOLS:
        raise ValueError(
            "Scanner catalogue mismatch: missing="
            + str(sorted(LAB_SCANNER_TOOLS - set(names)))
            + ", unexpected="
            + str(sorted(set(names) - LAB_SCANNER_TOOLS))
        )
    try:
        encoded = json.dumps(
            sorted(tools, key=lambda tool: tool["name"]),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Catalogue contains invalid JSON values") from None
    digest = hashlib.sha256(encoded).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Scanner catalogue differs from the approved schema hash")
    return {
        "profile": "lab-scanner.market_read.v1",
        "permissions": ["market_read"],
        "tool_names": sorted(names),
        "tools_schema_sha256": digest,
        "catalogue_verified": True,
        "core_input_schema_verified": True,
        "approved_schema_hash_verified": expected_sha256 is not None,
        "subscription_side_effect_tools": ["get_order_book", "get_stock_quote"],
        "execution_authorization_verified": False,
        "live_connection_verified": False,
    }
