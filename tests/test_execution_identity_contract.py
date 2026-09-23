"""Identity must survive decimal spelling and retain caller patch boundaries."""

from moomoo_mcp.services.execution_identity import (
    canonicalize_request,
    fingerprint_request,
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
