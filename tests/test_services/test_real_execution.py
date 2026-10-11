"""REAL journaling acceptance with actual SQLite and an explicit broker double."""

import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK, OpenSecTradeContext

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.execution_store import (
    ASSUMED_ABSENT,
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)
from moomoo_mcp.services.order_errors import OrderNotSentError
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.rate_limit_clock import FakeClock


class Broker:
    def __init__(self, path):
        self.path = path
        self.clock = FakeClock()
        self.calls = []
        self.reads = []
        self.orders = {}
        self.failure = None
        self.unlock_failure = False
        self.relock_failure = False
        self.entered = None
        self.release = None
        self.stale = None

    def _mutate(self, kind, params):
        with sqlite3.connect(self.path, timeout=0.5) as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert conn.execute(
                "SELECT state,marker FROM operations ORDER BY rowid DESC LIMIT 1"
            ).fetchone() == ("DISPATCHING", 1)
            conn.rollback()
        self.calls.append((kind, params.copy()))
        self.clock.advance(2)
        if self.entered is not None:
            self.entered.set()
            assert self.release is not None
            assert self.release.wait(5)
        if self.failure == "timeout":
            raise TimeoutError("no response")
        identity = str(params.get("order_id", "9007199254740993"))
        order = self.orders.get(identity, {}).copy()
        if kind == "CANCEL":
            order.update(order_status="FILLED_ALL", dealt_qty=order["qty"])
        else:
            order.update(
                {key: value for key, value in params.items() if value is not None}
            )
            order.update(order_status="SUBMITTED", dealt_qty=0, dealt_avg_price=0)
        order["order_id"] = identity
        self.orders[identity] = order
        if self.failure == "lost_response":
            raise TimeoutError("accepted but response lost")
        if self.failure == "unreadable":
            return RET_OK, pd.DataFrame([{"order_id": identity}])
        return RET_OK, pd.DataFrame([order])

    def place_order(self, **params):
        return self._mutate("PLACE", params)

    def place_combo_order(self, **params):
        params["combo_legs"] = [
            {"code": leg.code, "trd_side": leg.trd_side, "qty_ratio": leg.qty_ratio}
            | ({"position_id": leg.position_id} if leg.position_id is not None else {})
            for leg in params.pop("combo_leg_list")
        ]
        return self._mutate("PLACE_COMBO", params)

    def modify_order(self, **params):
        return self._mutate(
            "CANCEL" if params["modify_order_op"] == "CANCEL" else "MODIFY", params
        )

    def get_acc_list(self):
        self.reads.append(("accounts", {}))
        self.clock.advance(2)
        return RET_OK, pd.DataFrame(
            [{"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US", "HK"]}]
        )

    def order_list_query(self, **params):
        self.reads.append(("orders", params))
        self.clock.advance(2)
        rows = list(self.orders.values()) if self.stale is None else self.stale
        return RET_OK, pd.DataFrame(rows)

    def history_order_list_query(self, **params):
        self.reads.append(("history", params))
        self.clock.advance(2)
        return RET_OK, pd.DataFrame(list(self.orders.values()))

    def position_list_query(self, **params):
        self.reads.append(("positions", params))
        self.clock.advance(2)
        return RET_OK, pd.DataFrame(
            [{"code": params["code"], "qty": 4, "acc_id": 123, "trd_env": "REAL"}]
        )

    def unlock_trade(self, **params):
        self.reads.append(("unlock", params))
        self.clock.advance(2)
        failed = self.unlock_failure if params["is_unlock"] else self.relock_failure
        return (RET_ERROR, "test gateway refusal") if failed else (RET_OK, None)


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "real" / "execution.db"
    store = ExecutionStore(path, create=True, environment="REAL")
    broker = Broker(path)
    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
        real_execution_store=store,
        dispatcher=BrokerRequestDispatcher(clock=broker.clock),
    )
    service.trade_ctx = cast(OpenSecTradeContext, broker)
    assert service.real is not None
    service.real.recovery.clock = broker.clock
    yield service, broker, store
    service.real.recovery.stop()
    store.close()


