"""Identity must survive decimal spelling and retain caller patch boundaries."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from moomoo_mcp.services.execution_identity import (
    IDENTITY_TOKEN_MAX_LENGTH,
    IDENTITY_TOKEN_MIN_LENGTH,
    AdmissionEpoch,
    IdentityTokenValidationError,
    OperationId,
    canonicalize_request,
    fingerprint_request,
    validate_token,
)


def identity(parameters: dict) -> str:
    return fingerprint_request(
        canonicalize_request("SIMULATE", 123456, "MODIFY", parameters)
    )


def test_decimal_spelling_is_not_an_operation_change() -> None:
    assert identity({"order_id": "99", "price": "350.00"}) == identity(
        {"price": "350.0", "order_id": "99"}
    )


def test_decimal_identity_does_not_round_to_float_precision() -> None:
    assert identity({"price": "1.00000000000000001"}) != identity(
        {"price": "1.00000000000000002"}
    )


def test_explicit_quantity_changes_price_only_patch_identity() -> None:
    assert identity({"order_id": "99", "price": "50"}) != identity(
        {"order_id": "99", "price": "50", "qty": 100}
    )


def test_order_target_is_part_of_identity() -> None:
    assert identity({"order_id": "99", "price": "50"}) != identity(
        {"order_id": "100", "price": "50"}
    )


@pytest.mark.parametrize("token", ["a", "x" * 64, "mañana-Δ-🦊", " exact token "])
def test_opaque_tokens_preserve_printable_unicode_and_exact_spelling(token):
    validate_token(token, token)
    assert TypeAdapter(OperationId).validate_python(token) == token
    assert TypeAdapter(AdmissionEpoch).validate_python(token) == token


@pytest.mark.parametrize(
    ("token", "constraint", "actual_length"),
    [
        (None, "required", None),
        (123, "strict_string", None),
        (b"bytes", "strict_string", None),
        ("", "min_length", 0),
        (" " * 4, "nonblank", 4),
        ("line\nbreak", "printable", 10),
        ("nul\x00token", "printable", 9),
        ("private-token-" + "x" * 53, "max_length", 67),
    ],
)
@pytest.mark.parametrize("field", ["operation_id", "admission_epoch"])
def test_runtime_token_validation_exposes_only_sanitized_facts(
    token, constraint, actual_length, field
):
    arguments = {"operation_id": "original", "admission_epoch": "epoch"}
    arguments[field] = token
    with pytest.raises(IdentityTokenValidationError) as excinfo:
        validate_token(**arguments)
    error = excinfo.value
    assert error.field == field
    assert error.constraint == constraint
    assert error.min_length == IDENTITY_TOKEN_MIN_LENGTH == 1
    assert error.max_length == IDENTITY_TOKEN_MAX_LENGTH == 64
    assert error.actual_length == actual_length
    assert ("actual_length" in error.metadata()) is (actual_length is not None)
    assert "private-token" not in str(error) + json.dumps(error.metadata())


@pytest.mark.parametrize("alias", [OperationId, AdmissionEpoch])
@pytest.mark.parametrize("token", [None, 123, b"bytes", "", "   ", "a\nb", "x" * 65])
def test_pydantic_tokens_reject_without_coercion(alias, token):
    with pytest.raises(ValidationError):
        TypeAdapter(alias).validate_python(token)


@pytest.mark.parametrize("alias", [OperationId, AdmissionEpoch])
def test_token_schema_exposes_shared_bounds_without_requiring_uuid(alias):
    schema = TypeAdapter(alias).json_schema()
    assert schema["type"] == "string"
    assert schema["minLength"] == IDENTITY_TOKEN_MIN_LENGTH
    assert schema["maxLength"] == IDENTITY_TOKEN_MAX_LENGTH
    assert "format" not in schema
    assert "pattern" not in schema
