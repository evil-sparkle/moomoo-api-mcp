"""Versioned broker observations; configuration is never live verification."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

from moomoo import OpenQuoteContext
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import NotRequired, Self, TypedDict

from moomoo_mcp.client_authorization import tool_visible
from moomoo_mcp.services.broker_dispatch import ReadTiming
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.query_contracts import TradeQueryError, plain_record
from moomoo_mcp.services.sdk_response import as_frame

DOC_ROOT = "https://openapi.moomoo.com/moomoo-api-doc/en/quote/"
EVIDENCE_VERSION = "2026-10-26.1"
MAX_DATA_ROWS = 2000
MAX_DATA_BYTES = 2 * 1024 * 1024


def frame_records(operation: str, payload: Any) -> list[dict]:
    frame = as_frame(operation, payload)
    if len(frame) > MAX_DATA_ROWS:
        raise TradeQueryError("SNAPSHOT_LIMIT", operation, "narrow the query")
    if len(frame) and not len(frame.columns):
        raise TradeQueryError("INVALID_RESPONSE", operation, "rows have no fields")
    return frame.to_dict("records")


class Coverage(TypedDict):
    requested: list[str]
    returned: list[str]
    missing: list[str]
    complete_response: bool
    provider_history_complete: None
    truncated: bool
    total_returned: int


class DataEvidence(TypedDict):
    schema_version: str
    source_method: str
    requested_at: str
    received_at: str
    sdk_version: str | None
    source_revision: str | None
    delay_status: Literal["UNKNOWN"]
    entitlement_status: Literal["UNKNOWN"]
    timestamp_semantics: str
    scope: dict[str, Any]
    coverage: Coverage
    errors: dict[str, str]
    data: list[dict[str, Any]]
    limitations: list[str]
    timing: NotRequired[ReadTiming]


def sdk_version() -> str | None:
    try:
        return version("moomoo-api")
    except PackageNotFoundError:
        return None


def validate_codes(codes: list[str], maximum: int = 100) -> list[str]:
    if not isinstance(codes, list) or not 1 <= len(codes) <= maximum:
        raise ValueError(f"codes must contain 1 to {maximum} exact security codes")
    for code in codes:
        if not isinstance(code, str) or len(code) > 80 or code != code.strip():
            raise ValueError("codes must be exact market-prefixed security strings")
        market, separator, symbol = code.partition(".")
        if not separator or not market or not symbol or any(c.isspace() for c in code):
            raise ValueError("codes must be exact market-prefixed security strings")
    if len(set(codes)) != len(codes):
        raise ValueError("codes must not contain duplicates")
    return codes


def bounded_rows(rows: list[dict], maximum: int = MAX_DATA_ROWS) -> list[dict]:
    import json

    if len(rows) > maximum:
        raise TradeQueryError("SNAPSHOT_LIMIT", "market_data", "narrow the query")
    plain = plain_record(rows)
    size = 0
    for chunk in json.JSONEncoder(allow_nan=False).iterencode(plain):
        size += len(chunk.encode("utf-8"))
        if size > MAX_DATA_BYTES:
            raise TradeQueryError("SNAPSHOT_LIMIT", "market_data", "response too large")
    return plain


def evidence(
    method: str,
    started: str,
    rows: list[dict],
    *,
    requested: list[str] | None = None,
    key: str = "code",
    scope: dict[str, Any] | None = None,
    limit: int = MAX_DATA_ROWS,
    limitations: list[str] | None = None,
) -> DataEvidence:
    """Coverage means returned identifiers, never a claim of complete history."""
    rows = bounded_rows(rows)
    wanted = requested or []
    returned = [str(row[key]) for row in rows if row.get(key) is not None]
    if len(set(returned)) != len(returned) or (wanted and set(returned) - set(wanted)):
        raise TradeQueryError("INVALID_RESPONSE", method, "duplicate/unexpected rows")
    missing = [code for code in wanted if code not in returned]
    return {
        "schema_version": EVIDENCE_VERSION,
        "source_method": method,
        "requested_at": started,
        "received_at": utc_now_iso(),
        "sdk_version": sdk_version(),
        "source_revision": None,
        "delay_status": "UNKNOWN",
        "entitlement_status": "UNKNOWN",
        "timestamp_semantics": "Provider fields preserved verbatim; observation "
        "times are UTC. Snapshot update_time is not a per-side exchange timestamp.",
        "scope": scope or {},
        "coverage": {
            "requested": wanted,
            "returned": returned[:limit],
            "missing": missing,
            "complete_response": not missing and len(rows) <= limit,
            "provider_history_complete": None,
            "truncated": len(rows) > limit,
            "total_returned": len(rows),
        },
        "errors": dict.fromkeys(missing, "NOT_RETURNED_CAUSE_UNKNOWN"),
        "data": rows[:limit],
        "limitations": limitations or [],
    }


# Deliberately selected upstream filters; no arbitrary SDK kwargs are exposed.
class OptionChainFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    implied_volatility_min: float | None = Field(default=None, ge=0, le=10000)
    implied_volatility_max: float | None = Field(default=None, ge=0, le=10000)
    delta_min: float | None = Field(default=None, ge=-1, le=1)
    delta_max: float | None = Field(default=None, ge=-1, le=1)
    gamma_min: float | None = Field(default=None, ge=0)
    gamma_max: float | None = Field(default=None, ge=0)
    vega_min: float | None = Field(default=None, ge=0)
    vega_max: float | None = Field(default=None, ge=0)
    theta_min: float | None = None
    theta_max: float | None = None
    rho_min: float | None = None
    rho_max: float | None = None
    open_interest_min: int | None = Field(default=None, ge=0, le=2**53 - 1)
    open_interest_max: int | None = Field(default=None, ge=0, le=2**53 - 1)
    vol_min: int | None = Field(default=None, ge=0, le=2**53 - 1)
    vol_max: int | None = Field(default=None, ge=0, le=2**53 - 1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        values = self.model_dump(exclude_none=True)
        for field, low in values.items():
            if field.endswith("_min"):
                high = values.get(field.removesuffix("_min") + "_max")
                if high is not None and low > high:
                    raise ValueError("filter minimum must not exceed maximum")
        return self


# Method -> tool, upstream page, implementation decision. No arbitrary dispatch.
DATA_CAPABILITIES = {
    "get_market_snapshot": (
        "get_market_snapshot_evidence",
        "get-market-snapshot",
        "implemented",
    ),
    "get_stock_basicinfo": ("get_instrument_facts", "get-static-info", "implemented"),
    "get_option_chain": ("get_option_chain", "get-option-chain", "implemented"),
    "get_history_kl_quota": (
        "get_history_kl_quota",
        "get-history-kl-quota",
        "implemented",
    ),
    "get_rehab": ("get_rehab", "get-rehab", "implemented"),
    "get_corporate_actions_dividends": (
        "get_corporate_actions_dividends",
        "get-corporate-actions-dividends",
        "implemented",
    ),
    "get_earnings_calendar": (
        "get_earnings_calendar",
        "get-earnings-calendar",
        "implemented",
    ),
    "get_option_volatility": (
        "get_option_volatility",
        "get-option-volatility",
        "implemented",
    ),
    "get_option_strategy": (None, "get-option-strategy", "deferred"),
    "get_option_strategy_spread": (None, "get-option-strategy-spread", "deferred"),
    "get_option_quote": (None, "get-option-quote", "deferred"),
    "get_option_strategy_analysis": (None, "get-option-strategy-analysis", "deferred"),
    "get_stock_filter": (None, "get-stock-filter", "deferred"),
    "get_financials_statements": (None, "get-financials-statements", "deferred"),
}


def capability_evidence(visible_tools: set[str]) -> dict[str, Any]:
    """Inspect local code/schema only; never connect or infer provider acceptance."""
    return {
        "schema_version": EVIDENCE_VERSION,
        "checked_at": utc_now_iso(),
        "sdk_version": sdk_version(),
        "region": None,
        "entitlement": "UNKNOWN",
        "connector_import": "UNKNOWN",
        "operations": [
            {
                "method": method,
                "documentation": DOC_ROOT + page + ".html",
                "documentation_version": "v10.11",
                "documentation_reviewed_at": "2026-10-26",
                "sdk_method_present": callable(getattr(OpenQuoteContext, method, None)),
                "implementation": decision,
                "registered": True if tool in visible_tools else None,
                "tool": tool,
                "credential_permitted": tool_visible(tool) if tool else None,
                "served_schema_present": tool in visible_tools if tool else False,
                "connector_import": "UNKNOWN",
                "live_verification": "UNKNOWN",
                "gateway_support": "UNKNOWN",
            }
            for method, (tool, page, decision) in DATA_CAPABILITIES.items()
        ],
    }