def place(service, token="operation", **changes: Any):
    assert service.real is not None
    params = {
        "code": "US.AAPL",
        "price": 150.0,
        "qty": 2,
        "trd_side": "BUY",
        "trd_env": "REAL",
        "acc_id": "0",
        "operation_id": token,
        "admission_epoch": service.real.store.epoch,
    } | changes
    return service.place_order(**params)


def test_environment_identity_and_wrong_open_do_not_modify_database(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.close()
    before = path.read_bytes()
    with pytest.raises(ExecutionStoreError) as error:
        ExecutionStore(path, environment="REAL")
    assert "environment" in str(error.value)
    assert path.read_bytes() == before
    real_path = tmp_path / "real" / "journal.db"
    live = ExecutionStore(real_path, create=True, environment="REAL")
    live.close()
    before = real_path.read_bytes()
    with pytest.raises(ExecutionStoreError) as error:
        ExecutionStore(real_path)
    assert "environment" in str(error.value)
    assert real_path.read_bytes() == before


def test_wrong_environment_wal_probe_preserves_all_original_files(tmp_path):
    path = tmp_path / "paper" / "journal.db"
    store = ExecutionStore(path, create=True)
    store.close()
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE metadata SET environment=environment")
        files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
        before = {file: file.read_bytes() for file in files}
        with pytest.raises(ExecutionStoreError):
            ExecutionStore(path, environment="REAL")
        assert {file: file.read_bytes() for file in files} == before


def test_duplicate_frozen_account_conflicts_and_disconnected_retry(rig):
    service, broker, store = rig
    first = place(service, remark="caller note")
    assert first["state"] == "ACKNOWLEDGED"
    assert first["order_id"] == "9007199254740993"
    assert broker.calls[0][1]["remark"] == first["order_tag"]
    row = store.lookup("operation")
    assert json.loads(row["request"])["remark"] == "caller note"
    reads = len(broker.reads)
    service.trade_ctx = None
    service.policy = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({456}))
    assert place(service, price=150, remark="caller note") == first
    assert len(broker.reads) == reads and len(broker.calls) == 1
    for patch in (
        {"price": 151},
        {"remark": "changed"},
        {"acc_id": 456},
        {"time_in_force": "GTC"},
    ):
        with pytest.raises(ExecutionConflict):
            place(service, **cast(dict[str, Any], {"remark": "caller note"} | patch))


def test_inflight_retry_returns_without_waiting_for_serialized_mutation(rig):
    service, broker, _store = rig
    broker.entered, broker.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(place, service)
        assert broker.entered.wait(3)
        second = pool.submit(place, service).result(timeout=1)
        assert second["state"] == "DISPATCHING"
        broker.release.set()
        assert first.result(timeout=3)["state"] == "ACKNOWLEDGED"
    assert len(broker.calls) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"operation_id": None},
        {"admission_epoch": "retired"},
        {"qty": True},
        {"price": "150"},
        {"price": float("nan")},
        {"acc_id": "123.4"},
    ],
)
def test_invalid_new_requests_fail_before_account_reads_or_mutations(rig, patch):
    service, broker, _store = rig
    with pytest.raises(OrderNotSentError):
        place(service, **patch)
    assert broker.reads == [] and broker.calls == []


@pytest.mark.parametrize(
    "code,order_type,price,extra",
    [
        ("HK.00700", "NORMAL", 350.0, {"time_in_force": "GTC"}),
        ("US.AAPL", "MARKET", 0, {}),
        ("US.AAPL", "STOP_LIMIT", 150.0, {"aux_price": 149.0}),
    ],
)
def test_existing_real_markets_and_order_types_remain_journaled(
    rig, code, order_type, price, extra
):
    service, broker, store = rig
    result = place(service, code=code, order_type=order_type, price=price, **extra)
    assert result["state"] == "ACKNOWLEDGED"
    assert len(broker.calls) == 1 and store.lookup("operation")["marker"] == 1


