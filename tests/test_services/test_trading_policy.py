"""Trading-mode enforcement across the full mode/environment matrix (R3)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.order_errors import OrderNotSentError
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    ENV_VAR,
    InstrumentFacts,
    LegFacts,
    OrderFacts,
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


def _policy(mode: TradingMode, **kwargs) -> TradingPolicy:
    """A policy in ``mode``, with the REAL allowlist the fixture account needs."""
    kwargs.setdefault(
        "real_acc_ids", frozenset({456}) if mode is TradingMode.REAL else frozenset()
    )
    return TradingPolicy(mode, **kwargs)


def _service(mode: TradingMode, ctx: MagicMock, **kwargs) -> TradeService:
    service = TradeService(policy=_policy(mode, **kwargs))
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
    # A NORMAL modification is assessed as the order that would result, so it
    # reads the existing order first.
    context.order_list_query.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "order_id": "1",
                    "code": "US.AAPL",
                    "trd_side": "BUY",
                    "order_type": "NORMAL",
                    "qty": 10,
                    "price": 50.0,
                    "aux_price": 0.0,
                }
            ]
        ),
    )
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
        # REAL also needs its account allowlist; the other modes ignore it.
        env = {ENV_VAR: mode.value, "MOOMOO_REAL_ACC_IDS": "456"}
        assert TradingPolicy.from_env(env).mode is mode

    def test_mode_is_case_insensitive(self):
        env = {ENV_VAR: "real", "MOOMOO_REAL_ACC_IDS": "456"}
        assert TradingPolicy.from_env(env).mode is TradingMode.REAL

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

        with pytest.raises(OrderNotSentError) as excinfo:
            _invoke(service, operation, trd_env)

        # Denied writes make zero gateway calls — not even an account lookup.
        assert ctx.method_calls == []
        assert operation.split("_")[0] in str(excinfo.value)
        assert "no order was sent" in str(excinfo.value)
        # The refusal keeps the policy error that caused it, so a caller can
        # still branch on why as well as on what.
        assert isinstance(excinfo.value.__cause__, TradingPolicyError)
        assert service.policy.mode is mode

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_denied_write_does_not_query_accounts(self, ctx, mode):
        """Account selection is a gateway call, so it must not happen either."""
        service = _service(mode, ctx)

        if (mode, "REAL") in ALLOWED_WRITES:
            pytest.skip("REAL writes are permitted in this mode")

        with pytest.raises(OrderNotSentError):
            service.place_order(
                code="US.AAPL",
                price=1.0,
                qty=1,
                trd_side="BUY",
                trd_env="REAL",
                acc_id="0",  # would normally trigger account resolution
            )

        ctx.get_acc_list.assert_not_called()

    def test_denied_write_is_not_rerouted_to_another_environment(self, ctx):
        service = _service(TradingMode.SIMULATE, ctx)

        with pytest.raises(OrderNotSentError):
            _invoke(service, "place_order", "REAL")

        ctx.place_order.assert_not_called()

    def test_unrecognized_environment_is_refused(self, ctx):
        service = _service(TradingMode.REAL, ctx)

        with pytest.raises(OrderNotSentError, match="not a recognized"):
            _invoke(service, "place_order", "PAPER")

        ctx.place_order.assert_not_called()

    def test_cancellation_is_denied_in_read_only(self, ctx):
        service = _service(TradingMode.READ_ONLY, ctx)

        with pytest.raises(OrderNotSentError, match="cancel"):
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


# --- fixtures for the limit assessment --------------------------------------
#
# These build InstrumentFacts directly. The policy is pure over already
# normalized numbers, so a test that wants a missing bid says None, and the
# question of how a gateway spells "missing" belongs to the adapter's tests.

# M is 150: the largest of the three quoted prices. The reference-price rows
# below are written against that number.
STOCK = InstrumentFacts(
    code="US.AAPL",
    classification="STOCK",
    monetary_multiplier=1.0,
    last_price=150.0,
    bid_price=148.0,
    ask_price=149.0,
)


def _option(code: str = "US.XYZ260101C100000", **overrides) -> InstrumentFacts:
    facts = {
        "classification": "DRVT",
        "monetary_multiplier": 100.0,
        "contract_size": 100.0,
        "last_price": 3.00,
        "bid_price": 2.95,
        "ask_price": 3.05,
    }
    facts.update(overrides)
    return InstrumentFacts(code=code, **facts)


def _order(instrument: InstrumentFacts = STOCK, **overrides) -> OrderFacts:
    facts = {
        "order_type": "NORMAL",
        "trd_side": "BUY",
        "qty": 1,
        "legs": [LegFacts(instrument=instrument)],
        "price": 100.0,
    }
    facts.update(overrides)
    return OrderFacts(**facts)


class TestLimitConfiguration:
    """Parsing and validating the configured limits."""

    def test_from_env_parses_valid_limits(self):
        policy = TradingPolicy.from_env(
            {
                "MOOMOO_TRADING_MODE": "REAL",
                "MOOMOO_REAL_ACC_IDS": "456",
                "MOOMOO_MAX_ORDER_QTY": "500",
                "MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": "USD:25000.50,HKD:200000",
            }
        )
        assert policy.mode is TradingMode.REAL
        assert policy.max_order_qty == 500.0
        assert policy.max_order_notional == {"USD": 25000.50, "HKD": 200000.0}

    def test_from_env_rejects_non_numeric_qty(self):
        with pytest.raises(TradingModeConfigError, match="MOOMOO_MAX_ORDER_QTY"):
            TradingPolicy.from_env({"MOOMOO_MAX_ORDER_QTY": "invalid"})

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-100"])
    def test_from_env_rejects_unusable_qty_limits(self, value):
        """nan is the reason this validation exists at all.

        Every comparison against nan is false, so an unvalidated nan does not
        raise anywhere — it silently switches the limit off, which is the exact
        opposite of what configuring a limit means.
        """
        with pytest.raises(TradingModeConfigError, match="MOOMOO_MAX_ORDER_QTY"):
            TradingPolicy.from_env({"MOOMOO_MAX_ORDER_QTY": value})

    @pytest.mark.parametrize(
        "value",
        [
            "USD:nan",
            "USD:inf",
            "USD:0",
            "USD:-1",
            "25000",  # no currency
            "USD:1000,USD:2000",  # duplicate
            "US:1000",  # not three letters
            "USDD:1000",
            "US1:1000",  # not alphabetic
            "USD:abc",
        ],
    )
    def test_from_env_rejects_malformed_caps(self, value):
        with pytest.raises(
            TradingModeConfigError, match="MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY"
        ):
            TradingPolicy.from_env({"MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": value})

    def test_currency_codes_are_upper_cased(self):
        policy = TradingPolicy.from_env(
            {"MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": "usd:1000"}
        )
        assert policy.max_order_notional == {"USD": 1000.0}

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_order_qty": float("nan")},
            {"max_order_qty": float("inf")},
            {"max_order_qty": 0},
            {"max_order_notional": {"USD": float("nan")}},
            {"max_order_notional": {"USD": -1.0}},
        ],
    )
    def test_direct_construction_is_validated_too(self, kwargs):
        """A policy built in code gets the same check as one built from env."""
        with pytest.raises(TradingModeConfigError):
            TradingPolicy(TradingMode.REAL, **kwargs)

    def test_legacy_variable_alongside_the_new_one_is_ignored(self, caplog):
        with caplog.at_level("INFO"):
            policy = TradingPolicy.from_env(
                {
                    "MOOMOO_MAX_ORDER_NOTIONAL": "25000",
                    "MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY": "USD:25000",
                }
            )
        assert policy.max_order_notional == {"USD": 25000.0}
        assert "MOOMOO_MAX_ORDER_NOTIONAL is set and ignored" in caplog.text

    def test_legacy_variable_alone_is_a_startup_error(self):
        with pytest.raises(TradingModeConfigError) as excinfo:
            TradingPolicy.from_env({"MOOMOO_MAX_ORDER_NOTIONAL": "25000"})

        message = str(excinfo.value)
        assert "MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY" in message
        assert "without a currency" in message

    def test_legacy_variable_is_never_parsed_as_a_limit(self):
        """Even a value that would parse fine is not read as a cap."""
        with pytest.raises(TradingModeConfigError):
            TradingPolicy.from_env({"MOOMOO_MAX_ORDER_NOTIONAL": "25000"})

    def test_no_cap_configured_means_no_assessment(self):
        policy = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({456}))
        assert policy.notional_cap_configured is False
        # An instrument with no facts at all would be unassessable, and is
        # permitted, because nothing asked for a valuation.
        policy.assess_order(
            "place_order", _order(InstrumentFacts(code="XX.UNKNOWN"), qty=10_000)
        )


class TestCapMappingIsImmutable:
    """A validated cap cannot be edited back into an unvalidated one.

    `frozen=True` stops the field being reassigned, not the dict behind it being
    mutated. Without a defensive copy a caller could insert `nan` after
    validation and reintroduce the comparison bypass the validation exists to
    prevent -- every comparison against `nan` is false, so the cap would
    silently stop applying.
    """

    def test_the_mapping_cannot_be_mutated_after_validation(self):
        caps = {"USD": 1000.0}
        policy = TradingPolicy(TradingMode.REAL, max_order_notional=caps)

        with pytest.raises(TypeError):
            policy.max_order_notional["USD"] = float("nan")  # type: ignore[index]

    def test_mutating_the_original_dict_does_not_reach_the_policy(self):
        caps = {"USD": 1000.0}
        policy = TradingPolicy(TradingMode.REAL, max_order_notional=caps)

        caps["USD"] = float("nan")
        caps["EUR"] = float("inf")

        assert policy.max_order_notional == {"USD": 1000.0}

    def test_a_cap_that_survived_still_refuses_an_over_limit_order(self):
        """The point of the copy: the guardrail keeps working."""
        caps = {"USD": 1000.0}
        policy = TradingPolicy(
            TradingMode.REAL, max_order_notional=caps, real_acc_ids=frozenset({456})
        )
        caps["USD"] = float("nan")

        with pytest.raises(TradingPolicyError, match="exceeds"):
            policy.assess_order("place_order", _order(qty=11, price=100.0))

    @pytest.mark.parametrize("currency", ["US", "USDD", "US1", "", "  "])
    def test_direct_construction_validates_the_currency_key(self, currency):
        """from_env checked these; direct construction did not."""
        with pytest.raises(TradingModeConfigError, match="three-letter currency code"):
            TradingPolicy(TradingMode.REAL, max_order_notional={currency: 1000.0})

    def test_direct_construction_upper_cases_the_currency_key(self):
        policy = TradingPolicy(TradingMode.REAL, max_order_notional={"usd": 1000.0})

        assert policy.max_order_notional == {"USD": 1000.0}

    def test_direct_construction_rejects_a_duplicate_after_normalization(self):
        with pytest.raises(TradingModeConfigError, match="more than once"):
            TradingPolicy(
                TradingMode.REAL,
                max_order_notional={"usd": 1000.0, "USD": 2000.0},
            )


class TestRealAccountAllowlist:
    """MOOMOO_REAL_ACC_IDS is required in REAL mode and ignored elsewhere."""

    @pytest.mark.parametrize("value", [None, "", "   ", ","])
    def test_real_mode_requires_the_allowlist(self, value):
        env = {"MOOMOO_TRADING_MODE": "REAL"}
        if value is not None:
            env["MOOMOO_REAL_ACC_IDS"] = value

        with pytest.raises(TradingModeConfigError, match="MOOMOO_REAL_ACC_IDS"):
            TradingPolicy.from_env(env)

    @pytest.mark.parametrize("value", ["abc", "123,abc", "12.5", "-1"])
    def test_malformed_allowlist_is_rejected(self, value):
        with pytest.raises(TradingModeConfigError, match="MOOMOO_REAL_ACC_IDS"):
            TradingPolicy.from_env(
                {"MOOMOO_TRADING_MODE": "REAL", "MOOMOO_REAL_ACC_IDS": value}
            )

    def test_allowlist_parses_to_identifiers(self):
        policy = TradingPolicy.from_env(
            {"MOOMOO_TRADING_MODE": "REAL", "MOOMOO_REAL_ACC_IDS": " 123 , 456 "}
        )
        assert policy.real_acc_ids == frozenset({123, 456})

    def test_simulate_needs_no_allowlist(self):
        policy = TradingPolicy.from_env({"MOOMOO_TRADING_MODE": "SIMULATE"})
        assert policy.real_acc_ids == frozenset()

    def test_allowlist_outside_real_mode_is_ignored(self):
        policy = TradingPolicy.from_env(
            {"MOOMOO_TRADING_MODE": "SIMULATE", "MOOMOO_REAL_ACC_IDS": "456"}
        )
        assert policy.real_acc_ids == frozenset()


class TestQuantityLimit:
    """The quantity cap, including what it measures on a combo."""

    def test_quantity_at_the_limit_is_permitted(self):
        policy = TradingPolicy(TradingMode.REAL, max_order_qty=100)
        policy.assess_order("place_order", _order(qty=100))

    def test_quantity_over_the_limit_is_refused(self):
        policy = TradingPolicy(TradingMode.REAL, max_order_qty=100)
        with pytest.raises(TradingPolicyError, match="order quantity 101 exceeds"):
            policy.assess_order("place_order", _order(qty=101))

    def test_combo_measures_the_largest_leg_not_the_package_count(self):
        """A 1:2:1 butterfly for 3 packages puts 6 contracts on its middle leg.

        Measuring the package count would read that as 3 and let it through.
        """
        policy = TradingPolicy(TradingMode.REAL, max_order_qty=5)
        butterfly = OrderFacts(
            order_type="NORMAL",
            trd_side="",
            qty=3,
            is_combo=True,
            price=-1.0,
            legs=[
                LegFacts(instrument=_option("US.A"), qty_ratio=1),
                LegFacts(instrument=_option("US.B"), qty_ratio=2),
                LegFacts(instrument=_option("US.C"), qty_ratio=1),
            ],
        )

        with pytest.raises(TradingPolicyError, match="largest leg quantity 6 exceeds"):
            policy.assess_order("place_combo_order", butterfly)


class TestReferencePrice:
    """The reference-price table, row by row."""

    @pytest.fixture
    def policy(self) -> TradingPolicy:
        return TradingPolicy(
            TradingMode.REAL,
            max_order_notional={"USD": 1000.0},
            real_acc_ids=frozenset({456}),
        )

    def test_buy_limit_below_the_market_uses_its_own_price(self, policy):
        """10 x 90 = 900, permitted, even though M is 150.

        A BUY limit bounds the fill from above: the order cannot cost more than
        it asks to, so judging it at the market would overstate it.
        """
        policy.assess_order("place_order", _order(qty=10, price=90.0))

    def test_sell_limit_below_the_market_uses_the_market(self, policy):
        """10 x max(90, 150) = 1,500, refused.

        A SELL limit bounds the fill from below, so the market supplies the
        upper side.
        """
        with pytest.raises(TradingPolicyError, match="1,500.00 USD"):
            policy.assess_order(
                "place_order", _order(qty=10, price=90.0, trd_side="SELL")
            )

    def test_market_order_at_price_zero_uses_the_market(self, policy):
        """The old check skipped the notional entirely when price was 0."""
        with pytest.raises(TradingPolicyError, match="1,500.00 USD"):
            policy.assess_order(
                "place_order",
                _order(qty=10, price=0.0, order_type="MARKET"),
            )

    def test_stop_trigger_above_the_market_is_used(self, policy):
        """8 x max(120, 130) = 1,040, refused."""
        with pytest.raises(TradingPolicyError, match="1,040.00 USD"):
            policy.assess_order(
                "place_order",
                _order(
                    InstrumentFacts(
                        code="US.AAPL",
                        classification="STOCK",
                        monetary_multiplier=1.0,
                        last_price=120.0,
                    ),
                    qty=8,
                    price=0.0,
                    aux_price=130.0,
                    order_type="STOP",
                ),
            )

    def test_market_reference_is_the_largest_quoted_price(self):
        facts = InstrumentFacts(
            code="US.AAPL", last_price=100.0, bid_price=99.0, ask_price=101.0
        )
        assert facts.market_reference() == 101.0

    def test_non_numeric_quotes_leave_the_remaining_price_usable(self, policy):
        """Bid and ask absent, last present: M is the last price, and no crash."""
        facts = InstrumentFacts(
            code="US.AAPL",
            classification="STOCK",
            monetary_multiplier=1.0,
            last_price=150.0,
            bid_price=None,
            ask_price=None,
        )
        assert facts.market_reference() == 150.0
        with pytest.raises(TradingPolicyError, match="1,500.00 USD"):
            policy.assess_order(
                "place_order", _order(facts, qty=10, price=90.0, trd_side="SELL")
            )

    def test_sell_limit_with_no_market_reference_is_refused(self, policy):
        """Fail closed: an illiquid option quoting nothing is the common case."""
        facts = InstrumentFacts(
            code="US.AAPL", classification="STOCK", monetary_multiplier=1.0
        )
        assert facts.market_reference() is None

        with pytest.raises(TradingPolicyError, match="no usable market price"):
            policy.assess_order(
                "place_order", _order(facts, qty=1, price=1.0, trd_side="SELL")
            )

    def test_unclassified_order_type_is_refused(self, policy):
        """TWAP and friends are query-only, so nothing knows how to value them."""
        with pytest.raises(TradingPolicyError, match="not classified"):
            policy.assess_order(
                "place_order", _order(qty=1, price=1.0, order_type="TWAP")
            )


class TestNotionalAssessment:
    """Valuing an order, and refusing when it cannot be valued."""

    @pytest.fixture
    def policy(self) -> TradingPolicy:
        return TradingPolicy(
            TradingMode.REAL,
            max_order_notional={"USD": 1000.0},
            real_acc_ids=frozenset({456}),
        )

    def test_option_notional_uses_the_monetary_multiplier(self, policy):
        """5 x 3.00 x 100 = 1,500 USD."""
        with pytest.raises(TradingPolicyError) as excinfo:
            policy.assess_order("place_order", _order(_option(), qty=5, price=3.00))

        message = str(excinfo.value)
        assert "1,500.00 USD" in message
        assert "1,000.00 USD" in message

    def test_the_multiplier_is_used_even_when_contract_size_differs(self, policy):
        """The case that catches the wrong field being reused.

        Contract size and monetary multiplier are distinct broker fields. A
        synthetic instrument where they differ is the only way to tell which one
        the valuation actually reached for.
        """
        synthetic = _option(contract_size=10.0, monetary_multiplier=100.0)

        with pytest.raises(TradingPolicyError, match="1,500.00 USD"):
            policy.assess_order("place_order", _order(synthetic, qty=5, price=3.00))

    def test_equity_multiplier_is_one(self, policy):
        with pytest.raises(TradingPolicyError, match="1,100.00 USD"):
            policy.assess_order("place_order", _order(qty=11, price=100.0))

    def test_missing_classification_is_refused(self, policy):
        with pytest.raises(TradingPolicyError, match="classification"):
            policy.assess_order(
                "place_order",
                _order(InstrumentFacts(code="US.AAPL", last_price=1.0), price=1.0),
            )

    def test_unsupported_classification_is_refused(self, policy):
        with pytest.raises(TradingPolicyError, match="does not value"):
            policy.assess_order(
                "place_order",
                _order(
                    InstrumentFacts(
                        code="US.X", classification="FUTURE", last_price=1.0
                    ),
                    price=1.0,
                ),
            )

    def test_option_without_a_verified_multiplier_is_refused(self, policy):
        with pytest.raises(TradingPolicyError, match="monetary multiplier"):
            policy.assess_order(
                "place_order",
                _order(_option(monetary_multiplier=None), price=1.0),
            )

    def test_unverified_market_is_refused_not_guessed(self, policy):
        """A market prefix is a venue, not a currency.

        HK dual-counter instruments quote in HKD or RMB on one venue, so valuing
        by prefix outside a verified subset would misprice exactly those while
        appearing to work.
        """
        with pytest.raises(TradingPolicyError, match="cannot establish the currency"):
            policy.assess_order(
                "place_order",
                _order(
                    InstrumentFacts(
                        code="HK.00700",
                        classification="STOCK",
                        monetary_multiplier=1.0,
                        last_price=1.0,
                    ),
                    price=1.0,
                ),
            )

    def test_currency_without_a_configured_cap_is_refused(self):
        """A cap in one currency does not mean no cap in the others."""
        policy = TradingPolicy(
            TradingMode.REAL,
            max_order_notional={"HKD": 200000.0},
            real_acc_ids=frozenset({456}),
        )

        with pytest.raises(TradingPolicyError, match="no configured cap"):
            policy.assess_order("place_order", _order(qty=1, price=1.0))

    def test_an_instrument_supplied_currency_wins_over_the_table(self, policy):
        """Forward compatibility: a real currency field would take precedence."""
        facts = InstrumentFacts(
            code="XX.SOMETHING",
            classification="STOCK",
            monetary_multiplier=1.0,
            last_price=150.0,
            currency="USD",
        )
        with pytest.raises(TradingPolicyError, match="1,500.00 USD"):
            policy.assess_order("place_order", _order(facts, qty=10, price=150.0))


class TestComboPremium:
    """The combo cap measures package premium, and says so."""

    @pytest.fixture
    def policy(self) -> TradingPolicy:
        return TradingPolicy(
            TradingMode.REAL,
            max_order_notional={"USD": 500.0},
            real_acc_ids=frozenset({456}),
        )

    def _combo(self, policy_price: float = -2.50, **overrides) -> OrderFacts:
        facts = {
            "order_type": "NORMAL",
            "trd_side": "",
            "qty": 3,
            "is_combo": True,
            "price": policy_price,
            "legs": [
                LegFacts(instrument=_option("US.A")),
                LegFacts(instrument=_option("US.B")),
            ],
        }
        facts.update(overrides)
        return OrderFacts(**facts)

    def test_premium_uses_the_absolute_net_price(self, policy):
        """|-2.50| x 3 x 100 = 750 USD. The sign is not itself grounds to refuse."""
        with pytest.raises(TradingPolicyError) as excinfo:
            policy.assess_order("place_combo_order", self._combo())

        message = str(excinfo.value)
        assert "750.00 USD" in message
        assert "package premium" in message

    def test_premium_is_named_premium_not_max_loss(self, policy):
        with pytest.raises(TradingPolicyError) as excinfo:
            policy.assess_order("place_combo_order", self._combo())

        assert "maximum loss" not in str(excinfo.value)

    def test_premium_uses_the_multiplier_not_the_contract_size(self, policy):
        legs = [
            LegFacts(instrument=_option("US.A", contract_size=10.0)),
            LegFacts(instrument=_option("US.B", contract_size=10.0)),
        ]
        with pytest.raises(TradingPolicyError, match="750.00 USD"):
            policy.assess_order("place_combo_order", self._combo(legs=legs))

    def test_a_no_fixed_limit_combo_has_no_computable_premium(self, policy):
        with pytest.raises(TradingPolicyError, match="no fixed net limit price"):
            policy.assess_order("place_combo_order", self._combo(order_type="MARKET"))

    def test_a_stock_leg_makes_the_premium_meaningless(self, policy):
        legs = [
            LegFacts(instrument=_option("US.A")),
            LegFacts(instrument=STOCK),
        ]
        with pytest.raises(TradingPolicyError, match="options only"):
            policy.assess_order("place_combo_order", self._combo(legs=legs))

    def test_differing_multipliers_are_refused(self, policy):
        legs = [
            LegFacts(instrument=_option("US.A", monetary_multiplier=100.0)),
            LegFacts(instrument=_option("US.B", monetary_multiplier=10.0)),
        ]
        with pytest.raises(TradingPolicyError, match="differing monetary multipliers"):
            policy.assess_order("place_combo_order", self._combo(legs=legs))

    def test_differing_contract_sizes_are_refused(self, policy):
        legs = [
            LegFacts(instrument=_option("US.A", contract_size=100.0)),
            LegFacts(instrument=_option("US.B", contract_size=10.0)),
        ]
        with pytest.raises(TradingPolicyError, match="differing contract sizes"):
            policy.assess_order("place_combo_order", self._combo(legs=legs))

    def test_a_permitted_premium_passes(self, policy):
        """|-1.00| x 3 x 100 = 300 USD, under the 500 cap."""
        policy.assess_order("place_combo_order", self._combo(policy_price=-1.00))
