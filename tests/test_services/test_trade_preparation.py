"""Prepared approvals and daily budgets at actual journal/SDK boundaries."""

from __future__ import annotations

import inspect
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast

import pandas as pd
import pytest
from moomoo import RET_OK, OpenSecTradeContext

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.order_errors import OrderNotSentError
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_controls import TradingControls
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    TradingMode,
    TradingPolicy,
)
from tests.rate_limit_clock import FakeClock

FIRST_OPTION = "US.AAPL261218C100000"
SECOND_OPTION = "US.AAPL261218C105000"


class WallClock:
    def __init__(self):
        self.now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Broker:
    """A broker double requiring a committed matching dispatch marker."""

    def __init__(self, paths):
        self.paths = paths
        self.clock = FakeClock()
        self.calls = []
        self.reads = []
        self.unlocks = []
        self.orders = {}
        self.failure = None
        self.on_unlock = None
        self.next_order_id = 9007199254740993

    def _read(self, method, params):
        self.reads.append((method, params.copy()))
        self.clock.advance(5)

    def get_acc_list(self):
        self._read("accounts", {})
        return RET_OK, pd.DataFrame(
            [
                {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
                {"acc_id": 456, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            ]
        )

    def acctradinginfo_query(self, **params):
        self._read("maximum", params)
        return RET_OK, pd.DataFrame(
            [
                {
                    "max_cash_buy": 50,
                    "max_cash_and_margin_buy": 100,
                    "max_position_sell": 2,
                    "max_sell_short": 0,
                    "max_buy_back": 0,
                    "long_required_im": None,
                    "short_required_im": None,
                }
            ]
        )

    def comboorder_tradinginfo_query(self, **params):
        self._read("combo_preview", params)
        return RET_OK, pd.DataFrame(
            [
                {
                    "nlv_change": -100,
                    "initial_margin_change": 100,
                    "maintenance_margin_change": 100,
                    "option_bp": 500,
                    "max_withdraw_change": -100,
                    "bp_decrease": 100,
                }
            ]
        )

    def order_list_query(self, **params):
        self._read("orders", params)
        rows = [
            row.copy()
            for row in self.orders.values()
            if row["trd_env"] == params["trd_env"]
            and row["acc_id"] == params["acc_id"]
            and (not params.get("order_id") or row["order_id"] == params["order_id"])
        ]
        return RET_OK, pd.DataFrame(rows)

    def history_order_list_query(self, **params):
        return self.order_list_query(**params)

    def position_list_query(self, **params):
        self._read("positions", params)
        return RET_OK, pd.DataFrame(
            [
                {
                    "code": params.get("code", "US.AAPL"),
                    "qty": 2,
                }
            ]
        )

    def unlock_trade(self, **params):
        self.unlocks.append(params["is_unlock"])
        self.clock.advance(5)
        if params["is_unlock"] and self.on_unlock is not None:
            self.on_unlock()
        return RET_OK, None

    def _write(self, kind, params):
        environment = params["trd_env"]
        with sqlite3.connect(self.paths[environment], timeout=0.5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if kind in {"PLACE", "PLACE_COMBO"}:
                rows = connection.execute(
                    "SELECT state,marker FROM operations WHERE order_tag=?",
                    (params["remark"],),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT state,marker FROM operations WHERE state='DISPATCHING'"
                ).fetchall()
            assert rows and all(row == ("DISPATCHING", 1) for row in rows)
            connection.rollback()
        self.calls.append((kind, params.copy()))
        self.clock.advance(5)
        if self.failure == "timeout":
            raise TimeoutError("synthetic response loss")
        identity = str(params.get("order_id", self.next_order_id))
        self.next_order_id += 1
        order = self.orders.get(identity, {}).copy()
        if kind == "CANCEL":
            order["order_status"] = "CANCELLED_ALL"
        else:
            order.update(params)
            order.update(
                qty=float(params["qty"]),
                price=float(params["price"]),
                order_status="SUBMITTED",
                dealt_qty=0,
                dealt_avg_price=0,
            )
        order["order_id"] = identity
        if kind == "PLACE_COMBO":
            order["combo_legs"] = params["combo_leg_list"]
            order.pop("combo_leg_list")
        self.orders[identity] = order
        return RET_OK, pd.DataFrame([order])

    def place_order(self, **params):
        return self._write("PLACE", params)

    def place_combo_order(self, **params):
        return self._write("PLACE_COMBO", params)

    def modify_order(self, **params):
        return self._write(
            "CANCEL" if params["modify_order_op"] == "CANCEL" else "MODIFY", params
        )

    def seed(self, environment):
        identity = str(self.next_order_id)
        self.next_order_id += 1
        self.orders[identity] = {
            "order_id": identity,
            "code": "US.AAPL",
            "trd_side": "BUY",
            "qty": 2.0,
            "price": 10.0,
            "dealt_qty": 0,
            "dealt_avg_price": 0,
            "order_type": "NORMAL",
            "time_in_force": "DAY",
            "session": "RTH",
            "trd_env": environment,
            "acc_id": 123 if environment == "REAL" else 456,
            "order_status": "SUBMITTED",
            "aux_price": "N/A",
        }
        return identity


@dataclass
class Rig:
    service: TradeService
    broker: Broker
    stores: dict[str, ExecutionStore]
    controls: TradingControls | None
    clock: WallClock
    instruments: dict[str, InstrumentFacts]


@pytest.fixture
def build(tmp_path):
    rigs = []

    def create(*, mode=TradingMode.REAL, approval=True, budget=1000, jit=False):
        root = tmp_path / str(len(rigs))
        clock = WallClock()
        stores = {}
        paths = {}
        if mode is not TradingMode.READ_ONLY:
            for environment in ("REAL", "SIMULATE"):
                paths[environment] = root / environment / "execution.sqlite3"
                stores[environment] = ExecutionStore(
                    paths[environment], create=True, environment=environment
                )
        controls = (
            TradingControls(root / "controls.sqlite3", create=True, clock=clock)
            if mode is not TradingMode.READ_ONLY
            else None
        )
        instruments = {
            "US.AAPL": InstrumentFacts(
                code="US.AAPL",
                classification="STOCK",
                monetary_multiplier=1,
                last_price=10,
                bid_price=10,
                ask_price=10,
                lot_size=1,
            ),
            FIRST_OPTION: InstrumentFacts(
                code=FIRST_OPTION,
                classification="DRVT",
                monetary_multiplier=100,
                contract_size=100,
                last_price=3,
                lot_size=100,
            ),
            SECOND_OPTION: InstrumentFacts(
                code=SECOND_OPTION,
                classification="DRVT",
                monetary_multiplier=100,
                contract_size=100,
                last_price=3,
                lot_size=100,
            ),
        }
        broker = Broker(paths)
        policy = TradingPolicy(
            mode,
            real_acc_ids=frozenset({123}),
            symbol_allowlist=frozenset(instruments),
            require_approval=approval and mode is not TradingMode.READ_ONLY,
            max_daily_notional={"USD": budget}
            if mode is not TradingMode.READ_ONLY
            else {},
        )
        service = TradeService(
            policy=policy,
            execution_store=stores.get("SIMULATE"),
            real_execution_store=stores.get("REAL"),
            simulated_account_allowlist=frozenset({456}),
            dispatcher=BrokerRequestDispatcher(clock=broker.clock),
            trading_controls=controls,
            instrument_lookup=lambda codes: [instruments[code] for code in codes],
            trade_password="synthetic-test-credential" if jit else None,
        )
        service.trade_ctx = cast(OpenSecTradeContext, broker)
        rig = Rig(service, broker, stores, controls, clock, instruments)
        rigs.append(rig)
        return rig

    yield create
    for rig in rigs:
        if rig.service.real is not None:
            rig.service.real.recovery.stop()
        if rig.service.paper is not None:
            rig.service.paper.recovery.stop()
        if rig.controls is not None:
            rig.controls.close()
        for store in rig.stores.values():
            store.close()


def parameters(rig, environment="REAL", token="operation"):
    return {
        "code": "US.AAPL",
        "price": 10.0 if environment == "REAL" else "10.00",
        "qty": 2,
        "trd_side": "BUY",
        "trd_env": environment,
        "acc_id": "123" if environment == "REAL" else "456",
        "operation_id": token,
        "admission_epoch": rig.stores[environment].epoch,
    }


def approve(rig, prepared):
    assert rig.controls is not None
    return rig.controls.decide(
        prepared["request_id"],
        "APPROVED",
        operator_id="test-operator",
        reason="Synthetic operator reviewed the exact request",
    )


def execute(rig, prepared, *, approval_id=None):
    return getattr(rig.service, prepared["method"])(
        **prepared["parameters"], approval_id=approval_id or prepared["request_id"]
    )


def charges(rig):
    assert rig.controls is not None
    with sqlite3.connect(rig.controls.path) as connection:
        return connection.execute("SELECT amount,day FROM charges").fetchall()


def combo_parameters(rig, token="combo"):
    return {
        "combo_legs": [
            {"code": FIRST_OPTION, "trd_side": "BUY", "qty_ratio": 1},
            {"code": SECOND_OPTION, "trd_side": "SELL", "qty_ratio": 1},
        ],
        "price": 1.0,
        "qty": 1,
        "trd_env": "REAL",
        "acc_id": "123",
        "operation_id": token,
        "admission_epoch": rig.stores["REAL"].epoch,
    }


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_read_only_preview_never_initializes_or_admits_execution(build, environment):
    rig = build(mode=TradingMode.READ_ONLY)
    result = rig.service.preview_order(
        code="US.AAPL",
        price=10,
        qty=2,
        trd_side="BUY",
        trd_env=environment,
        acc_id="123" if environment == "REAL" else "456",
    )
    assert result["valuation"]["amount"] == "20.00"
    assert result["maximum_quantities"]["max_cash_buy"] == 50
    assert result["writes_allowed_by_mode"] is False
    assert result["instrument_rules"]["tick_validation"] == "VERIFIED"
    assert rig.service.real is None and rig.service.paper is None
    assert rig.stores == {} and rig.controls is None
    assert rig.broker.calls == [] and rig.broker.unlocks == []
    assert {name for name, _ in rig.broker.reads} == {"accounts", "maximum"}


@pytest.mark.parametrize(
    "method", ["place_order", "place_combo_order", "modify_order", "cancel_order"]
)
def test_prepared_parameters_match_exact_mutation_signature_without_admission(
    build, method
):
    rig = build()
    request = parameters(rig)
    if method == "place_combo_order":
        request = combo_parameters(rig)
    elif method in {"modify_order", "cancel_order"}:
        identity = rig.broker.seed("REAL")
        request = {
            key: value
            for key, value in request.items()
            if key in {"trd_env", "acc_id", "operation_id", "admission_epoch"}
        } | {"order_id": identity}
        if method == "modify_order":
            request.update(modify_order_op="NORMAL", price=11)
    prepared = rig.service.prepare_trade_request(method, request)
    assert prepared["method"] == method
    assert prepared["status"] == "PENDING"
    assert "method" not in prepared["parameters"]
    assert "approval_id" not in prepared["parameters"]
    signature = inspect.signature(getattr(rig.service, method))
    signature.bind(**prepared["parameters"])
    assert set(prepared["parameters"]) == set(signature.parameters) - {"approval_id"}
    if method == "modify_order":
        assert prepared["review"]["contract"]["qty"] == {"number": "2"}
    assert rig.broker.calls == [] and rig.broker.unlocks == []
    for store in rig.stores.values():
        with sqlite3.connect(store._path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations").fetchone() == (
                0,
            )


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_approved_dispatch_and_journal_retry_charge_once(build, environment):
    rig = build()
    prepared = rig.service.prepare_trade_request(
        "place_order", parameters(rig, environment)
    )
    approve(rig, prepared)
    first = execute(rig, prepared)
    assert first["state"] == "ACKNOWLEDGED"
    assert first["broker_order_id"] == "9007199254740993"
    assert len(charges(rig)) == 1 and Decimal(charges(rig)[0][0]) == 20
    assert rig.controls.get(prepared["request_id"])["status"] == "CONSUMED"
    # Stored retry does not need a replacement approval and never recharges.
    second = execute(rig, prepared, approval_id="another-decision")
    assert second["state"] == first["state"]
    assert len(rig.broker.calls) == 1 and len(charges(rig)) == 1


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
@pytest.mark.parametrize("decision", ["PENDING", "REJECTED", "EXPIRED", "MISSING"])
def test_missing_rejected_or_expired_approval_refuses_before_dispatch(
    build, environment, decision
):
    rig = build(jit=True)
    prepared = rig.service.prepare_trade_request(
        "place_order", parameters(rig, environment)
    )
    assert rig.controls is not None
    if decision == "REJECTED":
        rig.controls.decide(
            prepared["request_id"],
            "REJECTED",
            operator_id="test-operator",
            reason="Synthetic rejection",
        )
    elif decision in {"EXPIRED", "MISSING"}:
        approve(rig, prepared)
        if decision == "EXPIRED":
            rig.clock.advance(301)
    with pytest.raises(OrderNotSentError):
        getattr(rig.service, prepared["method"])(
            **prepared["parameters"],
            approval_id=None if decision == "MISSING" else prepared["request_id"],
        )
    row = rig.stores[environment].lookup("operation")
    assert row["state"] == "REFUSED" and row["marker"] == 0
    assert charges(rig) == []
    assert rig.broker.calls == [] and rig.broker.unlocks == []


@pytest.mark.parametrize(
    "environment,method,expected_charge",
    [
        ("REAL", "place_combo_order", 100),
        ("REAL", "modify_order", 22),
        ("SIMULATE", "modify_order", 22),
        ("REAL", "cancel_order", 0),
        ("SIMULATE", "cancel_order", 0),
    ],
)
def test_combo_modify_and_cancel_approval_bind_dispatch_and_stored_retry(
    build, environment, method, expected_charge
):
    rig = build(jit=True)
    if method == "place_combo_order":
        request = combo_parameters(rig)
    else:
        identity = rig.broker.seed(environment)
        request = {
            "order_id": identity,
            "trd_env": environment,
            "acc_id": "123" if environment == "REAL" else "456",
            "operation_id": "operation",
            "admission_epoch": rig.stores[environment].epoch,
        }
        if method == "modify_order":
            request.update(
                modify_order_op="NORMAL",
                price=11 if environment == "REAL" else "11.00",
            )
    prepared = rig.service.prepare_trade_request(method, request)
    assert rig.broker.calls == [] and rig.broker.unlocks == []
    approve(rig, prepared)
    first = execute(rig, prepared)
    assert first["state"] == "ACKNOWLEDGED"
    assert first["error"] is None
    assert rig.stores[environment].lookup(request["operation_id"])["marker"] == 1
    assert len(charges(rig)) == 1
    assert Decimal(charges(rig)[0][0]) == expected_charge
    before_unlocks = rig.broker.unlocks.copy()
    second = execute(rig, prepared)
    assert second["state"] == first["state"]
    assert len(rig.broker.calls) == 1 and len(charges(rig)) == 1
    assert rig.broker.unlocks == before_unlocks
    assert before_unlocks == ([True, False] if environment == "REAL" else [])


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_storage_schema_failure_after_approval_blocks_before_unlock_and_marker(
    build, environment
):
    rig = build(jit=True)
    prepared = rig.service.prepare_trade_request(
        "place_order", parameters(rig, environment)
    )
    approve(rig, prepared)
    assert rig.controls is not None
    with sqlite3.connect(rig.controls.path) as connection:
        connection.execute("DROP TABLE charges")
    with pytest.raises(OrderNotSentError) as error:
        execute(rig, prepared)
    assert "storage is blocked" in str(error.value)
    assert rig.controls.health()["state"] == "BLOCKED"
    assert rig.stores[environment].lookup("operation")["marker"] == 0
    assert rig.broker.calls == [] and rig.broker.unlocks == []


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_unknown_outcome_remains_charged_and_retry_never_resubmits(build, environment):
    rig = build(budget=20)
    prepared = rig.service.prepare_trade_request(
        "place_order", parameters(rig, environment)
    )
    approve(rig, prepared)
    rig.broker.failure = "timeout"
    first = execute(rig, prepared)
    assert first["state"] == "UNKNOWN_OUTCOME"
    assert first["disposition"] == "POSSIBLY_SENT"
    second = execute(rig, prepared)
    assert second["state"] == "UNKNOWN_OUTCOME"
    assert len(rig.broker.calls) == 1
    assert len(charges(rig)) == 1 and Decimal(charges(rig)[0][0]) == 20
    assert rig.stores[environment].lookup("operation")["marker"] == 1


def test_price_only_modification_binds_projected_contract_and_rejects_drift(build):
    rig = build()
    identity = rig.broker.seed("REAL")
    request = {
        "order_id": identity,
        "modify_order_op": "NORMAL",
        "price": 11,
        "trd_env": "REAL",
        "acc_id": "123",
        "operation_id": "modify",
        "admission_epoch": rig.stores["REAL"].epoch,
    }
    prepared = rig.service.prepare_trade_request("modify_order", request)
    approve(rig, prepared)
    rig.broker.orders[identity]["qty"] = 3.0
    with pytest.raises(OrderNotSentError) as error:
        execute(rig, prepared)
    assert "contract changed" in str(error.value)
    assert rig.stores["REAL"].lookup("modify")["marker"] == 0
    assert charges(rig) == [] and rig.broker.calls == []


@pytest.mark.parametrize("environment", ["REAL", "SIMULATE"])
def test_daily_budget_is_atomic_for_concurrent_journaled_submissions(
    build, environment
):
    rig = build(approval=False, budget=20)

    def submit(token):
        try:
            return rig.service.place_order(**parameters(rig, environment, token))[
                "state"
            ]
        except OrderNotSentError:
            return "REFUSED"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(submit, ["first", "second"]))
    assert sorted(results) == ["ACKNOWLEDGED", "REFUSED"]
    assert len(rig.broker.calls) == 1
    assert len(charges(rig)) == 1 and Decimal(charges(rig)[0][0]) == 20


def test_daily_charge_survives_controls_restart(build):
    rig = build(approval=False, budget=20)
    rig.service.place_order(**parameters(rig, token="first"))
    assert rig.controls is not None
    path = rig.controls.path
    rig.controls.close()
    rig.controls = TradingControls(path, clock=rig.clock)
    rig.service.trading_controls = rig.controls
    with pytest.raises(OrderNotSentError):
        rig.service.place_order(**parameters(rig, token="second"))
    assert len(rig.broker.calls) == 1
    assert len(charges(rig)) == 1 and Decimal(charges(rig)[0][0]) == 20


@pytest.mark.parametrize("boundary", ["EXPIRY", "UTC_DAY"])
def test_expiry_and_utc_rollover_during_unlock_refuse_before_marker(build, boundary):
    rig = build(jit=True)
    if boundary == "UTC_DAY":
        rig.clock.now = datetime(
            2026, 10, 11, 23, 59, 59, tzinfo=timezone.utc
        ).timestamp()
    prepared = rig.service.prepare_trade_request(
        "place_order",
        parameters(rig),
        ttl_seconds=1 if boundary == "EXPIRY" else 300,
    )
    approve(rig, prepared)
    rig.broker.on_unlock = lambda: rig.clock.advance(2)
    with pytest.raises(OrderNotSentError) as error:
        execute(rig, prepared)
    assert ("expired" if boundary == "EXPIRY" else "UTC day") in str(error.value)
    assert rig.broker.calls == []
    assert rig.broker.unlocks == [True, False]
    assert rig.stores["REAL"].lookup("operation")["marker"] == 0
    # A committed reservation is retained conservatively despite no dispatch.
    assert len(charges(rig)) == 1 and Decimal(charges(rig)[0][0]) == 20


def test_combo_caller_mutation_cannot_switch_approval_binding_after_admission(build):
    rig = build()
    approved_request = combo_parameters(rig)
    approved_request["combo_legs"][0]["trd_side"] = "SELL"
    approved_request["combo_legs"][1]["trd_side"] = "BUY"
    prepared = rig.service.prepare_trade_request("place_combo_order", approved_request)
    approve(rig, prepared)
    supplied = deepcopy(prepared["parameters"])
    supplied["combo_legs"][0]["trd_side"] = "BUY"
    supplied["combo_legs"][1]["trd_side"] = "SELL"

    def mutate_caller_during_lookup(codes):
        supplied["combo_legs"][0]["trd_side"] = "SELL"
        supplied["combo_legs"][1]["trd_side"] = "BUY"
        return [rig.instruments[code] for code in codes]

    rig.service.instrument_lookup = mutate_caller_during_lookup
    with pytest.raises(OrderNotSentError):
        rig.service.place_combo_order(**supplied, approval_id=prepared["request_id"])
    assert rig.broker.calls == [] and charges(rig) == []
    row = rig.stores["REAL"].lookup("combo")
    assert row["marker"] == 0
    assert json.loads(row["request"])["combo_legs"][0]["trd_side"] == "BUY"


def test_paper_preview_and_prepare_preserve_exact_grid_validation(build):
    rig = build()
    request = parameters(rig, "SIMULATE") | {
        "price": "1.100000000000000000000000000000001"
    }
    with pytest.raises(ValueError):
        rig.service.preview_order(
            **{
                key: value
                for key, value in request.items()
                if key not in {"operation_id", "admission_epoch"}
            }
        )
    with pytest.raises(ValueError):
        rig.service.prepare_trade_request("place_order", request)
    assert rig.broker.calls == [] and rig.broker.unlocks == []


def test_every_combo_symbol_is_checked_before_preparation(build):
    rig = build()
    rig.service.policy = replace(
        rig.service.policy, symbol_allowlist=frozenset({FIRST_OPTION})
    )
    with pytest.raises(RuntimeError) as error:
        rig.service.prepare_trade_request("place_combo_order", combo_parameters(rig))
    assert "symbol allowlist" in str(error.value)
    assert rig.broker.calls == []