def test_combo_identity_and_nested_leg_conflict(rig):
    service, broker, store = rig
    params = {
        "combo_legs": [
            {"code": "US.AAPL260116C150000", "trd_side": "BUY", "qty_ratio": 1},
            {"code": "US.AAPL260116C155000", "trd_side": "SELL", "qty_ratio": 1},
        ],
        "price": 1.2,
        "qty": 2,
        "trd_env": "REAL",
        "acc_id": 123,
        "operation_id": "combo",
        "admission_epoch": store.epoch,
    }
    first = service.place_combo_order(**params)
    assert first["order_tag"] and store.lookup("combo")["kind"] == "PLACE_COMBO"
    assert service.place_combo_order(**params) == first
    params["combo_legs"][1]["qty_ratio"] = 2
    with pytest.raises(ExecutionConflict) as error:
        service.place_combo_order(**params)
    assert "combo_legs" in str(error.value)
    assert len(broker.calls) == 1


def test_repeated_real_negatives_never_release_and_storage_rejects_assumption(rig):
    service, broker, store = rig
    broker.failure = "timeout"
    result = place(service)
    assert result["state"] == "UNKNOWN_OUTCOME"
    for _ in range(8):
        broker.clock.advance(31)
        result = service.real.reconcile("operation")
        assert result["state"] == "UNKNOWN_OUTCOME"
        assert (
            result["recovery"]["pending"]
            and not result["recovery"]["assumed_absence_allowed"]
        )
    assert result["recovery"]["successful_negative_checks"] == 8
    assert service.real.health()["state"] == "JOURNAL_BLOCKED"
    with pytest.raises(ExecutionConflict) as error:
        store.record_recovery(
            "operation",
            observed_state="UNKNOWN_OUTCOME",
            started_at=300,
            completed_at=300,
            outcome="NOT_FOUND",
            details={},
            next_check_at=330,
            resolution=ASSUMED_ABSENT,
        )
    assert "cannot assume absence" in str(error.value)
    assert len(store.lookup("operation")["recovery_checks"]) == 8
    with pytest.raises(OrderNotSentError):
        place(service, token="new")
    assert len(broker.calls) == 1


def test_lost_response_recovers_exact_tag_with_real_position_evidence(rig):
    service, broker, store = rig
    broker.failure = "lost_response"
    uncertain = place(service)
    assert uncertain["state"] == "UNKNOWN_OUTCOME"
    result = service.real.reconcile("operation")
    assert result["state"] == "RECONCILED"
    assert result["recovery"]["disposition"] == "BROKER_CONFIRMED"
    assert result["accounted_facts"]["trd_env"] == "REAL"
    assert result["accounted_facts"]["current_symbol_position"] == "4"
    assert service.real.health()["state"] == "READY"
    assert place(service) == result and len(broker.calls) == 1
    assert all(
        params.get("trd_env") == "REAL" and params.get("acc_id") == 123
        for kind, params in broker.reads
        if kind != "accounts"
    )
    with store._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM recovery_audit").fetchone() == (1,)


@pytest.mark.parametrize(
    "problem", ["similar", "duplicate", "wrong_account", "missing_field"]
)
def test_ambiguous_or_incomplete_real_evidence_keeps_recovery_pending(rig, problem):
    service, broker, _store = rig
    broker.failure = "lost_response"
    place(service)
    order = broker.orders["9007199254740993"]
    if problem == "similar":
        order["remark"] = "unrelated"
    elif problem == "duplicate":
        broker.orders["2"] = order | {"order_id": "2"}
    elif problem == "wrong_account":
        order["acc_id"] = 456
    else:
        del order["time_in_force"]
    result = service.real.reconcile("operation")
    assert result["state"] == "UNKNOWN_OUTCOME" and result["recovery"]["pending"]
    assert len(broker.calls) == 1


def test_conflicting_time_in_force_between_current_and_history_stays_pending(
    rig, monkeypatch
):
    service, broker, _store = rig
    broker.failure = "lost_response"
    place(service)
    order = broker.orders["9007199254740993"]
    monkeypatch.setattr(
        service,
        "get_history_orders",
        lambda **_kwargs: [order | {"time_in_force": "GTC"}],
    )
    result = service.real.reconcile("operation")
    assert result["state"] == "UNKNOWN_OUTCOME" and result["recovery"]["pending"]
    assert "time_in_force" in result["recovery_checks"][-1]["details"]["reason"]


