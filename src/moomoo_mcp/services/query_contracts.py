"""Typed read contracts that preserve broker fields and exact identifiers."""

import math
from datetime import date, datetime
from numbers import Integral, Real
from typing import Any

from moomoo import ComboLeg
from typing_extensions import TypedDict

IDENTIFIERS = frozenset({"acc_id", "order_id", "deal_id", "position_id", "combo_id"})
COMBO_FIELDS = ("code", "trd_side", "qty_ratio", "position_id", "pred_side")


class TradeQueryError(RuntimeError):
    """A classified broker query or response failure, with no mutation."""

    def __init__(self, code: str, operation: str, message: str):
        self.code = code
        self.operation = operation
        super().__init__(f"{operation}: {code}: {message}")


class QueryScope(TypedDict):
    source: str
    acc_id: str
    trd_env: str
    filters: dict[str, Any]
    coverage: str


class OrderView(TypedDict):
    order_id: str | None
    code: str | None
    trd_side: str | None
    order_type: str | None
    order_status: str | None
    qty: float | None
    price: float | None
    dealt_qty: float | None
    dealt_avg_price: float | None
    raw: dict[str, Any]


class DealView(TypedDict):
    deal_id: str | None
    order_id: str | None
    code: str | None
    trd_side: str | None
    qty: float | None
    price: float | None
    raw: dict[str, Any]


class OrderQueryResult(TypedDict):
    found: bool
    order: OrderView | None
    scope: QueryScope
    fetched_at: str


class OrderFeeResult(TypedDict):
    order_id: str
    fee_amount: float | None
    fee_details: list[dict[str, Any]] | None


class QueryPage(TypedDict):
    items: list[OrderView | DealView]
    next_cursor: str | None
    total_rows: int
    returned_rows: int
    scope: QueryScope
    fetched_at: str


def plain_combo_legs(records: list[dict]) -> list[dict]:
    """Normalize SDK legs while retaining exact identifiers for MCP serialization.

    DataFrame.to_dict owns these records, so updating them does not mutate SDK data.
    """
    for record in records:
        legs = record.get("combo_legs")
        if not isinstance(legs, (list, tuple)):
            continue
        record["combo_legs"] = [
            {field: getattr(leg, field, None) for field in COMBO_FIELDS}
            if isinstance(leg, ComboLeg)
            else leg
            for leg in legs
        ]
    return records


def exact_identifier(value: Any, field: str) -> str | None:
    """Refuse floating-point IDs instead of hiding lost precision."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TradeQueryError("INVALID_IDENTIFIER", "response", f"{field} is boolean")
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, str):
        return value
    if isinstance(value, Real) and math.isnan(float(value)):
        return None
    raise TradeQueryError(
        "INVALID_IDENTIFIER", "response", f"{field} must be an exact string or integer"
    )


def plain_record(value: Any, field: str = "") -> Any:
    """Copy SDK records to JSON values; unavailable numbers remain null."""
    if field in IDENTIFIERS:
        return exact_identifier(value, field)
    if isinstance(value, ComboLeg):
        return {
            key: plain_record(getattr(value, key, None), key) for key in COMBO_FIELDS
        }
    if isinstance(value, dict):
        return {key: plain_record(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_record(item) for item in value]
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TradeQueryError(
        "INVALID_RESPONSE", "response", f"unsupported field type {type(value).__name__}"
    )


def optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def optional_text(value: Any) -> str | None:
    if value is None or value == "N/A":
        return None
    return str(value)


def order_view(record: dict[str, Any]) -> OrderView:
    raw = plain_record(record)
    return {
        "order_id": raw.get("order_id"),
        "code": optional_text(raw.get("code")),
        "trd_side": optional_text(raw.get("trd_side")),
        "order_type": optional_text(raw.get("order_type")),
        "order_status": optional_text(raw.get("order_status")),
        "qty": optional_number(raw.get("qty")),
        "price": optional_number(raw.get("price")),
        "dealt_qty": optional_number(raw.get("dealt_qty")),
        "dealt_avg_price": optional_number(raw.get("dealt_avg_price")),
        "raw": raw,
    }


def deal_view(record: dict[str, Any]) -> DealView:
    raw = plain_record(record)
    return {
        "deal_id": raw.get("deal_id"),
        "order_id": raw.get("order_id"),
        "code": optional_text(raw.get("code")),
        "trd_side": optional_text(raw.get("trd_side")),
        "qty": optional_number(raw.get("qty")),
        "price": optional_number(raw.get("price")),
        "raw": raw,
    }
