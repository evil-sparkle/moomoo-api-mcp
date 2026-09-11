"""Combo order preview: account impact without a trading write (R4)."""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
)

STRATEGY_LEG_ID = 3333333333333333333
OTHER_LEG_ID = 4444444444444444444

IMPACT_COLUMNS = [
    "nlv_change",
    "initial_margin_change",
    "maintenance_margin_change",
    "option_bp",
    "max_withdraw_change",
    "bp_decrease",
]

# Every method that must never be reached during a preview.
WRITE_METHODS = ("place_order", "place_combo_order", "modify_order", "unlock_trade")


def _impact_frame(**overrides):
    values = {
        "nlv_change": -12.5,
        "initial_margin_change": 250.0,
        "maintenance_margin_change": 200.0,
        "option_bp": 15000.0,
        "max_withdraw_change": -250.0,
        "bp_decrease": 250.0,
    }
    values.update(overrides)
    return pd.DataFrame([values], columns=IMPACT_COLUMNS)


def _opening_legs():
    return [
        {"code": "US.XYZ260101C100000", "trd_side": "BUY", "qty_ratio": 1},
        {"code": "US.XYZ260101C105000", "trd_side": "SELL", "qty_ratio": 1},
    ]


def _closing_legs():
    return [
        {
            "code": "US.XYZ260101C100000",
            "trd_side": "SELL",
            "qty_ratio": 1,
            "position_id": str(STRATEGY_LEG_ID),
        },
        {
            "code": "US.XYZ260101C105000",
            "trd_side": "BUY",
            "qty_ratio": 1,
            "position_id": str(OTHER_LEG_ID),
        },
    ]


@pytest.fixture
def ctx():
    context = MagicMock()
    context.comboorder_tradinginfo_query.return_value = (0, _impact_frame())
    return context


@pytest.fixture
def service(ctx):
    """A read-only service: preview must work without any write permission."""
    svc = TradeService()
    svc.trade_ctx = ctx
    return svc


def assert_no_writes(ctx):
    """Preview must never touch a mutating gateway method."""
    for method in WRITE_METHODS:
        getattr(ctx, method).assert_not_called()


class TestValidPreview:
    """A well-formed package reaches the query and returns its impact."""

    def test_returns_all_impact_fields_with_a_timestamp(self, service, ctx):
        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, trd_env="SIMULATE", acc_id=456
        )

        assert preview["checked_at"].endswith("Z")
        assert preview["acc_id"] == 456
        assert preview["trd_env"] == "SIMULATE"
        assert preview["nlv_change"] == -12.5
        assert preview["initial_margin_change"] == 250.0
        assert preview["maintenance_margin_change"] == 200.0
        assert preview["option_bp"] == 15000.0
        assert preview["max_withdraw_change"] == -250.0
        assert preview["bp_decrease"] == 250.0
        assert_no_writes(ctx)

    def test_forwards_the_package_to_the_sdk(self, service, ctx):
        service.preview_combo_order(
            combo_legs=_opening_legs(),
            price=2.5,
            qty=3,
            order_type="NORMAL",
            trd_env="SIMULATE",
            acc_id=456,
        )

        kwargs = ctx.comboorder_tradinginfo_query.call_args.kwargs
        assert kwargs["price"] == 2.5
        assert kwargs["qty"] == 3
        assert kwargs["order_type"] == "NORMAL"
        assert kwargs["trd_env"] == "SIMULATE"
        assert kwargs["acc_id"] == 456
        legs = kwargs["combo_leg_list"]
        assert [leg.code for leg in legs] == [
            "US.XYZ260101C100000",
            "US.XYZ260101C105000",
        ]
        assert [leg.trd_side for leg in legs] == ["BUY", "SELL"]
        assert [leg.qty_ratio for leg in legs] == [1, 1]

    def test_price_sign_is_forwarded_untouched(self, service, ctx):
        """No debit/credit convention is invented on the caller's behalf."""
        service.preview_combo_order(
            combo_legs=_opening_legs(), price=-1.75, qty=1, acc_id=456
        )

        assert ctx.comboorder_tradinginfo_query.call_args.kwargs["price"] == -1.75

    def test_closing_position_ids_reach_the_sdk_exactly(self, service, ctx):
        """Decimal-string ids from get_positions must arrive as exact integers."""
        service.preview_combo_order(
            combo_legs=_closing_legs(), price=1.0, qty=1, acc_id=456
        )

        legs = ctx.comboorder_tradinginfo_query.call_args.kwargs["combo_leg_list"]
        assert [leg.position_id for leg in legs] == [STRATEGY_LEG_ID, OTHER_LEG_ID]
        assert_no_writes(ctx)

    def test_preview_is_permitted_under_read_only(self, service, ctx):
        assert service.policy.mode is TradingMode.READ_ONLY

        service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, trd_env="REAL", acc_id=456
        )

        ctx.comboorder_tradinginfo_query.assert_called_once()
        assert_no_writes(ctx)

    @pytest.mark.parametrize("mode", list(TradingMode))
    def test_preview_works_in_every_mode(self, ctx, mode):
        svc = TradeService(policy=TradingPolicy(mode))
        svc.trade_ctx = ctx

        svc.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        ctx.comboorder_tradinginfo_query.assert_called_once()