def test_acknowledgement_persistence_failure_stays_visible_and_latches_gate(
    rig, monkeypatch
):
    service, broker, store = rig
    monkeypatch.setattr(
        store,
        "outcome",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExecutionStoreError("test disk failure")
        ),
    )
    first = place(service)
    assert first["state"] == "ACKNOWLEDGED" and first["broker_acknowledged"]
    assert first["submission_state"] == "observed" and first["order_id"]
    assert service.real.result(store.lookup("operation")) == first
    assert place(service) == first
    with pytest.raises(OrderNotSentError):
        place(service, token="new")
    assert (
        len(broker.calls) == 1 and service.real.health()["state"] == "JOURNAL_BLOCKED"
    )


def test_jit_unlock_refusal_and_acknowledged_relock_halt_are_independent(rig):
    service, broker, store = rig
    service.trade_password_md5 = "nonsecret-test-placeholder"
    broker.unlock_failure = True
    with pytest.raises(OrderNotSentError):
        place(service, "unlock-failed")
    assert store.lookup("unlock-failed")["marker"] == 0 and broker.calls == []
    broker.unlock_failure, broker.relock_failure = False, True
    result = place(service, "relock-failed")
    assert result["state"] == "ACKNOWLEDGED" and result["execution_halted"]
    assert result["gateway_relock_error"] and service.real.health()["state"] == "READY"
    with pytest.raises(OrderNotSentError):
        place(service, "halted")
    assert len(broker.calls) == 1


def test_cancel_racing_fill_accounts_current_state_without_success_claim(rig):
    service, broker, store = rig
    place(service)
    broker.failure = "lost_response"
    uncertain = service.cancel_order(
        "9007199254740993",
        trd_env="REAL",
        acc_id=123,
        operation_id="cancel",
        admission_epoch=store.epoch,
    )
    assert uncertain["state"] == "UNKNOWN_OUTCOME"
    result = service.real.reconcile("cancel")
    assert result["state"] == "TERMINAL_ACCOUNTED"
    assert result["accounted_facts"]["filled_quantity"] == "2"
    assert not result["accounted_facts"]["mutation_success_proven"]
    assert not result["broker_acknowledged"]
    assert len(broker.calls) == 2


def test_stale_acknowledged_modification_cannot_feed_a_new_patch(rig):
    service, broker, store = rig
    place(service)
    original = broker.orders["9007199254740993"].copy()
    first = service.modify_order(
        "9007199254740993",
        "NORMAL",
        price=155,
        trd_env="REAL",
        acc_id=123,
        operation_id="modify",
        admission_epoch=store.epoch,
    )
    assert first["state"] == "ACKNOWLEDGED"
    broker.stale = [original]
    with pytest.raises(OrderNotSentError) as error:
        service.modify_order(
            "9007199254740993",
            "NORMAL",
            qty=3,
            trd_env="REAL",
            acc_id=123,
            operation_id="stale-dependent",
            admission_epoch=store.epoch,
        )
    assert "not yet observed" in str(error.value)
    assert len(broker.calls) == 2
    assert store.lookup("modify")["modification_observed"] == 0
    broker.stale = None
    service.modify_order(
        "9007199254740993",
        "NORMAL",
        qty=3,
        trd_env="REAL",
        acc_id=123,
        operation_id="visible-dependent",
        admission_epoch=store.epoch,
    )
    assert store.lookup("modify")["modification_observed"] == 1
    assert broker.calls[-1][1]["price"] == 155


