"""Stateful paper broker doubles shared by journal and transport tests."""

import sqlite3
import threading
from typing import Any

import pandas as pd
from moomoo import RET_ERROR, RET_OK

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
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
    _assess_order = TradeService._assess_order
    _check_dispatch_controls = TradeService._check_dispatch_controls
    _confirm_dispatch_controls = TradeService._confirm_dispatch_controls

    def __init__(self, broker):
        self.trade_ctx = broker
        self.dispatcher = BrokerRequestDispatcher(clock=lambda: self.trade_ctx.clock())
        self.policy = TradingPolicy(TradingMode.SIMULATE)
        self._control_local = threading.local()
        self.trading_controls = None
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


def recovery_clock(paper, initial=100.0):
    now = [initial]
    paper.recovery.clock = lambda: now[0]
    return now