class TestAccountSelection:
    """Preview resolves an omitted account the same way placement does."""

    def test_omitted_account_is_resolved_from_the_leg_market(self, service, ctx):
        ctx.get_acc_list.return_value = (
            0,
            pd.DataFrame(
                [{"acc_id": 789, "trd_env": "SIMULATE", "market_auth": ["US"]}]
            ),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, trd_env="SIMULATE"
        )

        assert ctx.comboorder_tradinginfo_query.call_args.kwargs["acc_id"] == 789
        assert preview["acc_id"] == 789

    def test_matches_the_account_placement_would_choose(self, ctx):
        acc_frame = pd.DataFrame(
            [{"acc_id": 789, "trd_env": "SIMULATE", "market_auth": ["US"]}]
        )
        ctx.get_acc_list.return_value = (0, acc_frame)
        ctx.place_combo_order.return_value = (
            0,
            pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}]),
        )
        svc = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
        svc.trade_ctx = ctx

        svc.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, trd_env="SIMULATE"
        )
        svc.place_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, trd_env="SIMULATE"
        )

        previewed = ctx.comboorder_tradinginfo_query.call_args.kwargs["acc_id"]
        placed = ctx.place_combo_order.call_args.kwargs["acc_id"]
        assert previewed == placed == 789

    def test_string_account_id_is_accepted(self, service, ctx):
        service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id="9007199254740993"
        )

        assert (
            ctx.comboorder_tradinginfo_query.call_args.kwargs["acc_id"]
            == 9007199254740993
        )