def test_restart_preserves_unknown_epoch_and_never_replays(rig):
    service, broker, store = rig
    broker.failure = "lost_response"
    original = place(service)
    original_epoch = store.epoch
    path = broker.path
    store.close()
    replacement = ExecutionStore(path, environment="REAL")
    try:
        resumed = TradeService(
            policy=service.policy,
            real_execution_store=replacement,
            dispatcher=service.dispatcher,
        )
        resumed.trade_ctx = cast(OpenSecTradeContext, broker)
        assert resumed.real is not None
        resumed.real.recovery.clock = broker.clock
        retry = place(resumed, admission_epoch=original_epoch)
        assert (
            retry["state"] == original["state"]
            and retry["admission_epoch"] == original_epoch
        )
        assert resumed.real.reconcile("operation")["state"] == "RECONCILED"
        assert len(broker.calls) == 1
    finally:
        replacement.close()


def test_unmarked_startup_admission_is_durably_not_dispatched(tmp_path):
    path = tmp_path / "real" / "journal.db"
    store = ExecutionStore(path, create=True, environment="REAL")
    store.review()
    store.admit("unmarked", store.epoch, 123, "CANCEL", "canonical", {"order_id": "1"})
    store.close()
    reopened = ExecutionStore(path, environment="REAL")
    try:
        service = TradeService(
            policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
            real_execution_store=reopened,
        )
        assert service.real is not None
        assert service.real.health()["state"] == "READY"
        row = reopened.lookup("unmarked")
        assert row is not None
        assert row["state"] == "REFUSED" and row["disposition"] == "NOT_SENT"
        assert row["recovery_disposition"] == "NOT_DISPATCHED"
    finally:
        reopened.close()


@pytest.mark.parametrize("window", ["admitted", "marked", "broker_returned"])
def test_real_process_crash_windows_are_never_automatically_replayed(tmp_path, window):
    path = tmp_path / "real" / "journal.db"
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from moomoo_mcp.services.execution_store import ExecutionStore
s = ExecutionStore(sys.argv[1], create=True, environment="REAL")
s.review()
s.admit("crashed", s.epoch, 123, "PLACE", "canonical", {"code":"US.AAPL"})
if sys.argv[2] != "admitted":
    s.mark_dispatch("crashed", {"code":"US.AAPL"})
if sys.argv[2] == "broker_returned":
    with open(sys.argv[1] + ".accepted", "w") as evidence:
        evidence.write("one broker invocation accepted")
