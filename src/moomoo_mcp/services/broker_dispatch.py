"""Moomoo operation policies and dispatch using shared PyrateLimiter budgets.

One instance belongs to one process-owned gateway/user. Account pools use the
resolved account ID; quote and user pools are shared across its local callers.
See README's broker request limits for the policy inventory and source links.
"""

import math
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, TypeVar

import anyio
import anyio.to_thread

from moomoo_mcp.services.rate_limit import (
    ProviderRequestLimiter,
    RateLimitPolicy,
    Reservation,
)

T = TypeVar("T")
PoolKey = tuple[str, int | None]
DOC_ROOT = "https://openapi.moomoo.com/moomoo-api-doc/en/"


@dataclass(frozen=True)
class OperationPolicy:
    calls: int
    scope: str
    documentation: str
    group: str = ""
    condition: str = "always"
    min_interval_seconds: float = 0.0


# Only explicitly shared APIs use the same group. The max-quantity grouping is
# conservative: the combo docs describe one limit for max-quantity query APIs.
OPERATION_POLICIES = {
    "accinfo_query": OperationPolicy(
        10, "account", "trade/get-funds.html", condition="refresh"
    ),
    "position_list_query": OperationPolicy(
        10, "account", "trade/get-position-list.html", condition="refresh"
    ),
    "order_list_query": OperationPolicy(
        10, "account", "trade/get-order-list.html", condition="refresh"
    ),
    "deal_list_query": OperationPolicy(
        10, "account", "trade/get-order-fill-list.html", condition="refresh"
    ),
    "history_order_list_query": OperationPolicy(
        10, "account", "trade/get-history-order-list.html"
    ),
    "history_deal_list_query": OperationPolicy(
        10, "account", "trade/get-history-order-fill-list.html"
    ),
    "acctradinginfo_query": OperationPolicy(
        10, "account", "trade/get-max-trd-qtys.html", group="max_tradable"
    ),
    "comboorder_tradinginfo_query": OperationPolicy(
        10, "account", "trade/comboorder-tradinginfo-query.html", group="max_tradable"
    ),
    "get_margin_ratio": OperationPolicy(10, "user", "trade/get-margin-ratio.html"),
    "get_acc_cash_flow": OperationPolicy(20, "account", "trade/get-acc-cash-flow.html"),
    "unlock_trade": OperationPolicy(10, "user", "trade/unlock.html"),
    "place_order": OperationPolicy(
        15,
        "account",
        "trade/place-order.html",
        group="order_placement",
        min_interval_seconds=0.020,
    ),
    "place_combo_order": OperationPolicy(
        15,
        "account",
        "trade/place-combo-order.html",
        group="order_placement",
        min_interval_seconds=0.020,
    ),
    "modify_order": OperationPolicy(
        20, "account", "trade/modify-order.html", min_interval_seconds=0.040
    ),
    "get_market_snapshot": OperationPolicy(
        60, "gateway", "quote/get-market-snapshot.html"
    ),
    "request_history_kline": OperationPolicy(
        60, "gateway", "quote/request-history-kline.html", condition="initial_page"
    ),
    "get_option_expiration_date": OperationPolicy(
        60, "gateway", "quote/get-option-expiration-date.html"
    ),
    "get_option_chain": OperationPolicy(10, "gateway", "quote/get-option-chain.html"),
    "get_market_state": OperationPolicy(10, "gateway", "quote/get-market-state.html"),
    "request_trading_days": OperationPolicy(
        30, "gateway", "quote/request-trading-days.html"
    ),
    "get_user_security_group": OperationPolicy(
        10, "gateway", "quote/get-user-security-group.html"
    ),
    "get_user_security": OperationPolicy(10, "gateway", "quote/get-user-security.html"),
}

# These interfaces have no published numeric frequency policy. Subscription
# capacity and the minimum hold period are still enforced by OpenD.
UNTIMED_OPERATIONS = {
    "get_acc_list": "trade/get-acc-list.html",
    "get_global_state": "quote/get-global-state.html",
    "get_stock_basicinfo": "quote/get-static-info.html",
    "get_stock_quote": "quote/get-stock-quote.html",
    "get_order_book": "quote/get-order-book.html",
    "subscribe": "quote/sub.html",
    "unsubscribe": "quote/sub.html",
    "query_subscription": "quote/query-subscription.html",
}


@dataclass(frozen=True)
class QuotaRequest:
    operation: str
    account_id: int | None = None
    weight: int = 1


class _DispatchLease:
    """Transfer permit cleanup to a worker once service execution has begun.

    Cancellation releases unused capacity, including an active validation
    worker's future calls. Mutation transactions retain their permits so a
    credential-managed REAL write can still relock and a paper marker completes.
    """

    def __init__(self, slots: dict[PoolKey, Reservation]):
        self.slots = slots
        self._lock = Lock()
        self._closed = False
        self._cancelled = False
        self._protected: set[PoolKey] = set()

    def begin(self) -> None:
        with self._lock:
            if self._closed or self._cancelled:
                raise RuntimeError("Broker dispatch admission was cancelled")

    def start(self, key: PoolKey | None, slot: Reservation | None) -> None:
        with self._lock:
            if self._cancelled and key not in self._protected:
                raise RuntimeError("Broker dispatch admission was cancelled")
            if slot is not None:
                slot.start()

    def protect(self, keys: set[PoolKey]) -> None:
        with self._lock:
            if self._cancelled:
                raise RuntimeError("Broker dispatch admission was cancelled")
            self._protected.update(keys)

    def unprotect(self, keys: set[PoolKey]) -> None:
        with self._lock:
            self._protected.difference_update(keys)

    def finish(self) -> None:
        with self._lock:
            self._closed = True
            for slot in self.slots.values():
                slot.release()

    def cancel_pending(self) -> None:
        with self._lock:
            self._cancelled = True
            for key, slot in self.slots.items():
                if key not in self._protected:
                    slot.release()


