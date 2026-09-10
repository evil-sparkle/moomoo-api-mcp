"""Trading-mode enforcement across the full mode/environment matrix (R3)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    ENV_VAR,
    TradingMode,
    TradingModeConfigError,
    TradingPolicy,
    TradingPolicyError,
)

ALL_MODES = list(TradingMode)
ENVIRONMENTS = ["REAL", "SIMULATE"]

# Which (mode, trd_env) pairs may write. Everything else must be refused.
ALLOWED_WRITES = {
    (TradingMode.SIMULATE, "SIMULATE"),
    (TradingMode.REAL, "SIMULATE"),
    (TradingMode.REAL, "REAL"),
}


def _service(mode: TradingMode, ctx: MagicMock) -> TradeService:
    service = TradeService(policy=TradingPolicy(mode))
    service.trade_ctx = ctx
    return service


@pytest.fixture
def ctx() -> MagicMock:
    """A trade context that would succeed at everything, if it were reached."""
    ok_frame = pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}])
    context = MagicMock()
    context.place_order.return_value = (0, ok_frame)
    context.place_combo_order.return_value = (0, ok_frame)
    context.modify_order.return_value = (0, ok_frame)
    context.unlock_trade.return_value = (0, None)
    context.get_acc_list.return_value = (
        0,
        pd.DataFrame([{"acc_id": 456, "trd_env": "REAL", "market_auth": ["US"]}]),
    )
    return context


def _combo_legs():
    return [
        {"code": "US.XYZ260101C100000", "trd_side": "BUY", "qty_ratio": 1},
        {"code": "US.XYZ260101C105000", "trd_side": "SELL", "qty_ratio": 1},
    ]


def _invoke(service: TradeService, operation: str, trd_env: str):
    """Call one guarded write on ``service``."""
    if operation == "place_order":
        return service.place_order(
            code="US.AAPL",
            price=100.0,
            qty=1,
            trd_side="BUY",
            trd_env=trd_env,
            acc_id=456,
        )
    if operation == "place_combo_order":
        return service.place_combo_order(
            combo_legs=_combo_legs(), price=2.5, qty=1, trd_env=trd_env, acc_id=456
        )
    if operation == "modify_order":
        return service.modify_order(
            order_id="1",
            modify_order_op="NORMAL",
            qty=2,
            price=1.0,
            trd_env=trd_env,
            acc_id=456,
        )
    if operation == "cancel_order":
        return service.cancel_order(order_id="1", trd_env=trd_env, acc_id=456)
    raise AssertionError(f"unknown operation {operation}")


WRITE_OPERATIONS = [
    "place_order",
    "place_combo_order",
    "modify_order",
    "cancel_order",
]


class TestConfiguration:
    """Parsing MOOMOO_TRADING_MODE."""

    def test_absent_mode_defaults_to_read_only(self):
        assert TradingPolicy.from_env({}).mode is TradingMode.READ_ONLY

    def test_empty_mode_defaults_to_read_only(self):
        assert TradingPolicy.from_env({ENV_VAR: "   "}).mode is TradingMode.READ_ONLY

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_each_documented_mode_parses(self, mode):
        assert TradingPolicy.from_env({ENV_VAR: mode.value}).mode is mode

    def test_mode_is_case_insensitive(self):
        assert TradingPolicy.from_env({ENV_VAR: "real"}).mode is TradingMode.REAL

    def test_unknown_mode_is_a_configuration_error(self):
        with pytest.raises(TradingModeConfigError, match="not a trading mode"):
            TradingPolicy.from_env({ENV_VAR: "YOLO"})

    def test_unknown_mode_does_not_fall_back_to_a_permissive_one(self):
        """A misconfiguration must fail loudly, never select a mode."""
        policy = "unset"
        with pytest.raises(TradingModeConfigError):
            policy = TradingPolicy.from_env({ENV_VAR: "REALLY"})

        assert policy == "unset"

    def test_direct_construction_defaults_to_read_only(self):
        assert TradeService().policy.mode is TradingMode.READ_ONLY


class TestWriteMatrix:
    """Every guarded write, in every mode, for every environment."""

    @pytest.mark.parametrize("operation", WRITE_OPERATIONS)
    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("trd_env", ENVIRONMENTS)
    def test_matrix(self, ctx, operation, mode, trd_env):
        service = _service(mode, ctx)

        if (mode, trd_env) in ALLOWED_WRITES:
            _invoke(service, operation, trd_env)
            assert ctx.method_calls, "an allowed write must reach the gateway"
            return

        with pytest.raises(TradingPolicyError) as excinfo:
            _invoke(service, operation, trd_env)

        # Denied writes make zero gateway calls — not even an account lookup.
        assert ctx.method_calls == []
        assert operation.split("_")[0] in str(excinfo.value)
        assert service.policy.mode is mode

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_denied_write_does_not_query_accounts(self, ctx, mode):
        """Account selection is a gateway call, so it must not happen either."""
        service = _service(mode, ctx)

        if (mode, "REAL") in ALLOWED_WRITES:
            pytest.skip("REAL writes are permitted in this mode")

        with pytest.raises(TradingPolicyError):
            service.place_order(
                code="US.AAPL",
                price=1.0,
                qty=1,
                trd_side="BUY",
                trd_env="REAL",
                acc_id="0",  # would normally trigger _find_best_account
            )

        ctx.get_acc_list.assert_not_called()

    def test_denied_write_is_not_rerouted_to_another_environment(self, ctx):
        service = _service(TradingMode.SIMULATE, ctx)

        with pytest.raises(TradingPolicyError):
            _invoke(service, "place_order", "REAL")

        ctx.place_order.assert_not_called()

    def test_unrecognized_environment_is_refused(self, ctx):
        service = _service(TradingMode.REAL, ctx)

        with pytest.raises(TradingPolicyError, match="not a recognized"):
            _invoke(service, "place_order", "PAPER")

        ctx.place_order.assert_not_called()

    def test_cancellation_is_denied_in_read_only(self, ctx):
        service = _service(TradingMode.READ_ONLY, ctx)

        with pytest.raises(TradingPolicyError, match="cancel"):
            service.cancel_order(order_id="1", trd_env="SIMULATE", acc_id=456)

        ctx.modify_order.assert_not_called()


class TestUnlockMatrix:
    """Only REAL mode may unlock trading."""

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_unlock_permission_follows_mode(self, ctx, mode):
        service = _service(mode, ctx)

        if mode is TradingMode.REAL:
            service.unlock_trade(password="pw")
            ctx.unlock_trade.assert_called_once()
            return

        with pytest.raises(TradingPolicyError, match="only REAL mode may unlock"):
            service.unlock_trade(password="pw")
        ctx.unlock_trade.assert_not_called()

    def test_failed_unlock_in_real_mode_leaves_policy_unchanged(self, ctx):
        ctx.unlock_trade.return_value = (-1, "wrong password")
        service = _service(TradingMode.REAL, ctx)

        with pytest.raises(RuntimeError, match="unlock_trade failed"):
            service.unlock_trade(password="pw")

        assert service.policy.mode is TradingMode.REAL
        ctx.place_order.assert_not_called()


class TestReadsRemainAvailable:
    """Reads and previews are permitted in every mode."""

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_account_reads_allowed_in_every_mode(self, ctx, mode):
        service = _service(mode, ctx)

        accounts = service.get_accounts()

        assert accounts[0]["acc_id"] == 456

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_position_reads_allowed_in_every_mode(self, ctx, mode):
        ctx.position_list_query.return_value = (
            0,
            pd.DataFrame([{"code": "US.AAPL", "qty": 1}]),
        )
        service = _service(mode, ctx)

        assert service.get_positions(trd_env="REAL", acc_id=456)[0]["qty"] == 1


class TestPolicyErrorMessages:
    """Operators must be able to act on a refusal."""

    def test_read_only_message_names_the_variable_and_covers_cancellation(self):
        policy = TradingPolicy(TradingMode.READ_ONLY)

        with pytest.raises(TradingPolicyError) as excinfo:
            policy.check_write("cancel_order", "SIMULATE")

        message = str(excinfo.value)
        assert ENV_VAR in message
        assert "cancelling" in message

    def test_unlock_message_states_a_password_does_not_help(self):
        policy = TradingPolicy(TradingMode.SIMULATE)

        with pytest.raises(TradingPolicyError) as excinfo:
            policy.check_unlock()

        assert "password does not change the mode" in str(excinfo.value)