os._exit(73)
""",
            str(path),
            window,
        ],
        timeout=10,
    )
    assert child.returncode == 73
    for _ in range(2):
        store = ExecutionStore(path, environment="REAL")
        try:
            service = TradeService(
                policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
                real_execution_store=store,
            )
            assert service.real is not None
            row = store.lookup("crashed")
            assert row is not None
            assert row["state"] == (
                "REFUSED" if window == "admitted" else "UNKNOWN_OUTCOME"
            )
            assert service.real.health()["state"] == (
                "READY" if window == "admitted" else "JOURNAL_BLOCKED"
            )
            assert service.trade_ctx is None
        finally:
            store.close()


def test_process_owned_worker_accounts_without_a_client_reconcile_call(rig):
    service, broker, store = rig
    broker.failure = "lost_response"
    place(service)
    service.real.recovery.start()
    deadline = time.monotonic() + 3
    while (
        store.lookup("operation")["state"] != "RECONCILED"
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert store.lookup("operation")["state"] == "RECONCILED"
    assert len(broker.calls) == 1


def test_signed_combo_fill_and_each_leg_position_can_be_accounted(rig):
    service, broker, store = rig
    broker.failure = "lost_response"
    result = service.place_combo_order(
        [
            {"code": "US.AAPL260116C150000", "trd_side": "SELL", "qty_ratio": 1},
            {"code": "US.AAPL260116C155000", "trd_side": "BUY", "qty_ratio": 1},
        ],
        price=-1.2,
        qty=2,
        trd_env="REAL",
        acc_id=123,
        operation_id="credit",
        admission_epoch=store.epoch,
    )
    assert result["state"] == "UNKNOWN_OUTCOME"
    broker.orders["9007199254740993"].update(
        order_status="FILLED_ALL",
        dealt_qty=2,
        dealt_avg_price=-1.2,
    )
    result = service.real.reconcile("credit")
    assert result["state"] == "RECONCILED"
    assert result["accounted_facts"]["average_fill_price"] == "-1.2"
    assert len(result["accounted_facts"]["current_positions"]) == 2
    assert len(broker.calls) == 1


def test_combo_recovery_normalizes_dispatch_evidence_and_retains_caller_identity(rig):
    service, broker, store = rig
    broker.failure = "lost_response"
    legs = [
        {
            "code": " US.AAPL260116C150000 ",
            "trd_side": " buy ",
            "qty_ratio": 1,
            "position_id": " 000123 ",
        },
        {"code": "US.AAPL260116C155000", "trd_side": "sell", "qty_ratio": 1},
    ]
    uncertain = service.place_combo_order(
        legs,
        price=1.2,
        qty=2,
        trd_env="REAL",
        acc_id=123,
        operation_id="normalized-combo",
        admission_epoch=store.epoch,
    )
    assert uncertain["state"] == "UNKNOWN_OUTCOME"
    request = json.loads(store.lookup("normalized-combo")["request"])
    assert request["combo_legs"] == legs
    broker.orders["9007199254740993"]["combo_legs"][0].update(
        qty_ratio=1.0,
        position_id="123",
    )
    result = service.real.reconcile("normalized-combo")
    assert result["state"] == "RECONCILED"
    assert result["accounted_facts"]["current_positions"] == {
        "US.AAPL260116C150000": "4",
        "US.AAPL260116C155000": "4",
    }
    assert json.loads(store.lookup("normalized-combo")["request"]) == request
    assert len(broker.calls) == 1


def test_duplicate_exact_target_rows_refuse_before_a_real_modification(rig):
    service, broker, store = rig
    place(service)
    target = broker.orders["9007199254740993"]
    broker.stale = [target, target | {"qty": 3}]
    with pytest.raises(OrderNotSentError) as error:
        service.modify_order(
            "9007199254740993",
            "NORMAL",
            price=151,
            trd_env="REAL",
            acc_id=123,
            operation_id="ambiguous-target",
            admission_epoch=store.epoch,
        )
    assert "ambiguous evidence" in str(error.value)
    row = store.lookup("ambiguous-target")
    assert row["state"] == "REFUSED" and row["marker"] == 0
    assert len(broker.calls) == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"order_id": "not-an-order"},
        {"order_id": "0"},
        {"order_id": "１２３"},
        {"modify_order_op": "unknown"},
        {"qty": 0},
        {"qty": -1},
        {"qty": True},
        {"qty": 1.5},
        {"price": -1},
        {"price": "151"},
        {"price": float("nan")},
        {"price": float("inf")},
    ],
)
def test_invalid_modification_admission_uses_no_broker_io_or_quota(rig, patch):
    service, broker, store = rig
    params: dict[str, Any] = {
        "order_id": "9007199254740993",
        "modify_order_op": "NORMAL",
        "qty": None,
        "price": None,
        "adjust_limit": 0,
        "trd_env": "REAL",
        "acc_id": "0",
        "operation_id": "invalid-modification",
        "admission_epoch": store.epoch,
    } | patch
    with pytest.raises(OrderNotSentError):
        service.prepare_admission("modify_order", params)
    assert broker.calls == [] and broker.reads == []
    assert service.dispatcher._limiters == {}
    assert store.lookup("invalid-modification") is None


@pytest.mark.parametrize("operation", ["cancel", "disable", "delete"])
def test_reducing_modify_defaults_dispatch_zeros_and_keep_original_none(rig, operation):
    service, broker, store = rig
    place(service)
    result = service.modify_order(
        "9007199254740993",
        operation,
        trd_env="REAL",
        acc_id=123,
        operation_id="reduce",
        admission_epoch=store.epoch,
    )
    assert result["state"] == "ACKNOWLEDGED"
    sent = broker.calls[-1][1]
    assert sent["modify_order_op"] == operation.upper()
    assert sent["qty"] == 0 and sent["price"] == 0
    original = json.loads(store.lookup("reduce")["request"])
    assert original["modify_order_op"] == operation
    assert original["qty"] is None and original["price"] is None