class BrokerRequestDispatcher:
    """Shared registry, async admission, and the synchronous SDK dispatch gate."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        option_chain_limiter: ProviderRequestLimiter | None = None,
        max_wait_seconds: float = 5.0,
    ):
        if not math.isfinite(max_wait_seconds) or max_wait_seconds < 0:
            raise ValueError("max_wait_seconds must be finite and non-negative")
        self.clock = clock
        self.sleep = sleep
        self.max_wait_seconds = max_wait_seconds
        self._lock = Lock()
        self._limiters: dict[PoolKey, ProviderRequestLimiter] = {}
        if option_chain_limiter is not None:
            self._limiters[("get_option_chain", None)] = option_chain_limiter
        self._slots: ContextVar[dict[PoolKey, Reservation] | None] = ContextVar(
            "broker_dispatch_slots", default=None
        )
        self._lease: ContextVar[_DispatchLease | None] = ContextVar(
            "broker_dispatch_lease", default=None
        )

    @staticmethod
    def request(operation: str, **kwargs: Any) -> QuotaRequest | None:
        if operation in UNTIMED_OPERATIONS:
            return None
        policy = OPERATION_POLICIES[operation]
        if policy.condition == "refresh" and not kwargs.get("refresh_cache", False):
            return None
        if policy.condition == "initial_page" and kwargs.get("page_req_key"):
            return None
        account = None
        if policy.scope == "account":
            account = int(kwargs.get("acc_id", 0))
            if account <= 0:
                raise ValueError("Resolve the broker account before quota admission")
        return QuotaRequest(operation, account)

    @staticmethod
    def key(request: QuotaRequest) -> PoolKey:
        policy = OPERATION_POLICIES[request.operation]
        if policy.scope == "account" and (
            request.account_id is None or request.account_id <= 0
        ):
            raise ValueError("Account-scoped quota requires a resolved account ID")
        return (
            policy.group or request.operation,
            request.account_id if policy.scope == "account" else None,
        )

    def limiter(self, request: QuotaRequest) -> ProviderRequestLimiter:
        key = self.key(request)
        with self._lock:
            if key not in self._limiters:
                policy = OPERATION_POLICIES[request.operation]
                self._limiters[key] = ProviderRequestLimiter(
                    RateLimitPolicy(
                        "Moomoo",
                        key[0],
                        policy.calls,
                        30,
                        max_wait_seconds=self.max_wait_seconds,
                        min_interval_seconds=policy.min_interval_seconds,
                    ),
                    clock=self.clock,
                    sleep=self.sleep,
                )
            return self._limiters[key]

    def _merged(self, requests: Sequence[QuotaRequest]) -> dict[PoolKey, QuotaRequest]:
        merged: dict[PoolKey, QuotaRequest] = {}
        for request in requests:
            key = self.key(request)
            if key in merged:
                request = replace(request, weight=merged[key].weight + request.weight)
            merged[key] = request
        return merged

    @contextmanager
    def reserve(self, requests: Sequence[QuotaRequest]) -> Iterator[None]:
        """Reserve a synchronous bundle, borrowing an admitted MCP lease."""
        slots = dict(self._slots.get() or {})
        owned: list[Reservation] = []
        token = None
        try:
            for key, request in self._merged(requests).items():
                current = slots.get(key)
                if current is None or current.remaining < request.weight:
                    current = self.limiter(request).reserve(request.weight)
                    slots[key] = current
                    owned.append(current)
            token = self._slots.set(slots)
            yield
        finally:
            if token is not None:
                self._slots.reset(token)
            for slot in owned:
                slot.release()

    @contextmanager
    def protect_mutation(self, requests: Sequence[QuotaRequest]) -> Iterator[None]:
        """Retain admitted permits once unlocking or a paper marker will start."""
        lease = self._lease.get()
        keys = {self.key(request) for request in requests}
        if lease is not None:
            lease.protect(keys)
        try:
            yield
        finally:
            if lease is not None:
                lease.unprotect(keys)

    def call(
        self, operation: str, func: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> T:
        """Start one actual SDK call. Never sleep or replay a broker request."""
        request = self.request(operation, **kwargs)
        lease = self._lease.get()
        if request is None:
            if lease is not None:
                lease.start(None, None)
            return func(*args, **kwargs)
        with self.reserve([request]):
            slots = self._slots.get()
            assert slots is not None
            key = self.key(request)
            if lease is None:
                slots[key].start()
            else:
                lease.start(key, slots[key])
            return func(*args, **kwargs)

    async def run_async(
        self,
        requests: Sequence[QuotaRequest],
        func: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Await all capacity before dispatch; one deadline covers the bundle."""
        slots: dict[PoolKey, Reservation] = {}
        deadline = self.clock() + self.max_wait_seconds
        try:
            for key, request in self._merged(requests).items():
                slots[key] = await self.limiter(request).acquire(
                    request.weight, deadline=deadline
                )
        except BaseException:
            for slot in slots.values():
                slot.release()
            raise
        lease = _DispatchLease(slots)

        def dispatch() -> T:
            lease.begin()
            token = self._slots.set(slots)
            lease_token = self._lease.set(lease)
            try:
                return func(*args, **kwargs)
            finally:
                self._slots.reset(token)
                self._lease.reset(lease_token)
                lease.finish()

        try:
            return await anyio.to_thread.run_sync(dispatch, abandon_on_cancel=True)
        finally:
            lease.cancel_pending()
