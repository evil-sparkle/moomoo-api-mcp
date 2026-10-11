"""Durable approval and budget boundaries with actual independent SQLite users."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier

import pytest

from moomoo_mcp.services.trading_controls import (
    TradingControlError,
    TradingControls,
    canonical_intent,
    contract_view,
)
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    LegFacts,
    OrderFacts,
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
)
from tests.assertions import raises_message

ACCOUNT = "3333333333333333333"
CONTRACT = {"code": "US.AAPL", "qty": {"number": "2"}, "multiplier": {"number": "1"}}


class EpochClock:
    def __init__(self):
        self.now = datetime(2026, 10, 11, 23, 59, 30, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def request(operation="one", *, account=ACCOUNT, environment="REAL"):
    return {
        "code": "US.AAPL",
        "price": 10.0,
        "qty": 2,
        "trd_side": "BUY",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "adjust_limit": 0,
        "trd_env": environment,
        "acc_id": account,
        "operation_id": operation,
        "admission_epoch": "journal-epoch",
        "session": "N/A",
    }


@pytest.fixture
def controls(tmp_path):
    clock = EpochClock()
    store = TradingControls(tmp_path / "controls.sqlite3", create=True, clock=clock)
    yield store, clock
    store.close()


def approved(store, parameters, *, contract=CONTRACT, ttl_seconds=60):
    pending = store.prepare(
        "place_order", parameters, {"contract": contract}, ttl_seconds=ttl_seconds
    )
    return store.decide(
        pending["request_id"],
        "APPROVED",
        operator_id="configured-operator",
        reason="Reviewed the exact order and limits",
    )


def consume(
    store, parameters, *, amount="10", caps=None, approval=None, contract=CONTRACT
):
    store.consume(
        "place_order",
        parameters,
        approval_id=approval,
        require_approval=approval is not None,
        currency="USD",
        amount=Decimal(amount),
        caps={"USD": 100} if caps is None else caps,
        contract=contract,
    )


def test_canonical_exact_ids_numbers_and_transport_separation():
    first = request() | {"acc_id": "03333333333333333333", "qty": 2.0}
    second = request() | {"acc_id": int(ACCOUNT), "qty": Decimal("2.000")}
    assert canonical_intent("place_order", first) == canonical_intent(
        "place_order", second
    )
    parsed = json.loads(canonical_intent("place_order", first))
    assert parsed["parameters"]["acc_id"] == ACCOUNT
    assert parsed["parameters"]["qty"] == {"number": "2"}
    assert canonical_intent(
        "place_order", first | {"approval_id": "transport-one"}
    ) == (canonical_intent("place_order", first | {"approval_id": "transport-two"}))
    assert canonical_intent("place_order", first | {"qty": True}) != (
        canonical_intent("place_order", first | {"qty": 1})
    )
    precise = Decimal("12345678901234567890.12345678901234567890")
    body = json.loads(canonical_intent("place_order", request() | {"price": precise}))
    assert body["parameters"]["price"] == {
        "number": "12345678901234567890.1234567890123456789"
    }


@pytest.mark.parametrize("bad_id", [float(int(ACCOUNT)), True, "-1", "1.0", "１２３"])
def test_canonical_account_rejects_inexact_or_non_ascii_identifiers(bad_id):
    with raises_message(ValueError, "exact decimal"):
        canonical_intent("place_order", request(account=bad_id))


@pytest.mark.parametrize("bad_number", [float("nan"), float("inf"), Decimal("NaN")])
def test_canonical_refuses_nonfinite_numbers(bad_number):
    with raises_message(ValueError, "finite"):
        canonical_intent("place_order", request() | {"price": bad_number})


def test_canonical_session_compatibility_is_environment_specific():
    real = request()
    baseline = canonical_intent("place_order", real)
    for default in ("N/A", "NONE", "none", " n/a "):
        assert canonical_intent("place_order", real | {"session": default}) == baseline
    assert canonical_intent("place_order", real | {"session": "RTH"}) != baseline
    assert canonical_intent("place_order", real | {"session": "ETH"}) != baseline
    assert canonical_intent("place_order", real | {"session": "eth"}) == (
        canonical_intent("place_order", real | {"session": "ETH"})
    )
    with raises_message(ValueError, "session"):
        canonical_intent("place_order", real | {"session": None})
    paper = request(environment="SIMULATE")
    assert canonical_intent("place_order", paper | {"session": "RTH"}) == (
        canonical_intent("place_order", paper)
    )
    assert canonical_intent("place_order", paper | {"session": "ETH"}) != (
        canonical_intent("place_order", paper)
    )


def test_canonical_binds_every_execution_identity_component():
    original = request()
    baseline = canonical_intent("place_order", original)
    for change in (
        {"operation_id": "another"},
        {"admission_epoch": "another-epoch"},
        {"trd_env": "SIMULATE"},
        {"acc_id": "9007199254740993"},
        {"qty": 3},
        {"code": "US.TSLA"},
        {"price": 11},
    ):
        assert canonical_intent("place_order", original | change) != baseline
    assert canonical_intent("cancel_order", original) != baseline


def test_prepared_view_is_reviewable_and_copy_isolation_preserves_intent(controls):
    store, _ = controls
    parameters = request()
    review = {"contract": dict(CONTRACT), "note": "Operator evidence"}
    result = store.prepare("place_order", parameters, review)
    assert result["method"] == "place_order"
    assert "method" not in result["parameters"]
    assert result["parameters"] == parameters
    assert result["status"] == "PENDING"
    assert len(result["fingerprint"]) == 64
    parameters["qty"] = 999
    review["note"] = "changed caller buffer"
    result["parameters"]["price"] = 999
    persisted = store.get(result["request_id"])
    assert persisted["parameters"]["qty"] == 2
    assert persisted["parameters"]["price"] == 10
    assert persisted["review"]["note"] == "Operator evidence"
    assert persisted["fingerprint"] == result["fingerprint"]


@pytest.mark.parametrize("ttl", [0, 901, True])
def test_prepare_expiry_bounds(controls, ttl):
    store, _ = controls
    with raises_message(ValueError, "expiry"):
        store.prepare("place_order", request(), {}, ttl_seconds=ttl)


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED"])
def test_operator_decision_is_final_and_preserves_audit(controls, decision):
    store, clock = controls
    pending = store.prepare("place_order", request(), {})
    decision_view = store.decide(
        pending["request_id"],
        decision,
        operator_id="operator-a",
        reason="Manual review",
    )
    assert decision_view["operator_id"] == "operator-a"
    assert decision_view["reason"] == "Manual review"
    assert decision_view["decided_at"] == clock()
    for repeated in ("APPROVED", "REJECTED"):
        with raises_message(TradingControlError, "final"):
            store.decide(
                pending["request_id"],
                repeated,
                operator_id="operator-b",
                reason="Replaced",
            )
    persisted = store.get(pending["request_id"])
    assert persisted["status"] == decision
    assert persisted["operator_id"] == "operator-a"
    assert store.health() == {"state": "READY"}


def test_pending_rejected_missing_and_expired_requests_cannot_authorize(controls):
    store, clock = controls
    parameters = request()
    pending = store.prepare("place_order", parameters, {}, ttl_seconds=10)
    for identifier in (None, "unknown", pending["request_id"]):
        with pytest.raises(TradingControlError):
            store.check_approval("place_order", parameters, identifier)
    store.decide(
        pending["request_id"], "REJECTED", operator_id="operator", reason="Rejected"
    )
    with raises_message(TradingControlError, "rejected"):
        store.check_approval("place_order", parameters, pending["request_id"])
    expiring = approved(store, parameters, ttl_seconds=10)
    store.check_approval("place_order", parameters, expiring["request_id"])
    clock.advance(10)
    assert store.get(expiring["request_id"])["status"] == "EXPIRED"
    with raises_message(TradingControlError, "expired"):
        store.check_approval("place_order", parameters, expiring["request_id"])
    pending_expiry = store.prepare("place_order", request("later"), {}, ttl_seconds=1)
    clock.advance(1)
    with raises_message(TradingControlError, "expired"):
        store.decide(
            pending_expiry["request_id"],
            "APPROVED",
            operator_id="operator",
            reason="Too late",
        )


def test_changed_request_or_contract_never_consumes_approval_or_budget(controls):
    store, _ = controls
    parameters = request()
    decision = approved(store, parameters)
    with raises_message(TradingControlError, "exact request"):
        consume(store, parameters | {"qty": 3}, approval=decision["request_id"])
    with raises_message(TradingControlError, "contract changed"):
        consume(
            store,
            parameters,
            approval=decision["request_id"],
            contract=CONTRACT | {"code": "US.TSLA"},
        )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}
    assert store.get(decision["request_id"])["status"] == "APPROVED"
    consume(store, parameters, approval=decision["request_id"])
    assert store.get(decision["request_id"])["status"] == "CONSUMED"


def test_contract_ignores_quote_movement_but_binds_instrument_multiplier():
    instrument = InstrumentFacts(
        "US.AAPL", classification="STOCK", monetary_multiplier=1, last_price=10
    )
    facts = OrderFacts("NORMAL", "BUY", 2, [LegFacts(instrument)], price=10)
    moved_quote = replace(facts, legs=[LegFacts(replace(instrument, last_price=11))])
    changed_multiplier = replace(
        facts, legs=[LegFacts(replace(instrument, monetary_multiplier=100))]
    )
    assert contract_view(facts) == contract_view(moved_quote)
    assert contract_view(facts) != contract_view(changed_multiplier)
    assert contract_view(facts) != contract_view(replace(facts, qty=3))


def test_failed_budget_charge_preserves_approval_until_success(controls):
    store, _ = controls
    consume(store, request("first"), amount="90")
    parameters = request("second")
    decision = approved(store, parameters)
    with raises_message(TradingControlError, "budget exceeded"):
        consume(store, parameters, amount="20", approval=decision["request_id"])
    assert store.get(decision["request_id"])["status"] == "APPROVED"
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 90
    consume(store, parameters, amount="10", approval=decision["request_id"])
    assert store.get(decision["request_id"])["status"] == "CONSUMED"
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 100


def test_two_independent_store_connections_cannot_exceed_daily_cap(controls):
    first, clock = controls
    second = TradingControls(first.path, clock=clock)
    barrier = Barrier(2)

    def submit(store, operation):
        barrier.wait(timeout=3)
        try:
            consume(store, request(operation), amount="60")
            return "charged"
        except TradingControlError:
            return "refused"

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(
                workers.map(
                    lambda pair: submit(*pair), [(first, "one"), (second, "two")]
                )
            )
        assert sorted(results) == ["charged", "refused"]
        assert Decimal(first.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 60
        assert second.usage("REAL", ACCOUNT) == first.usage("REAL", ACCOUNT)
    finally:
        second.close()


def test_concurrent_consumption_of_one_approval_charges_only_once(controls):
    first, clock = controls
    second = TradingControls(first.path, clock=clock)
    parameters = request()
    decision = approved(first, parameters)
    barrier = Barrier(2)

    def submit(store):
        barrier.wait(timeout=3)
        try:
            consume(store, parameters, amount="60", approval=decision["request_id"])
            return "charged"
        except TradingControlError:
            return "refused"

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            assert sorted(workers.map(submit, [first, second])) == [
                "charged",
                "refused",
            ]
        assert Decimal(first.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 60
        assert second.get(decision["request_id"])["status"] == "CONSUMED"
    finally:
        second.close()


def test_restart_and_utc_rollover_preserve_old_charge_and_fresh_day_capacity(controls):
    first, clock = controls
    consume(first, request(), amount="90")
    first.close()
    reopened = TradingControls(first.path, clock=clock)
    try:
        assert reopened.usage("REAL", ACCOUNT)["day"] == "2026-10-11"
        assert Decimal(reopened.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 90
        with raises_message(TradingControlError, "budget exceeded"):
            consume(reopened, request("second"), amount="20")
        clock.advance(30)
        assert reopened.usage("REAL", ACCOUNT) == {
            "day": "2026-10-12",
            "timezone": "UTC",
            "amounts": {},
        }
        consume(reopened, request("next-day"), amount="100")
        assert Decimal(reopened.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 100
        with raises_message(TradingControlError, "already consumed"):
            consume(reopened, request(), amount="90")
        assert Decimal(reopened.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 100
    finally:
        reopened.close()


@pytest.mark.parametrize("currency", [None, "HKD"])
def test_missing_or_uncapped_currency_cannot_bypass_configured_budget(
    controls, currency
):
    store, _ = controls
    with raises_message(TradingControlError, "uncapped"):
        store.consume(
            "place_order",
            request(),
            approval_id=None,
            require_approval=False,
            currency=currency,
            amount=Decimal(10),
            caps={"USD": 100},
        )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}
    consume(store, request(), amount="10")  # Failed attempt did not consume identity.


def test_zero_value_approval_without_budget_does_not_invent_currency(controls):
    store, _ = controls
    parameters = request()
    decision = approved(store, parameters)
    store.consume(
        "place_order",
        parameters,
        approval_id=decision["request_id"],
        require_approval=True,
        currency=None,
        amount=Decimal(0),
        caps={},
        contract=CONTRACT,
    )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}
    assert store.get(decision["request_id"])["status"] == "CONSUMED"


def test_budget_decimal_arithmetic_has_no_float_slack(controls):
    store, _ = controls
    for number in range(3):
        consume(store, request(str(number)), amount="0.1", caps={"USD": 0.3})
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == Decimal("0.3")
    with raises_message(TradingControlError, "budget exceeded"):
        consume(
            store, request("fourth"), amount="0.00000000000000000001", caps={"USD": 0.3}
        )


def test_duplicate_and_conflicting_operation_never_charge_again(controls):
    store, _ = controls
    parameters = request()
    consume(store, parameters, amount="40")
    with raises_message(TradingControlError, "already consumed"):
        consume(store, parameters, amount="99")
    with raises_message(TradingControlError, "identity conflict"):
        consume(store, parameters | {"qty": 3}, amount="40")
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 40


def test_budget_isolates_accounts_and_environments(controls):
    store, _ = controls
    for account, environment in (
        (ACCOUNT, "REAL"),
        (ACCOUNT, "SIMULATE"),
        ("9007199254740993", "REAL"),
    ):
        consume(store, request(account=account, environment=environment), amount="100")
        assert Decimal(store.usage(environment, account)["amounts"]["USD"]) == 100


@pytest.mark.parametrize("amount", [Decimal(-1), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_budget_valuation_never_charges(controls, amount):
    store, _ = controls
    with raises_message(TradingControlError, "valuation"):
        store.consume(
            "place_order",
            request(),
            approval_id=None,
            require_approval=False,
            currency="USD",
            amount=amount,
            caps={"USD": 100},
        )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}


def test_confirm_reservation_rechecks_expiry_and_target_without_refunding(controls):
    store, clock = controls
    parameters = request()
    decision = approved(store, parameters, ttl_seconds=10)
    consume(store, parameters, amount="40", approval=decision["request_id"])
    arguments = {
        "approval_id": decision["request_id"],
        "require_approval": True,
        "contract": CONTRACT,
    }
    store.confirm_reservation("place_order", parameters, **arguments)
    with raises_message(TradingControlError, "contract changed"):
        store.confirm_reservation(
            "place_order",
            parameters,
            **(arguments | {"contract": CONTRACT | {"code": "US.TSLA"}}),
        )
    clock.advance(10)
    with raises_message(TradingControlError, "expired"):
        store.confirm_reservation("place_order", parameters, **arguments)
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 40
    assert store.get(decision["request_id"])["status"] == "CONSUMED"


def test_confirm_requires_the_exact_charge_and_bound_approval(controls):
    store, _ = controls
    parameters = request()
    first = approved(store, parameters)
    second = approved(store, parameters)
    consume(store, parameters, approval=first["request_id"])
    with raises_message(TradingControlError, "expired"):
        store.confirm_reservation(
            "place_order",
            parameters,
            approval_id=second["request_id"],
            require_approval=True,
            contract=CONTRACT,
        )
    with raises_message(TradingControlError, "unavailable"):
        store.confirm_reservation(
            "place_order",
            parameters | {"qty": 99},
            approval_id=first["request_id"],
            require_approval=True,
            contract=CONTRACT,
        )
    with raises_message(TradingControlError, "unavailable"):
        store.confirm_reservation(
            "place_order", request("unknown"), approval_id=None, require_approval=False
        )
    store.confirm_reservation(
        "place_order", parameters, approval_id=None, require_approval=False
    )


@pytest.mark.parametrize("require_approval", [False, True])
def test_confirm_refuses_a_previous_utc_day_reservation(controls, require_approval):
    store, clock = controls
    parameters = request()
    decision = (
        approved(store, parameters, ttl_seconds=300) if require_approval else None
    )
    identifier = decision["request_id"] if decision else None
    consume(store, parameters, amount="40", approval=identifier)
    clock.advance(30)
    with raises_message(TradingControlError, "UTC day boundary"):
        store.confirm_reservation(
            "place_order",
            parameters,
            approval_id=identifier,
            require_approval=require_approval,
            contract=CONTRACT,
        )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}
    clock.advance(-30)
    assert Decimal(store.usage("REAL", ACCOUNT)["amounts"]["USD"]) == 40


def test_approval_and_reservation_reopen_with_the_same_evidence(controls):
    first, clock = controls
    parameters = request()
    decision = approved(first, parameters)
    consume(first, parameters, amount="40", approval=decision["request_id"])
    first.close()
    reopened = TradingControls(first.path, clock=clock)
    try:
        persisted = reopened.get(decision["request_id"])
        assert persisted["status"] == "CONSUMED"
        assert persisted["operator_id"] == "configured-operator"
        reopened.confirm_reservation(
            "place_order",
            parameters,
            approval_id=decision["request_id"],
            require_approval=True,
            contract=CONTRACT,
        )
        with raises_message(TradingControlError, "already consumed"):
            consume(reopened, parameters, approval=decision["request_id"])
    finally:
        reopened.close()


@pytest.mark.parametrize("missing_leg", [0, 1, 2])
def test_symbol_policy_checks_every_exact_combo_leg(missing_leg):
    codes = ["US.AAPL261120C100000", "US.AAPL261120C105000", "US.AAPL261120P100000"]
    policy = TradingPolicy(
        symbol_allowlist=frozenset(
            code for index, code in enumerate(codes) if index != missing_leg
        )
    )
    facts = OrderFacts(
        "NORMAL",
        "BUY",
        1,
        [
            LegFacts(
                InstrumentFacts(code, classification="DRVT", monetary_multiplier=100)
            )
            for code in codes
        ],
        price=1,
        is_combo=True,
    )
    with pytest.raises(TradingPolicyError) as failure:
        policy.assess_order("place_combo_order", facts)
    assert codes[missing_leg] in str(failure.value)


def test_underlying_allowlist_does_not_authorize_a_derivative_or_modify_target():
    policy = TradingPolicy(symbol_allowlist=frozenset({"US.AAPL"}))
    derivative = OrderFacts(
        "NORMAL",
        "BUY",
        1,
        [
            LegFacts(
                InstrumentFacts(
                    "US.AAPL261120C100000",
                    classification="DRVT",
                    monetary_multiplier=100,
                )
            )
        ],
        price=1,
    )
    with raises_message(TradingPolicyError, "symbol allowlist"):
        policy.assess_order("place_order", derivative)
    changed_target = replace(
        derivative, legs=[LegFacts(InstrumentFacts("US.TSLA", classification="STOCK"))]
    )
    with raises_message(TradingPolicyError, "symbol allowlist"):
        policy.assess_order("modify_order", changed_target)
    allowed = replace(
        derivative, legs=[LegFacts(InstrumentFacts("US.AAPL", classification="STOCK"))]
    )
    policy.assess_order("modify_order", allowed)


@pytest.mark.parametrize("combo", [False, True])
def test_daily_valuation_decimal_products_preserve_more_than_28_quantity_digits(combo):
    policy = TradingPolicy()
    quantity = 123456789012345678901234567890123456789
    instrument = InstrumentFacts(
        "US.AAPL261120C100000",
        classification="DRVT",
        monetary_multiplier=100,
        contract_size=100,
    )
    facts = OrderFacts(
        "NORMAL", "BUY", quantity, [LegFacts(instrument)], price=1.25, is_combo=combo
    )
    estimate = policy.estimate_order("daily budget", facts)
    expected_cents = 125 * quantity * 100
    expected = f"{expected_cents // 100}.{expected_cents % 100:02d}"
    assert Decimal(estimate["amount"]) == Decimal(expected)
    assert estimate["currency"] == "USD"


def test_daily_modify_valuation_uses_full_total_qty_and_conservative_sell_price(
    controls,
):
    store, _ = controls
    policy = TradingPolicy(
        TradingMode.REAL,
        real_acc_ids=frozenset({int(ACCOUNT)}),
        max_daily_notional={"USD": 150},
    )
    facts = OrderFacts(
        "NORMAL",
        "SELL",
        10,
        [
            LegFacts(
                InstrumentFacts(
                    "US.AAPL",
                    classification="STOCK",
                    last_price=20,
                )
            )
        ],
        price=12,
    )
    valuation = policy.estimate_order("modify_order", facts)
    assert Decimal(valuation["amount"]) == 200
    with raises_message(TradingControlError, "budget exceeded"):
        store.consume(
            "modify_order",
            request(),
            approval_id=None,
            require_approval=False,
            amount=Decimal(valuation["amount"]),
            currency=valuation["currency"],
            caps=policy.max_daily_notional,
        )
    assert store.usage("REAL", ACCOUNT)["amounts"] == {}


def test_daily_valuation_unknown_currency_and_option_multiplier_fail_closed():
    policy = TradingPolicy(max_daily_notional={"USD": 100})
    unknown_currency = OrderFacts(
        "NORMAL",
        "BUY",
        1,
        [
            LegFacts(
                InstrumentFacts(
                    "AU.TEST",
                    classification="STOCK",
                )
            )
        ],
        price=10,
    )
    with raises_message(TradingPolicyError, "currency"):
        policy.estimate_order("daily budget", unknown_currency)
    unknown_multiplier = replace(
        unknown_currency,
        legs=[
            LegFacts(
                InstrumentFacts(
                    "US.AAPL261120C100000",
                    classification="DRVT",
                    monetary_multiplier=None,
                )
            )
        ],
    )
    with raises_message(TradingPolicyError, "multiplier"):
        policy.estimate_order("daily budget", unknown_multiplier)
