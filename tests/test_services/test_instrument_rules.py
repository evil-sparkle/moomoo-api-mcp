"""Documented grids and sessions without inferred option ticks or rounding."""

from decimal import Decimal

import pytest

from moomoo_mcp.services.instrument_rules import (
    instrument_rule_summary,
    normalize_order_session,
    validate_instrument_order,
    validate_order_session,
    whole_quantity,
)
from moomoo_mcp.services.real_execution import canonical_real_request
from moomoo_mcp.services.trading_policy import InstrumentFacts


def equity(code="US.AAPL"):
    return InstrumentFacts(code=code, classification="STOCK", lot_size=1)


@pytest.mark.parametrize("price", ["0.9999", "0.0001", "1", "1.01", "1.0100"])
def test_us_equity_accepts_exact_grid_and_trailing_zeroes(price):
    result = validate_instrument_order(
        "place", instrument=equity(), qty=1, order_type="NORMAL", price=price
    )
    assert result["price_tick"] == ("0.0001" if Decimal(price) < 1 else "0.01")
    assert result["tick_validation"] == "VERIFIED"


@pytest.mark.parametrize("price", ["0.99999", "1.0001", "1.015", "1e-1000000"])
def test_us_equity_refuses_off_grid_without_rounding(price):
    with pytest.raises(ValueError) as error:
        validate_instrument_order(
            "place", instrument=equity(), qty=1, order_type="NORMAL", price=price
        )
    assert "documented" in str(error.value)


def test_a_share_grid_is_one_cent_even_below_one():
    rules = instrument_rule_summary(equity("SH.600000"), Decimal("0.5"))
    assert rules["price_tick"] == "0.01"
    with pytest.raises(ValueError):
        validate_instrument_order(
            "place",
            instrument=equity("SZ.000001"),
            qty=100,
            order_type="NORMAL",
            price=Decimal("0.5001"),
        )


def test_option_precision_leaves_product_tick_explicitly_unknown():
    option = InstrumentFacts(
        code="US.AAPL260116C150000",
        classification="DRVT",
        lot_size=25,
        monetary_multiplier=100,
        contract_size=25,
    )
    rules = validate_instrument_order(
        "place", instrument=option, qty=1, order_type="NORMAL", price="1.03"
    )
    assert rules["quantity_unit"] == "contracts"
    assert rules["price_decimal_places"] == 2
    assert rules["tick_validation"] == "UNKNOWN"
    assert rules["price_tick"] is None
    assert rules["lot_size"] == 25
    assert rules["lot_size_meaning"] == "deliverable_shares_per_contract"
    with pytest.raises(ValueError):
        validate_instrument_order(
            "place", instrument=option, qty=1, order_type="NORMAL", price="1.031"
        )


def test_hk_lot_does_not_turn_odd_lot_sale_into_quantity_multiple_rule():
    stock = InstrumentFacts(code="HK.00700", classification="STOCK", lot_size=100)
    rules = validate_instrument_order(
        "modify", instrument=stock, qty=1, order_type="NORMAL", price="350.123"
    )
    assert rules["tick_validation"] == "UNKNOWN"
    assert rules["lot_size_meaning"] == "shares_per_lot"


def test_missing_classification_refuses_even_without_valuation():
    with pytest.raises(ValueError) as error:
        validate_instrument_order(
            "place",
            instrument=InstrumentFacts(code="US.AAPL"),
            qty=1,
            order_type="NORMAL",
            price=150,
        )
    assert "classification" in str(error.value)


@pytest.mark.parametrize(
    "order_type", ["TWAP", "TWAP_LIMIT", "VWAP", "VWAP_LIMIT", "NEW_TYPE"]
)
def test_query_only_and_unknown_order_types_are_not_submission_capabilities(order_type):
    with pytest.raises(ValueError) as error:
        validate_instrument_order(
            "place", instrument=equity(), qty=1, order_type=order_type, price=150
        )
    assert "submission order type" in str(error.value)


@pytest.mark.parametrize("qty", [1.5, Decimal("2.01"), "2", True, 0, float("nan")])
def test_quantity_is_never_truncated_or_coerced(qty):
    with pytest.raises(ValueError):
        whole_quantity("modify", qty)


def test_sdk_whole_quantity_float_remains_acceptable():
    assert whole_quantity("modify", 2.0) == 2


def test_market_zero_price_is_not_rewritten_and_trigger_grid_is_checked():
    validate_instrument_order(
        "place", instrument=equity(), qty=1, order_type="MARKET", price=0
    )
    with pytest.raises(ValueError) as error:
        validate_instrument_order(
            "place",
            instrument=equity(),
            qty=1,
            order_type="STOP_LIMIT",
            price=150,
            aux_price=149.005,
        )
    assert "aux_price" in str(error.value)


@pytest.mark.parametrize("session", ["N/A", "NONE", " rth "])
def test_paper_aliases_keep_the_existing_regular_session(session):
    assert normalize_order_session(session, trd_env="SIMULATE") == "RTH"


@pytest.mark.parametrize("session", ["ETH", "OVERNIGHT", "ALL"])
def test_paper_sessions_cannot_broaden_scope(session):
    with pytest.raises(ValueError):
        normalize_order_session(session, trd_env="SIMULATE")


def test_explicit_extended_sessions_are_us_equity_limit_only():
    assert validate_order_session("US.AAPL", "STOCK", "NORMAL", "eth") == "ETH"
    for code, classification, order_type in [
        ("HK.00700", "STOCK", "NORMAL"),
        ("US.AAPL260116C150000", "DRVT", "NORMAL"),
        ("US.AAPL", "STOCK", "MARKET"),
    ]:
        with pytest.raises(ValueError):
            validate_order_session(code, classification, order_type, "ETH")
    assert validate_order_session("HK.00700", "STOCK", "NORMAL", "N/A") == "N/A"


def test_old_real_identity_is_preserved_only_for_the_sdk_default():
    params = {"code": "US.AAPL", "price": 150, "qty": 2}
    old = canonical_real_request(123, "PLACE", params)
    assert canonical_real_request(123, "PLACE", params | {"session": "N/A"}) == old
    assert canonical_real_request(123, "PLACE", params | {"session": "NONE"}) == old
    explicit = canonical_real_request(123, "PLACE", params | {"session": "eth"})
    assert explicit == canonical_real_request(123, "PLACE", params | {"session": "ETH"})
    assert explicit != old
