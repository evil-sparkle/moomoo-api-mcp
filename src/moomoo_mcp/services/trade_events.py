"""Bounded callback observations; never an execution or recovery ledger."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from contextlib import suppress
from numbers import Integral, Real
from typing import Any
from uuid import uuid4

from moomoo import RET_OK, TradeDealHandlerBase, TradeOrderHandlerBase

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.sdk_response import as_frame

EVENT_FIELDS = (
    "order_id",
    "deal_id",
    "code",
    "order_type",
    "order_status",
    "status",
    "trd_side",
    "qty",
    "price",
    "dealt_qty",
    "dealt_avg_price",
    "create_time",
    "updated_time",
    "time_in_force",
    "session",
    "currency",
)
IDENTIFIERS = frozenset({"order_id", "deal_id"})


def exact_identifier(value: Any) -> str:
    """Preserve integer precision and refuse identifiers already made lossy."""
    if isinstance(value, Integral) and not isinstance(value, bool):
        text = str(int(value))
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError("Callback identifiers must be exact integers or strings")
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError("Callback identifiers must be positive decimal identifiers")
    return text


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:256]
    return None


class TradeEventBuffer:
    """Keep a short process-local ring independently of placement responses.

    An observation does not establish dispatch correlation, broker acceptance,
    or complete execution history. Global retention gaps conservatively signal
    that matching account events may have been lost.
    """

    def __init__(
        self,
        max_events: int = 1024,
        retention_seconds: float = 900,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int):
            raise ValueError("max_events must be a positive integer")
        if max_events < 1 or not math.isfinite(retention_seconds):
            raise ValueError("Event bounds must be finite and positive")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self.epoch = str(uuid4())
        self._events: deque[tuple[float, dict[str, Any]]] = deque(maxlen=max_events)
        self._retention_seconds = retention_seconds
        self._monotonic = monotonic
        self._sequence = 0
        self._lock = threading.Lock()
        self._accepting = True
        self._reconnects = 0

    def resume(self) -> None:
        with self._lock:
            self._accepting = True

    def close(self) -> None:
        with self._lock:
            self._accepting = False
            self._events.clear()

    def mark_reconnect(self) -> None:
        with self._lock:
            self._reconnects += 1

    def _expire(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self._retention_seconds:
            self._events.popleft()

    def append(self, kind: str, account: Any, rows: Iterable[dict[str, Any]]) -> None:
        """Accept exact scoped rows without retaining arbitrary SDK objects."""
        account_id = exact_identifier(account)
        if kind not in {"ORDER", "DEAL"}:
            raise ValueError("event_type must be ORDER or DEAL")
        for row in rows:
            environment = row.get("trd_env")
            if environment not in {"REAL", "SIMULATE"}:
                continue
            if kind == "DEAL" and environment != "REAL":
                continue
            try:
                payload = {
                    name: exact_identifier(row[name])
                    if name in IDENTIFIERS
                    else _scalar(row[name])
                    for name in EVENT_FIELDS
                    if name in row
                }
                if "order_id" not in payload or (
                    kind == "DEAL" and "deal_id" not in payload
                ):
                    continue
            except ValueError:
                # A malformed identifier cannot be attributed to an account.
                continue
            with self._lock:
                if not self._accepting:
                    return
                now = self._monotonic()
                self._expire(now)
                self._sequence += 1
                self._events.append(
                    (
                        now,
                        {
                            "sequence": self._sequence,
                            "event_type": kind,
                            "observed_at": utc_now_iso(),
                            "acc_id": account_id,
                            "trd_env": environment,
                            "data": payload,
                        },
                    )
                )

    def read(
        self,
        environment: str,
        account: Any,
        *,
        after_sequence: int = 0,
        event_epoch: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        account_id = exact_identifier(account)
        if environment not in {"REAL", "SIMULATE"}:
            raise ValueError("trd_env must be REAL or SIMULATE")
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise ValueError("after_sequence must be a non-negative integer")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise ValueError("limit must be an integer from 1 to 200")
        if after_sequence and event_epoch is None:
            raise ValueError("event_epoch is required with after_sequence")
        with self._lock:
            self._expire(self._monotonic())
            restarted = event_epoch is not None and event_epoch != self.epoch
            after = 0 if restarted else after_sequence
            if after > self._sequence:
                raise ValueError("after_sequence is beyond this event buffer")
            oldest = (
                self._events[0][1]["sequence"] if self._events else self._sequence + 1
            )
            selected = [
                event
                for _, event in self._events
                if event["sequence"] > after
                and event["trd_env"] == environment
                and event["acc_id"] == account_id
            ]
            has_more = len(selected) > limit
            selected = selected[:limit]
            next_sequence = selected[-1]["sequence"] if has_more else self._sequence
            return {
                "event_epoch": self.epoch,
                "trd_env": environment,
                "acc_id": account_id,
                "events": [dict(event, data=dict(event["data"])) for event in selected],
                "next_sequence": next_sequence,
                "has_more": has_more,
                "oldest_sequence": oldest,
                "latest_sequence": self._sequence,
                "retention_gap": after < oldest - 1,
                "restart_gap": restarted,
                "reconnect_count": self._reconnects,
                "ephemeral": True,
                "complete_history": False,
            }


class OrderEventHandler(TradeOrderHandlerBase):
    def __init__(self, buffer: TradeEventBuffer):
        super().__init__()
        self.buffer = buffer

    def on_recv_rsp(self, rsp_pb):
        result = super().on_recv_rsp(rsp_pb)
        ret, data = result
        if ret == RET_OK:
            with suppress(AttributeError, TypeError, ValueError):
                # SDK 10.10.7008's decoded DataFrame omits acc_id. The exact
                # protobuf header must supply scope; never infer it from a code.
                self.buffer.append(
                    "ORDER",
                    rsp_pb.s2c.header.accID,
                    as_frame("order_callback", data).to_dict("records"),
                )
        return result


class DealEventHandler(TradeDealHandlerBase):
    def __init__(self, buffer: TradeEventBuffer):
        super().__init__()
        self.buffer = buffer

    def on_recv_rsp(self, rsp_pb):
        result = super().on_recv_rsp(rsp_pb)
        ret, data = result
        if ret == RET_OK:
            with suppress(AttributeError, TypeError, ValueError):
                self.buffer.append(
                    "DEAL",
                    rsp_pb.s2c.header.accID,
                    as_frame("deal_callback", data).to_dict("records"),
                )
        return result
