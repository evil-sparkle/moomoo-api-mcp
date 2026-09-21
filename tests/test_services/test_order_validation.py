"""Numeric validation of order values, before anything reaches the gateway.

These checks exist so that everything downstream — the notional assessment in
particular — is arithmetic over real numbers. A `nan` price is the case worth
keeping in mind: it does not raise anywhere on its own, because every
comparison against it is false, so it silently passes whatever limit it meets.
"""

import pytest

from moomoo_mcp.services.validation import (
    FIXED_LIMIT_TYPES,
    NO_FIXED_LIMIT_TYPES,
    validate_order_values,
    validate_required_order_fields,
)


class TestOrderTypeClasses:
    """The two classes the reference-price rule and validation share."""

    def test_the_classes_do_not_overlap(self):
        assert not (FIXED_LIMIT_TYPES & NO_FIXED_LIMIT_TYPES)

    def test_trailing_stop_limit_is_not_a_fixed_limit_type(self):
        """Its limit follows the trail, so a zero price is meaningful."""
        assert "TRAILING_STOP_LIMIT" in NO_FIXED_LIMIT_TYPES
        assert "TRAILING_STOP_LIMIT" not in FIXED_LIMIT_TYPES

    def test_algorithmic_types_are_in_neither_class(self):
        """TWAP/VWAP are documented as query-only, so they are not classified.

        Leaving them out is what makes an order type nobody has classified a
        refusal rather than a silent pass.
        """
        for order_type in ("TWAP", "TWAP_LIMIT", "VWAP", "VWAP_LIMIT"):
            assert order_type not in FIXED_LIMIT_TYPES
            assert order_type not in NO_FIXED_LIMIT_TYPES


class TestQuantity:
    @pytest.mark.parametrize("qty", [0, -1, -100])
    def test_non_positive_quantity_is_refused(self, qty):
        with pytest.raises(ValueError, match="greater than 0"):
            validate_order_values("place_order", order_type="NORMAL", qty=qty)

    @pytest.mark.parametrize("qty", [True, False])
    def test_boolean_quantity_is_refused(self, qty):
        """bool is an int subclass, so True would otherwise pass as one share."""
        with pytest.raises(ValueError, match="positive integer"):
            validate_order_values("place_order", order_type="NORMAL", qty=qty)

    @pytest.mark.parametrize("qty", [1.5, 10.0, "10"])
    def test_non_integer_quantity_is_refused(self, qty):
        with pytest.raises(ValueError, match="positive integer"):
            validate_order_values("place_order", order_type="NORMAL", qty=qty)

    def test_a_positive_integer_passes(self):
        validate_order_values("place_order", order_type="MARKET", qty=1)


class TestPrices:
    @pytest.mark.parametrize(
        "field", ["price", "aux_price", "trail_value", "trail_spread"]
    )
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_values_are_refused(self, field, value):
        with pytest.raises(ValueError, match="finite number"):
            validate_order_values("place_order", order_type="MARKET", **{field: value})

    @pytest.mark.parametrize(
        "field", ["price", "aux_price", "trail_value", "trail_spread"]
    )
    def test_negative_values_are_refused(self, field):
        with pytest.raises(ValueError, match="must not be negative"):
            validate_order_values("place_order", order_type="MARKET", **{field: -1.0})

    def test_non_numeric_price_is_refused(self):
        with pytest.raises(ValueError, match="must be a number"):
            validate_order_values("place_order", order_type="MARKET", price="100")

    @pytest.mark.parametrize("order_type", sorted(FIXED_LIMIT_TYPES))
    def test_fixed_limit_types_require_a_positive_price(self, order_type):
        with pytest.raises(ValueError, match="greater than 0"):
            validate_order_values("place_order", order_type=order_type, price=0.0)

    @pytest.mark.parametrize("order_type", sorted(NO_FIXED_LIMIT_TYPES))
    def test_other_types_may_carry_a_zero_price(self, order_type):
        validate_order_values("place_order", order_type=order_type, price=0.0)

    def test_trailing_stop_limit_accepts_a_zero_price(self):
        """Called out on its own because its name reads like a limit type."""
        validate_order_values(
            "place_order", order_type="TRAILING_STOP_LIMIT", price=0.0
        )

    def test_order_type_is_matched_case_insensitively(self):
        with pytest.raises(ValueError, match="greater than 0"):
            validate_order_values("place_order", order_type="normal", price=0.0)


class TestComboPrice:
    def test_a_negative_net_price_is_accepted_unchanged(self):
        """moomoo documents no debit/credit convention, so the sign passes through."""
        validate_order_values(
            "place_combo_order", order_type="NORMAL", combo_price=-2.50
        )

    def test_a_zero_net_price_is_accepted(self):
        validate_order_values("place_combo_order", order_type="NORMAL", combo_price=0.0)

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_a_non_finite_net_price_is_refused(self, value):
        with pytest.raises(ValueError, match="finite number"):
            validate_order_values(
                "place_combo_order", order_type="NORMAL", combo_price=value
            )

    def test_a_non_numeric_net_price_is_refused(self):
        with pytest.raises(ValueError, match="must be a number"):
            validate_order_values(
                "place_combo_order", order_type="NORMAL", combo_price="2.50"
            )


class TestRequiredFields:
    """The fields the gateway itself demands, absorbed from place_order."""

    @pytest.mark.parametrize(
        "order_type",
        ["STOP", "STOP_LIMIT", "MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"],
    )
    def test_stop_types_require_a_trigger_price(self, order_type):
        with pytest.raises(ValueError, match="aux_price is required"):
            validate_required_order_fields("place_order", order_type=order_type)

    @pytest.mark.parametrize("order_type", ["TRAILING_STOP", "TRAILING_STOP_LIMIT"])
    def test_trailing_types_require_trail_parameters(self, order_type):
        with pytest.raises(ValueError, match="trail_type and trail_value"):
            validate_required_order_fields(
                "place_order", order_type=order_type, trail_value=1.0
            )

    def test_a_complete_trailing_order_passes(self):
        validate_required_order_fields(
            "place_order",
            order_type="TRAILING_STOP",
            trail_type="RATIO",
            trail_value=1.0,
        )

    def test_a_plain_limit_order_needs_neither(self):
        validate_required_order_fields("place_order", order_type="NORMAL")
