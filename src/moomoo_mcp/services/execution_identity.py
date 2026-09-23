"""Opaque caller tokens and lossless request identity for paper execution."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any


def validate_token(operation_id: Any, admission_epoch: Any) -> None:
    for name, value in (
        ("operation_id", operation_id),
        ("admission_epoch", admission_epoch),
    ):
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.isprintable()
            or len(value) > 64
        ):
            raise ValueError(
                f"{name} must be a nonblank printable string of 1–64 characters"
            )


def decimal_price(value: Any) -> Decimal:
    """Accept bounded plain decimal text, never a number coerced to text."""
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("paper price must be a positive decimal string")
    parts = value.split(".")
    if len(parts) > 2 or any(
        not part or not all(c in "0123456789" for c in part) for part in parts
    ):
        raise ValueError("paper price must be a positive decimal string")
    number = Decimal(value)
    if number <= 0:
        raise ValueError("paper price must be positive")
    return number


def _canonical(value: Any, key: str = "") -> Any:
    if key == "price":
        number = decimal_price(value)
        # normalize() is context-sensitive and could round long decimal inputs.
        text = format(number, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, dict):
        return {k: _canonical(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def canonicalize_request(env: str, account: int, op_type: str, params: dict) -> str:
    return json.dumps(
        {
            "env": env,
            "account": str(account),
            "op_type": op_type,
            "params": _canonical(params),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_request(canonical_req: str) -> str:
    return hashlib.sha256(canonical_req.encode()).hexdigest()


def differing_fields(stored: str, proposed: str) -> list[str]:
    left, right = json.loads(stored), json.loads(proposed)
    fields = [key for key in ("env", "account", "op_type") if left[key] != right[key]]
    fields.extend(
        key
        for key in sorted(left["params"].keys() | right["params"].keys())
        if key not in left["params"]
        or key not in right["params"]
        or left["params"][key] != right["params"][key]
    )
    return fields
