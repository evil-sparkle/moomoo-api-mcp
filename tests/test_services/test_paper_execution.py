"""Paper journal acceptance using stateful broker doubles and actual SQLite."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher, QuotaRequest
from moomoo_mcp.services.execution_store import ExecutionConflict, ExecutionStore
from moomoo_mcp.services.order_errors import OrderNotSentError
from moomoo_mcp.services.paper_execution import PaperExecution
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    TradingMode,
    TradingPolicy,
)
from tests.rate_limit_clock import FakeClock


class Broker:
    def __init__(self, path):
        self.path = path
        self.clock = FakeClock()
        self.calls = []
        self.orders = {}
        self.failure = None
        self.entered = None
        self.release = None

    def _call(self, kind, request):
        # A second connection can obtain the writer lock throughout gateway IO.
        # Another admission thread may hold a short transaction concurrently.
        # A transaction spanning this gateway call would still time out here.
        with sqlite3.connect(self.path, timeout=0.5) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, marker FROM operations ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            assert row == ("DISPATCHING", 1)
            conn.rollback()
        self.calls.append((kind, request.copy()))
        # Model time taken by gateway IO, beyond both mutation spacing limits.
        self.clock.advance(0.05)
        if self.entered:
            self.entered.set()
            assert self.release is not None
            assert self.release.wait(5)
        if self.failure == "timeout":
            raise TimeoutError("response lost")
        if self.failure == "error":
            return RET_ERROR, "gateway error"
        order_id = request.get("order_id", "9007199254740993")
        current = self.orders.get(order_id, {}).copy()
        current.update(request)
        current.update(
            order_id=order_id, order_status="SUBMITTED", dealt_qty=0, dealt_avg_price=0
        )
        if kind == "CANCEL":
            current["order_status"] = "FILLED_ALL"
            current["dealt_qty"] = current.get("qty", 2)
        self.orders[order_id] = current
        if self.failure == "lost_response":
            raise TimeoutError("response lost after acceptance")
        return RET_OK, pd.DataFrame([current])

    def place_order(self, **request):
        return self._call("PLACE", request)

    def modify_order(self, **request):
        return self._call(
            "CANCEL" if request["modify_order_op"] == "CANCEL" else "MODIFY", request
        )

    def order_list_query(self, **_request):
        return RET_OK, pd.DataFrame(list(self.orders.values()))

    def history_order_list_query(self, **_request):
        return RET_OK, pd.DataFrame(list(self.orders.values()))


class Service:
    def __init__(self, broker):
        self.trade_ctx = broker
        self.dispatcher = BrokerRequestDispatcher(clock=lambda: self.trade_ctx.clock())
        self.policy = TradingPolicy(TradingMode.SIMULATE)
        self.accounts = [
            {"acc_id": 123, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]}
        ]
        self.account_reads = 0
        self.target_reads = 0
        self.position = 2
        self.instrument_lookup = lambda codes: [
            InstrumentFacts(code=code, classification="STOCK") for code in codes
        ]

    @staticmethod
    def _exact_account_id(value, *, allow_zero):
        assert allow_zero or int(value) != 0
        return int(value)

    def get_accounts(self):
        self.account_reads += 1
        return self.accounts

    def _fetch_order(self, _operation, order_id, _env, _account):
        self.target_reads += 1
        return self.trade_ctx.orders[order_id].copy()

    @staticmethod
    def _first_record(_kind, data):
        return data.iloc[0].to_dict()

    def get_orders(self, **request):
        ret, data = self.trade_ctx.order_list_query(**request)
        if ret != RET_OK:
            raise RuntimeError("order_list_query failed")
        return data.to_dict("records")

    def get_history_orders(self, **request):
        ret, data = self.trade_ctx.history_order_list_query(**request)
        if ret != RET_OK:
            raise RuntimeError("history_order_list_query failed")
        return data.to_dict("records")

    def get_positions(self, **request):
        return [{"code": request["code"], "qty": self.position}]


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    broker = Broker(path)
    service = Service(broker)
    paper = PaperExecution(
        cast(TradeService, cast(object, service)), store, frozenset({123, 456})
    )
    yield paper, service, broker, store
    paper.recovery.stop()
    store.close()


def place(paper, token="place", **patch: Any):
    params = {
        "code": "US.AAPL",
        "qty": 2,
        "price": "1.10",
        "trd_side": "BUY",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
    }
    params.update(patch)
    return paper.execute(
        "PLACE",
        params,
        operation_id=token,
        admission_epoch=paper.store.epoch,
        acc_id="0",
    )


def target(broker):
    broker.orders["77"] = {
        "order_id": "77",
        "code": "US.AAPL",
        "qty": 2,
        "price": 1.1,
        "trd_side": "BUY",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "fill_outside_rth": False,
        "session": "RTH",
        "order_status": "SUBMITTED",
        "dealt_qty": 0,
        "dealt_avg_price": 0,
    }


def mutate(paper, token="modify", kind="MODIFY", **patch):
    return paper.execute(
        kind,
        dict(order_id="77", **patch),
        operation_id=token,
        admission_epoch=paper.store.epoch,
        acc_id="0",
    )


def test_duplicate_decimal_identity_frozen_account_and_conflicts(rig):
    paper, service, broker, store = rig
    first = place(paper)
    assert first["state"] == "ACKNOWLEDGED"
    assert first["broker_order_id"] == "9007199254740993"
    assert first["submission_state"] == "durably_stored"
    service.accounts = []
    assert place(paper, price="1.100") == first
    assert service.account_reads == 1
    assert len(broker.calls) == 1
    assert store.lookup("place")["account"] == "123"
    patches: list[dict[str, Any]] = [{"price": "1.11"}, {"qty": 3}]
    for patch in patches:
        with pytest.raises(ExecutionConflict) as caught:
            place(paper, **patch)
        assert "conflict" in str(caught.value).lower()
    assert len(broker.calls) == 1


def test_inflight_retry_is_immediate_and_does_not_dispatch(rig):
    paper, _, broker, _ = rig
    broker.entered, broker.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(place, paper)
        assert broker.entered.wait(3)
        try:
            retry = pool.submit(place, paper).result(timeout=1)
            assert retry["status"] == "IN_FLIGHT"
            assert len(broker.calls) == 1
            health = paper.health()
            assert health["state"] == "READY"
            assert health["in_flight"] == 1
            assert health["awaiting_review"] == health["blocking"] == 0
        finally:
            broker.release.set()
        assert future.result(timeout=3)["state"] == "ACKNOWLEDGED"


def test_mutations_read_and_merge_inside_serialized_region(rig):
    paper, service, broker, store = rig
    target(broker)
    broker.entered, broker.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mutate, paper, "quantity", qty=5)
        assert broker.entered.wait(3)
        second = pool.submit(mutate, paper, "price", price="2.20")
        broker.release.set()
        assert first.result(timeout=3)["state"] == "ACKNOWLEDGED"
        assert second.result(timeout=3)["state"] == "ACKNOWLEDGED"
    assert broker.calls[1][1]["qty"] == 5
    assert service.target_reads == 2
    assert json.loads(store.lookup("price")["request"]) == {
        "order_id": "77",
        "price": "2.20",
    }
    assert json.loads(store.lookup("price")["merged_request"])["qty"] == 5
    broker.orders["77"]["qty"] = 9
    mutate(paper, "price", price="2.200")
    assert len(broker.calls) == 2
    assert service.target_reads == 2


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_uncertain_response_blocks_new_mutations_and_never_replays(rig, failure):
    paper, _, broker, store = rig
    broker.failure = failure
    result = place(paper)
    assert result["state"] == "UNKNOWN_OUTCOME"
    assert result["disposition"] == "POSSIBLY_SENT"
    assert place(paper) == result
    assert store.health()["state"] == "JOURNAL_BLOCKED"
    with pytest.raises(OrderNotSentError):
        place(paper, "second")
    with pytest.raises(OrderNotSentError):
        mutate(paper, "cancel", "CANCEL")
    assert len(broker.calls) == 1


def test_predispatch_refusal_has_no_dispatch_marker(rig):
    paper, service, broker, store = rig
    service.instrument_lookup = lambda _codes: []
    with pytest.raises(OrderNotSentError):
        place(paper)
    row = store.lookup("place")
    assert row["state"] == "REFUSED"
    assert row["disposition"] == "NOT_SENT"
    assert row["marker"] == 0
    assert broker.calls == []


def test_unreadable_receipt_preserves_acknowledgement_and_identifier(rig):
    paper, service, broker, store = rig

    def fail(_kind, _data):
        raise ValueError("conversion failed")

    service._first_record = fail
    result = place(paper)
    assert result["state"] == "ACKNOWLEDGED"
    assert result["broker_order_id"] == "9007199254740993"
    assert "RECEIPT_UNREADABLE" in store.health()["blocking_reasons"]
    assert place(paper) == result
    assert len(broker.calls) == 1


def test_lost_outcome_write_preserves_observed_ack_and_blocks(rig, monkeypatch):
    paper, _, broker, store = rig

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "outcome", fail)
    result = place(paper)
    assert result["submission_state"] == "observed"
    assert result["broker_acknowledged"] is True
    assert result["receipt"]["order_id"] == "9007199254740993"
    assert paper.health()["state"] == "JOURNAL_BLOCKED"
    assert place(paper) == result
    assert len(broker.calls) == 1


@pytest.mark.parametrize("kind", ["PLACE", "MODIFY", "CANCEL"])
def test_reconciliation_does_not_infer_success_from_candidates_or_target(rig, kind):
    paper, _, broker, store = rig
    target(broker)
    broker.failure = "timeout"
    result = (
        place(paper)
        if kind == "PLACE"
        else mutate(paper, kind=kind, **({"price": "2.0"} if kind == "MODIFY" else {}))
    )
    token = result["operation_id"]
    before = len(broker.calls)
    assert paper.reconcile(token)["state"] == "UNKNOWN_OUTCOME"
    broker.orders.clear()
    assert paper.reconcile(token)["state"] == "UNKNOWN_OUTCOME"
    assert len(broker.calls) == before
    assert store.health()["state"] == "JOURNAL_BLOCKED"


def test_automatic_terminal_accounting_preserves_exposure_without_success_claim(rig):
    paper, service, broker, store = rig
    target(broker)
    broker.failure = "timeout"
    mutate(paper, price="2.0")
    broker.orders["77"].update(
        order_status="FILLED_ALL", dealt_qty=2, dealt_avg_price=1.1
    )
    service.position = 8
    result = paper.reconcile("modify")
    assert result["state"] == "TERMINAL_ACCOUNTED"
    assert result["accounted_facts"]["current_symbol_position"] == "8"
    assert result["accounted_facts"]["mutation_success_proven"] is False
    assert paper.health()["state"] == "READY"
    with sqlite3.connect(broker.path) as conn:
        audit = conn.execute(
            "SELECT operator_id, resolution, previous_state FROM recovery_audit"
        ).fetchone()
    assert audit == ("system", "TERMINAL_ACCOUNTED", "UNKNOWN_OUTCOME")


@pytest.mark.parametrize(
    "patch",
    [
        {"price": 1.1},
        {"qty": 1.5},
        {"order_type": "MARKET"},
        {"order_type": "STOP"},
        {"order_type": "TRAILING_STOP"},
        {"time_in_force": "GTC"},
        {"code": "HK.00700"},
    ],
)
def test_unsupported_requests_never_reach_broker(rig, patch):
    paper, _, broker, store = rig
    with pytest.raises(OrderNotSentError):
        place(paper, **patch)
    assert broker.calls == []
    assert store.lookup("place") is None


def test_paper_journal_identity_survives_real_mode_restart(rig):
    paper, service, broker, store = rig
    first = place(paper)
    old_epoch = store.epoch
    store.close()
    restarted = ExecutionStore(broker.path)
    try:
        service.policy = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({456}))
        real_deployment = PaperExecution(
            cast(TradeService, cast(object, service)), restarted, frozenset({123})
        )
        accounts = service.accounts
        service.accounts = []
        row = restarted.lookup("place")
        assert row is not None
        retry = real_deployment.execute(
            "PLACE",
            json.loads(row["request"]),
            operation_id="place",
            admission_epoch=old_epoch,
            acc_id="0",
        )
        assert retry == first
        assert len(broker.calls) == 1
        service.accounts = accounts
        second = place(real_deployment, token="second")
        assert second["state"] == "ACKNOWLEDGED"
        assert len(broker.calls) == 2
    finally:
        restarted.close()

    reopened = ExecutionStore(broker.path)
    try:
        service.policy = TradingPolicy(TradingMode.SIMULATE)
        simulate_again = PaperExecution(
            cast(TradeService, cast(object, service)), reopened, frozenset({123})
        )
        assert place(simulate_again, token="second") == second
        assert place(simulate_again) == first
        assert len(broker.calls) == 2
    finally:
        reopened.close()


def test_reconciliation_with_reliable_id_releases_dispatch_recovery_gate(rig):
    paper, _, broker, store = rig
    broker.failure = "timeout"
    place(paper)
    store.outcome(
        "place",
        "UNKNOWN_OUTCOME",
        "POSSIBLY_SENT",
        receipt={"order_id": "77"},
        reason="RECOVERED_DISPATCH",
    )
    target(broker)
    broker.orders["77"]["remark"] = store.lookup("place")["order_tag"]
    assert paper.reconcile("place")["state"] == "RECONCILED"
    assert store.health()["state"] == "READY"
    assert store.health()["blocking_reasons"] == []
    assert len(broker.calls) == 1


def test_cancellation_racing_fill_reports_fill_as_broker_fact(rig):
    paper, _, broker, _ = rig
    target(broker)
    result = mutate(paper, "cancel", "CANCEL")
    assert result["state"] == "ACKNOWLEDGED"
    assert result["broker_status"] == "FILLED_ALL"
    assert result["broker_status"] != "CANCELLED_ALL"
    assert mutate(paper, "cancel", "CANCEL") == result
    assert len(broker.calls) == 1


def test_concurrent_same_id_admission_after_both_lookups_miss(rig, monkeypatch):
    paper, _, broker, store = rig
    original_lookup = store.lookup
    barrier = threading.Barrier(2)
    seen = threading.local()

    def racing_lookup(token):
        row = original_lookup(token)
        if not getattr(seen, "looked_up", False):
            seen.looked_up = True
            assert row is None
            barrier.wait(timeout=3)
        return row

    monkeypatch.setattr(store, "lookup", racing_lookup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(place, paper) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert len(broker.calls) == 1
    assert all(
        result["state"] in {"ACKNOWLEDGED", "ADMITTED", "DISPATCHING"}
        for result in results
    )
    assert original_lookup("place")["state"] == "ACKNOWLEDGED"


def test_known_placeholder_retry_ignores_changed_allowlist(rig):
    paper, service, broker, _ = rig
    first = place(paper)
    paper.allowlist = frozenset({456})
    service.accounts = [
        {"acc_id": 456, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]}
    ]
    assert place(paper) == first
    assert service.account_reads == 1
    assert len(broker.calls) == 1


def test_unallowlisted_explicit_account_refused_before_discovery(rig):
    paper, service, broker, store = rig
    with pytest.raises(OrderNotSentError):
        paper.execute(
            "CANCEL",
            {"order_id": "77"},
            operation_id="cancel",
            admission_epoch=store.epoch,
            acc_id="999",
        )
    assert service.account_reads == 0
    assert broker.calls == []
    assert store.lookup("cancel") is None


def test_decimal_precision_survives_storage_and_detects_last_digit_conflict(rig):
    paper, _, broker, store = rig
    price = "1.100000000000000000000000000000000000001"
    first = place(paper, price=price)
    row = store.lookup("place")
    assert json.loads(row["request"])["price"] == price
    assert json.loads(row["merged_request"])["price"] == price
    assert broker.calls[0][1]["price"] == price
    assert place(paper, price=price + "0") == first
    with pytest.raises(ExecutionConflict):
        place(paper, price=price[:-1] + "2")


def test_contradictory_exact_identity_observations_remain_unresolved(rig, monkeypatch):
    paper, _, broker, store = rig
    broker.failure = "timeout"
    place(paper)
    store.outcome(
        "place",
        "UNKNOWN_OUTCOME",
        "POSSIBLY_SENT",
        receipt={"order_id": "77"},
        reason="UNRESOLVED_OUTCOME",
    )
    target(broker)
    current = broker.orders["77"].copy()
    conflicting = current | {"order_status": "FILLED_ALL", "dealt_qty": 2}
    monkeypatch.setattr(
        broker,
        "history_order_list_query",
        lambda **_kwargs: (RET_OK, pd.DataFrame([conflicting])),
    )
    result = paper.reconcile("place")
    assert result["state"] == "UNKNOWN_OUTCOME"
    assert paper.health()["state"] == "JOURNAL_BLOCKED"
    assert len(json.loads(store.lookup("place")["evidence"])) == 2
    assert len(broker.calls) == 1


def test_attribute_identical_candidates_do_not_establish_placement_ownership(rig):
    paper, _, broker, store = rig
    broker.failure = "timeout"
    place(paper)
    row = store.lookup("place")
    candidate = json.loads(row["merged_request"]) | {
        "remark": "unrelated-order",
        "order_id": "1234",
        "order_status": "SUBMITTED",
    }
    broker.orders["1234"] = candidate
    assert paper.reconcile("place")["state"] == "UNKNOWN_OUTCOME"
    assert store.health()["state"] == "JOURNAL_BLOCKED"
    assert len(broker.calls) == 1


def test_successful_outcome_commit_followed_by_lookup_failure_is_durable(
    rig, monkeypatch
):
    paper, _, _, store = rig
    original_outcome = store.outcome
    original_lookup = store.lookup
    committed = False

    def outcome(*args, **kwargs):
        nonlocal committed
        original_outcome(*args, **kwargs)
        committed = True

    def lookup(token):
        if committed:
            raise OSError("read failed after successful commit")
        return original_lookup(token)

    monkeypatch.setattr(store, "outcome", outcome)
    monkeypatch.setattr(store, "lookup", lookup)
    result = place(paper)
    assert result["state"] == "ACKNOWLEDGED"
    assert result["submission_state"] == "durably_stored"
    assert original_lookup("place")["state"] == "ACKNOWLEDGED"


def test_failed_outcome_write_survives_two_process_restarts_and_recovers_automatically(
    tmp_path,
):
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "journal.db"
    helpers = str(Path(__file__).parent)
    crash = """