class TestValidationFailures:
    """A malformed package never reaches the gateway."""

    @pytest.mark.parametrize(
        "legs,message",
        [
            ([{"code": "US.A", "trd_side": "BUY", "qty_ratio": 1}], "at least two"),
            (
                [
                    {"code": "US.A", "trd_side": "BUY"},
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                "missing 'qty_ratio'",
            ),
            (
                [
                    {"code": "US.A", "trd_side": "HOLD", "qty_ratio": 1},
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                "Invalid trd_side",
            ),
            (
                [
                    {"code": "", "trd_side": "BUY", "qty_ratio": 1},
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                "missing a non-empty 'code'",
            ),
            (
                [
                    {"code": "US.A", "trd_side": "BUY", "qty_ratio": 1},
                    {"code": "HK.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                "same market",
            ),
            (
                [
                    {
                        "code": "US.A",
                        "trd_side": "BUY",
                        "qty_ratio": 1,
                        "position_id": 1.5,
                    },
                    {"code": "US.B", "trd_side": "SELL", "qty_ratio": 1},
                ],
                "non-integer 'position_id'",
            ),
        ],
    )
    def test_invalid_package_fails_before_the_gateway(
        self, service, ctx, legs, message
    ):
        with pytest.raises(ValueError, match=message):
            service.preview_combo_order(combo_legs=legs, price=2.5, qty=1, acc_id=456)

        ctx.comboorder_tradinginfo_query.assert_not_called()
        assert_no_writes(ctx)

    def test_missing_connection_is_reported(self):
        svc = TradeService()

        with pytest.raises(RuntimeError, match="Trade context not connected"):
            svc.preview_combo_order(
                combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
            )


class TestUnavailableValues:
    """The gateway's gaps and rejections are reported, not filled in."""

    def test_missing_impact_field_is_null_not_zero(self, service, ctx):
        ctx.comboorder_tradinginfo_query.return_value = (
            0,
            _impact_frame(option_bp=np.nan, bp_decrease=np.nan),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        assert preview["option_bp"] is None
        assert preview["bp_decrease"] is None
        assert preview["nlv_change"] == -12.5

    def test_empty_response_reports_every_field_as_null(self, service, ctx):
        ctx.comboorder_tradinginfo_query.return_value = (
            0,
            pd.DataFrame([], columns=IMPACT_COLUMNS),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        assert all(preview[field] is None for field in IMPACT_COLUMNS)
        assert preview["checked_at"].endswith("Z")

    def test_gateway_rejection_raises_without_a_fallback_write(self, service, ctx):
        ctx.comboorder_tradinginfo_query.return_value = (-1, "no option permission")

        with pytest.raises(RuntimeError, match="no option permission"):
            service.preview_combo_order(
                combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
            )

        assert_no_writes(ctx)

    def test_a_denied_placement_is_still_denied_after_a_preview(self, service, ctx):
        """Previewing does not grant permission to submit."""
        service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        with pytest.raises(TradingPolicyError):
            service.place_combo_order(
                combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
            )

        assert_no_writes(ctx)


class TestAgainstTheRealDecoder:
    """Missing-value handling checked against the SDK's own decoder.

    A hand-written fixture can only assert what its author guessed the gateway
    returns. These build a protobuf response with the impact fields unset and
    run it through ComboOrderTradingInfoQuery.unpack_rsp, so the sentinel under
    test is whatever the installed SDK actually produces.
    """

    @staticmethod
    def _decode_response_with_unset_impact_fields():
        from moomoo.common.pb import Trd_GetComboMaxTrdQtys_pb2 as pb
        from moomoo.trade.trade_query import ComboOrderTradingInfoQuery

        rsp = pb.Response()
        rsp.retType = 0
        rsp.s2c.header.trdEnv = 0
        rsp.s2c.header.accID = 123
        rsp.s2c.header.trdMarket = 1
        rsp.s2c.maxTrdQtys.SetInParent()  # present, but every field omitted

        ret, _, data = ComboOrderTradingInfoQuery.unpack_rsp(rsp)
        assert ret == 0
        return data

    def test_sdk_reports_missing_fields_as_a_string_not_nan(self):
        """Pin the real contract: the sentinel is 'N/A', not NaN."""
        decoded = self._decode_response_with_unset_impact_fields()

        assert decoded[0]["option_bp"] == "N/A"
        assert all(value == "N/A" for value in decoded[0].values())

    def test_preview_nulls_every_field_the_decoder_marked_missing(self, service, ctx):
        decoded = self._decode_response_with_unset_impact_fields()
        ctx.comboorder_tradinginfo_query.return_value = (
            0,
            pd.DataFrame(decoded, columns=IMPACT_COLUMNS),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        for field in IMPACT_COLUMNS:
            assert preview[field] is None, f"{field} leaked the SDK sentinel"

    def test_preview_keeps_supplied_values_and_nulls_only_the_gaps(self, service, ctx):
        decoded = self._decode_response_with_unset_impact_fields()
        decoded[0]["nlv_change"] = -12.5
        ctx.comboorder_tradinginfo_query.return_value = (
            0,
            pd.DataFrame(decoded, columns=IMPACT_COLUMNS),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        assert preview["nlv_change"] == -12.5
        assert preview["option_bp"] is None

    def test_zero_is_never_substituted_for_a_missing_value(self, service, ctx):
        """Null means 'not reported'; 0.0 would mean 'no impact'."""
        decoded = self._decode_response_with_unset_impact_fields()
        ctx.comboorder_tradinginfo_query.return_value = (
            0,
            pd.DataFrame(decoded, columns=IMPACT_COLUMNS),
        )

        preview = service.preview_combo_order(
            combo_legs=_opening_legs(), price=2.5, qty=1, acc_id=456
        )

        assert not any(preview[field] == 0 for field in IMPACT_COLUMNS)
