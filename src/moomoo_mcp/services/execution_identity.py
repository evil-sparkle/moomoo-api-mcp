"""Opaque caller tokens and lossless request identity for paper execution."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, Field

IDENTITY_TOKEN_MIN_LENGTH = 1
IDENTITY_TOKEN_MAX_LENGTH = 64


class IdentityTokenValidationError(ValueError):
    """A local token rejection with diagnostics that never contain its value."""

    def __init__(self, field: str, constraint: str, actual_length: int | None):
        self.field = field
        self.constraint = constraint
        self.min_length = IDENTITY_TOKEN_MIN_LENGTH
        self.max_length = IDENTITY_TOKEN_MAX_LENGTH
        self.actual_length = actual_length
        super().__init__(
            f"{field} must be a nonblank printable string of "
            f"{IDENTITY_TOKEN_MIN_LENGTH}–{IDENTITY_TOKEN_MAX_LENGTH} characters "
            f"(violated constraint: {constraint})"
        )

    def metadata(self) -> dict[str, Any]:
        facts: dict[str, Any] = {
            "field": self.field,
            "constraint": self.constraint,
            "min_length": IDENTITY_TOKEN_MIN_LENGTH,
            "max_length": IDENTITY_TOKEN_MAX_LENGTH,
        }
        if self.actual_length is not None:
            facts["actual_length"] = self.actual_length
        return facts


def validate_identity_token(field: str, value: Any) -> str:
    """Validate an opaque exact token without normalizing printable Unicode."""
    constraint = None
    actual_length = len(value) if isinstance(value, str) else None
    if value is None:
        constraint = "required"
    elif not isinstance(value, str):
        constraint = "strict_string"
    elif len(value) < IDENTITY_TOKEN_MIN_LENGTH:
        constraint = "min_length"
    elif len(value) > IDENTITY_TOKEN_MAX_LENGTH:
        constraint = "max_length"
    elif not value.strip():
        constraint = "nonblank"
    elif not value.isprintable():
        constraint = "printable"
    if constraint is not None:
        raise IdentityTokenValidationError(field, constraint, actual_length)
    assert isinstance(value, str)
    return value


def _operation_id(value: str) -> str:
    return validate_identity_token("operation_id", value)


def _admission_epoch(value: str) -> str:
    return validate_identity_token("admission_epoch", value)


OperationId = Annotated[
    str,
    Field(
        strict=True,
        min_length=IDENTITY_TOKEN_MIN_LENGTH,
        max_length=IDENTITY_TOKEN_MAX_LENGTH,
        description=(
            "Caller-owned opaque operation token: 1–64 printable characters, "
            "nonblank, preserved exactly; UUID format is not required. Generate "
            "once for a NEW authorized intent, for example uuid.uuid4().hex, "
            "never per invocation. Retain unchanged after admission and for "
            "every retry and recovery of that intent."
        ),
    ),
    AfterValidator(_operation_id),
]
AdmissionEpoch = Annotated[
    str,
    Field(
        strict=True,
        min_length=IDENTITY_TOKEN_MIN_LENGTH,
        max_length=IDENTITY_TOKEN_MAX_LENGTH,
        description=(
            "Opaque journal epoch: use check_health.execution_journal for "
            "SIMULATE or check_health.real_execution_journal for REAL. "
            "1–64 printable nonblank characters, preserved exactly. Never "
            "invent or refresh an epoch to bypass refusal; retain the original "
            "for every previously attempted operation and its recovery."
        ),
    ),
    AfterValidator(_admission_epoch),
]


def validate_token(operation_id: Any, admission_epoch: Any) -> None:
    for name, value in (
        ("operation_id", operation_id),
        ("admission_epoch", admission_epoch),
    ):
        validate_identity_token(name, value)


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