import os, sys
sys.path.insert(0, sys.argv[2])
from test_paper_execution import Broker, Service, target, mutate
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.paper_execution import PaperExecution
s = ExecutionStore(sys.argv[1], create=True)
b = Broker(sys.argv[1])
target(b)
p = PaperExecution(Service(b), s, frozenset({123}))
def fail(*args, **kwargs):
    raise OSError("injected outcome persistence failure")
s.outcome = fail
result = mutate(p, price="2.0")
assert result["submission_state"] == "observed"
assert len(b.calls) == 1
assert s.lookup("modify")["state"] == "DISPATCHING"
os._exit(73)
"""
    first = subprocess.run(
        [sys.executable, "-c", crash, str(path), helpers], timeout=10
    )
    assert first.returncode == 73
    restart = """
import sys
sys.path.insert(0, sys.argv[2])
from test_paper_execution import Broker, Service, target
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.paper_execution import PaperExecution
s = ExecutionStore(sys.argv[1])
b = Broker(sys.argv[1])
target(b)
b.orders["77"].update(order_status="FILLED_ALL", dealt_qty=2, dealt_avg_price=1.1)
p = PaperExecution(Service(b), s, frozenset({123}))
assert s.lookup("modify")["state"] == "UNKNOWN_OUTCOME"
assert "RECOVERED_DISPATCH" in p.health()["blocking_reasons"]
assert p.health()["state"] == "JOURNAL_BLOCKED"
assert b.calls == []
s.close()
"""
    for _ in range(2):
        child = subprocess.run(
            [sys.executable, "-c", restart, str(path), helpers],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode == 0, child.stderr
    store = ExecutionStore(path)
    try:
        broker = Broker(path)
        target(broker)
        broker.orders["77"].update(
            order_status="FILLED_ALL", dealt_qty=2, dealt_avg_price=1.1
        )
        paper = PaperExecution(
            cast(TradeService, cast(object, Service(broker))), store, frozenset({123})
        )
        assert paper.health()["state"] == "JOURNAL_BLOCKED"
        result = paper.reconcile("modify")
        assert result["state"] == "TERMINAL_ACCOUNTED"
        assert paper.health()["state"] == "READY"
        assert broker.calls == []
    finally:
        store.close()


def test_retry_with_unreadable_journal_does_not_claim_prior_order_was_not_sent(rig):
    paper, _, broker, store = rig
    assert place(paper)["state"] == "ACKNOWLEDGED"
    store._failed = "STORAGE_FAILED: injected read failure"
    with pytest.raises(RuntimeError) as caught:
        place(paper)
    assert not isinstance(caught.value, OrderNotSentError)
    assert "so no order was sent" not in str(caught.value)
    assert len(broker.calls) == 1


def test_cached_acknowledgement_survives_storage_latch_and_checks_identity(
    rig, monkeypatch
):
    paper, _, broker, store = rig

    def fail(*_args, **_kwargs):
        store._failed = "STORAGE_FAILED: injected outcome failure"
        raise OSError("disk full")

    monkeypatch.setattr(store, "outcome", fail)
    first = place(paper)
    assert first["broker_acknowledged"] is True
    assert first["submission_state"] == "observed"
    assert place(paper, price="1.100") == first
    with pytest.raises((ValueError, RuntimeError)) as caught:
        place(paper, price="1.11")
    assert "conflict" in str(caught.value).lower()
    assert len(broker.calls) == 1


@pytest.mark.parametrize(
    "conflict",
    [{"dealt_avg_price": 9.9}, {"code": "US.MSFT"}, {"qty": 3}, {"trd_side": "SELL"}],
)
def test_operator_accounting_rejects_disagreement_in_broker_facts(
    rig, monkeypatch, conflict
):
    paper, _, broker, store = rig
    target(broker)
    broker.failure = "timeout"
    mutate(paper, price="2.0")
    broker.orders["77"].update(
        order_status="FILLED_ALL", dealt_qty=2, dealt_avg_price=1.1
    )
    conflicting = broker.orders["77"] | conflict
    monkeypatch.setattr(
        broker,
        "history_order_list_query",
        lambda **_kwargs: (RET_OK, pd.DataFrame([conflicting])),
    )
    assert paper.reconcile("modify")["state"] == "UNKNOWN_OUTCOME"
    assert store.lookup("modify")["state"] == "UNKNOWN_OUTCOME"
    assert paper.health()["state"] == "JOURNAL_BLOCKED"
    with sqlite3.connect(broker.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recovery_audit").fetchone() == (0,)


@pytest.mark.parametrize("token", [None, "", " ", "a" * 65, "line\nbreak"])
def test_missing_or_malformed_tokens_are_not_rewritten(rig, token):
    paper, service, broker, store = rig
    with pytest.raises(OrderNotSentError):
        place(paper, token=token)
    assert broker.calls == []
    assert service.account_reads == 0
    assert store.health()["state"] == "READY"


@pytest.mark.parametrize("price", ["", " 1", "1 ", "1e2", ".5", "1.", "-1", "NaN", "0"])
def test_decimal_schema_refuses_lossy_or_malformed_values(rig, price):
    paper, service, broker, _ = rig
    with pytest.raises(OrderNotSentError):
        place(paper, price=price)
    assert broker.calls == []
    assert service.account_reads == 0


@pytest.mark.parametrize(
    "accounts",
    [
        [],
        [
            {"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]},
        ],
        [
            {"acc_id": 123, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
            {"acc_id": 456, "trd_env": "SIMULATE", "trdmarket_auth": ["US"]},
        ],
    ],
)
def test_unverifiable_or_ambiguous_paper_account_never_admits(rig, accounts):
    paper, service, broker, store = rig
    service.accounts = accounts
    with pytest.raises(OrderNotSentError):
        place(paper)
    assert broker.calls == []
    assert store.lookup("place") is None


def test_decimal_notional_just_above_cap_is_not_rounded_down(rig):
    paper, service, broker, store = rig
    service.policy = TradingPolicy(TradingMode.SIMULATE, max_order_notional={"USD": 2})
    with pytest.raises(OrderNotSentError):
        place(paper, price="1.00000000000000000000000000000001")
    assert broker.calls == []
    row = store.lookup("place")
    assert row is not None and row["state"] == "REFUSED" and row["marker"] == 0


def test_quota_refusal_does_not_admit_or_mark_a_paper_write(rig):
    paper, service, broker, store = rig
    held = service.dispatcher.limiter(QuotaRequest("place_order", 123)).reserve()
    try:
        with pytest.raises(OrderNotSentError, match="order_placement rate limit"):
            place(paper)
        assert store.lookup("place") is None
        assert broker.calls == []
    finally:
        held.release()


class DelayedObservationBroker(Broker):
    """Acknowledge a modification before publishing its new order fields."""

    def __init__(self, path):
        super().__init__(path)
        self.pending_observations = {}

    def modify_order(self, **request):
        if request["modify_order_op"] != "NORMAL":
            return super().modify_order(**request)
        order_id = request["order_id"]
        before = self.orders[order_id].copy()
        result, _ = super().modify_order(**request)
        assert result == RET_OK
        self.pending_observations[order_id] = self.orders[order_id].copy()
        self.orders[order_id] = before
        return result, pd.DataFrame([{"order_id": order_id}])

    def publish(self, order_id):
        self.orders[order_id] = self.pending_observations.pop(order_id)


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize(
    "first_patch,next_patch",
    [
        ({"qty": 50}, {"price": "80"}),
        ({"price": "80"}, {"qty": 50}),
    ],
)
def test_acknowledgement_does_not_authorize_merge_from_old_order_fields(
    rig, restart, first_patch, next_patch
):
    paper, service, original, store = rig
    broker = DelayedObservationBroker(original.path)
    service.trade_ctx = broker
    target(broker)
    broker.orders["77"].update(qty=100, price=50)
    first = mutate(paper, "first-change", **first_patch)
    assert first["state"] == "ACKNOWLEDGED"
    assert first["receipt"] == {"order_id": "77"}
    assert first["modification_observed"] is False
    assert broker.orders["77"]["qty"] == 100
    assert broker.orders["77"]["price"] == 50
    if restart:
        store.close()
        store = ExecutionStore(broker.path)
        paper = PaperExecution(
            cast(TradeService, cast(object, service)), store, frozenset({123})
        )
    try:
        with pytest.raises(OrderNotSentError) as refusal:
            mutate(paper, "dependent-refused", **next_patch)
        assert "not yet observed" in str(refusal.value)
        assert len(broker.calls) == 1
        refused = store.lookup("dependent-refused")
        assert refused is not None
        assert refused["state"] == "REFUSED" and refused["marker"] == 0
        # Retrying the acknowledged operation never dispatches or claims visibility.
        assert mutate(paper, "first-change", **first_patch) == first
        broker.publish("77")
        # A REFUSED token remains refused. A newly authorized intent uses a new ID.
        assert mutate(paper, "dependent-refused", **next_patch)["state"] == "REFUSED"
        result = mutate(paper, "dependent-after-observation", **next_patch)
        assert result["state"] == "ACKNOWLEDGED"
        assert len(broker.calls) == 2
        assert broker.calls[-1][1]["qty"] == 50
        assert broker.calls[-1][1]["price"] == "80"
        previous = store.lookup("first-change")
        assert previous is not None and previous["modification_observed"] == 1
    finally:
        store.close()


def test_pending_visibility_does_not_prevent_individual_cancellation(rig):
    paper, service, original, _ = rig
    broker = DelayedObservationBroker(original.path)
    service.trade_ctx = broker
    target(broker)
    mutate(paper, "quantity-change", qty=1)
    result = mutate(paper, "cancel", "CANCEL")
    assert result["state"] == "ACKNOWLEDGED"
    assert len(broker.calls) == 2
    assert broker.calls[-1][0] == "CANCEL"


def test_failed_successor_preparation_does_not_retire_prior_visibility_guard(rig):
    paper, service, original, store = rig
    broker = DelayedObservationBroker(original.path)
    service.trade_ctx = broker
    target(broker)
    broker.orders["77"].update(qty=100, price=50)
    mutate(paper, "reduce", qty=50)
    broker.publish("77")
    classify = service.instrument_lookup
    service.instrument_lookup = lambda _codes: []
    with pytest.raises(OrderNotSentError):
        mutate(paper, "bad-instrument", price="80")
    assert len(store.unobserved_modifications(123, "00077")) == 1
    service.instrument_lookup = classify
    # A subsequent read may regress; no acknowledged successor replaced the guard.
    broker.orders["77"]["qty"] = 100
    with pytest.raises(OrderNotSentError) as refusal:
        mutate(paper, "stale-again", price="80")
    assert "not yet observed" in str(refusal.value)
    assert len(broker.calls) == 1


def recovery_clock(paper, initial=100.0):
    now = [initial]
    paper.recovery.clock = lambda: now[0]
    return now


def next_round(paper, now, token="place"):
    row = paper.store.lookup(token)
    assert row is not None
    now[0] = row["next_recovery_at"]
    return paper.reconcile(token)


def test_persisted_unique_tags_and_original_remark_identity(rig):
    paper, _, broker, store = rig
    first = place(paper, remark="caller note")
    row = store.lookup("place")
    assert row is not None
    assert first["order_tag"] == broker.calls[0][1]["remark"] == row["order_tag"]
    assert len(first["order_tag"].encode("utf-8")) <= 64
    assert first["order_tag"] != first["operation_id"]
    assert json.loads(row["request"])["remark"] == "caller note"
    assert place(paper, remark="caller note") == first
    with pytest.raises(ExecutionConflict):
        place(paper, remark="different note")
    second = place(paper, token="second")
    assert second["order_tag"] != first["order_tag"]
    target(broker)
    modification = mutate(paper, price="2.0")
    assert modification["order_tag"] is None


def test_lost_response_recovers_by_tag_and_deduplicates_current_history(rig):
    paper, _, broker, store = rig
    broker.failure = "lost_response"
    first = place(paper)
    assert first["broker_order_id"] is None
    recovered = paper.reconcile("place")
    assert recovered["state"] == "RECONCILED"
    assert recovered["broker_order_id"] == "9007199254740993"
    assert recovered["broker_status"] == "SUBMITTED"
    assert recovered["recovery"]["disposition"] == "BROKER_CONFIRMED"
    assert recovered["accounted_facts"]["remaining_executable_quantity"] == "2"
    assert store.health()["state"] == "READY"
    assert len(broker.calls) == 1
    assert place(paper) == recovered


def test_five_successful_negatives_release_gate_without_resending(rig):
    from moomoo_mcp.services.execution_store import ASSUMED_ABSENT

    paper, _, broker, store = rig
    now = recovery_clock(paper)
    broker.failure = "timeout"
    place(paper)
    first = paper.reconcile("place")
    assert first["recovery"]["successful_negative_checks"] == 1
    assert first["recovery"]["next_check_at"] == 105
    assert paper.reconcile("place") == first  # A client cannot accelerate the schedule.
    for expected_time in (105, 110, 120, 140):
        result = next_round(paper, now)
        assert result["recovery_checks"][-1]["completed_at"] == expected_time
    assert result["state"] == "UNKNOWN_OUTCOME"
    assert result["disposition"] == "POSSIBLY_SENT"
    assert result["recovery"]["disposition"] == ASSUMED_ABSENT
    assert result["recovery"]["absence_proven"] is False
    assert store.health()["state"] == "READY"
    assert place(paper) == result
    assert len(broker.calls) == 1
    broker.failure = None
    fresh = place(paper, token="separate-decision")
    assert fresh["state"] == "ACKNOWLEDGED"
    assert fresh["recovery_updates"][0]["disposition"] == ASSUMED_ABSENT
    assert len(broker.calls) == 2


def test_queued_reads_use_actual_times_and_errors_do_not_count(rig, monkeypatch):
    paper, service, broker, _ = rig
    now = recovery_clock(paper)
    broker.failure = "timeout"
    place(paper)
    original = service.get_history_orders
    calls = []

    def history(**kwargs):
        calls.append(kwargs)
        now[0] += 7  # Future provider queue time, without implementing a limiter.
        if len(calls) == 2:
            raise TimeoutError("query failed")
        return original(**kwargs)

    monkeypatch.setattr(service, "get_history_orders", history)
    result = paper.reconcile("place")
    assert result["recovery_checks"][0]["started_at"] == 100
    assert result["recovery_checks"][0]["completed_at"] == 107
    assert result["recovery"]["next_check_at"] == 112
    result = next_round(paper, now)
    assert result["recovery"]["successful_negative_checks"] == 1
    assert result["recovery_checks"][-1]["outcome"] == "ERROR"
    assert result["recovery"]["next_check_at"] == 124
    for _ in range(4):
        result = next_round(paper, now)
    assert result["recovery"]["successful_negative_checks"] == 5
    assert result["recovery_checks"][-1]["completed_at"] > 140
    assert len(calls) == 6
    assert all(
        c["acc_id"] == 123 and c["trd_env"] == "SIMULATE" and c["start"] for c in calls
    )


@pytest.mark.parametrize(
    "problem",
    [
        "duplicates",
        "request",
        "history",
        "account",
        "malformed",
        "fills",
        "position_account",
        "position_environment",
    ],
)
def test_invalid_or_ambiguous_tag_evidence_does_not_count_as_absence(
    rig, monkeypatch, problem
):
    paper, service, broker, store = rig
    now = recovery_clock(paper)
    broker.failure = "lost_response"
    place(paper)
    order = broker.orders["9007199254740993"]
    if problem == "duplicates":
        broker.orders["88"] = order | {"order_id": "88"}
    elif problem == "request":
        order["qty"] = 3
    elif problem == "history":
        monkeypatch.setattr(
            service, "get_history_orders", lambda **_kwargs: [order | {"price": "2"}]
        )
    elif problem == "account":
        order["acc_id"] = 456
    elif problem == "malformed":
        monkeypatch.setattr(
            service, "get_orders", lambda **_kwargs: [{"remark": "missing identity"}]
        )
    elif problem.startswith("position_"):
        position = {"code": "US.AAPL", "qty": 2}
        position.update(
            {"acc_id": 456} if problem == "position_account" else {"trd_env": "REAL"}
        )
        monkeypatch.setattr(service, "get_positions", lambda **_kwargs: [position])
    else:
        order["dealt_qty"] = float("nan")
    for _ in range(6):
        result = next_round(paper, now)
        assert result["state"] == "UNKNOWN_OUTCOME"
        assert result["recovery"]["successful_negative_checks"] == 0
        assert result["recovery_checks"][-1]["outcome"] == "ERROR"
    assert store.health()["state"] == "JOURNAL_BLOCKED"
    assert len(broker.calls) == 1


def test_late_discovery_blocks_before_position_accounting_and_reports_exposure(
    rig, monkeypatch
):
    paper, service, broker, store = rig
    now = recovery_clock(paper)
    broker.failure = "timeout"
    place(paper)
    result = {}
    for _ in range(5):
        result = next_round(paper, now)
    assert store.health()["state"] == "READY"
    row = store.lookup("place")
    assert row is not None
    broker.orders["99"] = json.loads(row["merged_request"]) | {
        "order_id": "99",
        "order_status": "FILLED_ALL",
        "dealt_qty": 2,
        "dealt_avg_price": 1.1,
    }
    original = service.get_positions

    def positions(**kwargs):
        assert store.health()["blocking_reasons"] == ["LATE_ORDER_FOUND"]
        assert kwargs["trd_env"] == "SIMULATE" and kwargs["acc_id"] == 123
        raise TimeoutError("position evidence unavailable")

    monkeypatch.setattr(service, "get_positions", positions)
    result = next_round(paper, now)
    assert result["recovery_checks"][-1]["outcome"] == "ERROR"
    assert store.health()["state"] == "JOURNAL_BLOCKED"
    with pytest.raises(OrderNotSentError):
        place(paper, token="blocked")
    monkeypatch.setattr(service, "get_positions", original)
    service.position = 4
    result = next_round(paper, now)
    assert result["recovery"]["disposition"] == "LATE_BROKER_ORDER_FOUND"
    assert result["accounted_facts"]["current_symbol_position"] == "4"
    update = result["recovery_updates"][0]
    assert update["accounted_facts"]["current_symbol_position"] == "4"
    assert update["broker_order_id"] == "99"
    assert update["next_check_at"] is None
    assert store.health()["state"] == "READY"
    assert len(broker.calls) == 1
    with sqlite3.connect(broker.path) as conn:
        assert [
            r[0]
            for r in conn.execute("SELECT resolution FROM recovery_audit ORDER BY id")
        ] == ["ASSUMED_NOT_PLACED_AFTER_RETRIES", "LATE_BROKER_ORDER_FOUND"]


@pytest.mark.parametrize("kind", ["MODIFY", "CANCEL"])
def test_mutation_target_accounting_does_not_claim_causal_success(rig, kind):
    paper, _, broker, store = rig
    now = recovery_clock(paper)
    target(broker)
    broker.failure = "timeout"
    mutate(paper, kind=kind, **({"price": "2.0"} if kind == "MODIFY" else {}))
    first = paper.reconcile("modify")
    assert first["recovery_checks"][-1]["outcome"] == "TARGET_PENDING"
    assert first["order_tag"] is None
    if kind == "MODIFY":
        broker.orders["77"]["price"] = 2.0
    else:
        broker.orders["77"]["order_status"] = "CANCELLED_ALL"
    result = next_round(paper, now, "modify")
    assert result["accounted_facts"]["mutation_success_proven"] is False
    assert store.health()["state"] == "READY"
    assert len(broker.calls) == 1


@pytest.mark.parametrize("known_id", [True, False])
def test_legacy_untagged_placement_uses_id_or_remains_pending(rig, known_id):
    paper, _, broker, store = rig
    now = recovery_clock(paper)
    broker.failure = "timeout"
    place(paper)
    with sqlite3.connect(broker.path) as conn:
        conn.execute(
            "UPDATE operations SET order_tag=NULL,broker_order_id=?",
            ("77" if known_id else None,),
        )
    target(broker)
    result = {}
    for _ in range(5):
        result = next_round(paper, now)
    assert result["state"] == ("RECONCILED" if known_id else "UNKNOWN_OUTCOME")
    assert result["recovery"]["successful_negative_checks"] == 0
    assert store.health()["state"] == ("READY" if known_id else "JOURNAL_BLOCKED")
    assert len(broker.calls) == 1


def test_recovery_worker_resolves_lost_response_without_client_call(rig, monkeypatch):
    paper, _, broker, store = rig
    done = threading.Event()
    original = store.record_recovery

    def record(*args, **kwargs):
        original(*args, **kwargs)
        done.set()

    monkeypatch.setattr(store, "record_recovery", record)
    paper.recovery.start()
    try:
        broker.failure = "lost_response"
        first = place(paper)
        assert first["state"] == "UNKNOWN_OUTCOME"
        assert done.wait(5)
        assert store.lookup("place")["state"] == "RECONCILED"
        assert store.health()["state"] == "READY"
        assert len(broker.calls) == 1
    finally:
        paper.recovery.stop()
    assert not paper.recovery._thread.is_alive()


def test_startup_worker_recovers_persisted_dispatch_without_client_request(
    rig, monkeypatch
):
    paper, service, broker, store = rig
    broker.failure = "lost_response"
    place(paper)
    path = broker.path
    tag = store.lookup("place")["order_tag"]
    store.close()
    restarted = ExecutionStore(path)
    recovered = PaperExecution(
        cast(TradeService, cast(object, service)), restarted, frozenset({123})
    )
    done = threading.Event()
    original = restarted.record_recovery

    def record(*args, **kwargs):
        original(*args, **kwargs)
        done.set()

    monkeypatch.setattr(restarted, "record_recovery", record)
    recovered.recovery.start()
    try:
        assert done.wait(5)
        row = restarted.lookup("place")
        assert (
            row is not None and row["order_tag"] == tag and row["state"] == "RECONCILED"
        )
        assert restarted.health()["state"] == "READY"
        assert len(broker.calls) == 1
    finally:
        recovered.recovery.stop()
        restarted.close()


def test_previously_conflicting_correlation_cannot_later_be_assumed_absent(rig):
    paper, _, broker, store = rig
    now = recovery_clock(paper)
    broker.failure = "lost_response"
    place(paper)
    broker.orders["9007199254740993"]["qty"] = 3
    result = paper.reconcile("place")
    assert result["recovery_checks"][-1]["outcome"] == "ERROR"
    broker.orders.clear()
    for _ in range(5):
        result = next_round(paper, now)
    assert result["recovery"]["successful_negative_checks"] == 0
    assert result["recovery"]["disposition"] is None
    assert store.health()["state"] == "JOURNAL_BLOCKED"


def test_shutdown_during_provider_read_does_not_wait_for_io_or_write_after_close(
    rig, monkeypatch
):
    paper, service, broker, store = rig
    broker.failure = "lost_response"
    place(paper)
    entered, release = threading.Event(), threading.Event()
    original = service.get_orders

    def orders(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(service, "get_orders", orders)
    paper.recovery.start()
    assert entered.wait(5)
    try:
        paper.recovery.stop()
        store.close()
    finally:
        release.set()
        paper.recovery._thread.join(timeout=5)
    assert not paper.recovery._thread.is_alive()
    with sqlite3.connect(broker.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recovery_checks").fetchone() == (0,)
    assert len(broker.calls) == 1


def test_result_conversion_failure_after_outcome_commit_keeps_observed_tag(
    rig, monkeypatch
):
    paper, _, broker, store = rig

    def failed_result(_row):
        raise OSError("result conversion failed")

    monkeypatch.setattr(paper, "result", failed_result)
    result = place(paper)
    assert result["submission_state"] == "durably_stored"
    assert result["broker_acknowledged"] is True
    assert result["order_tag"] == broker.calls[0][1]["remark"]
    assert store.lookup("place")["state"] == "ACKNOWLEDGED"
    assert place(paper) == result
    assert paper.health()["pending_operation_ids"] == ["place"]
