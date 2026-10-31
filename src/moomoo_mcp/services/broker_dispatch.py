"""Moomoo operation policies and dispatch using shared PyrateLimiter budgets.

One instance belongs to one process-owned gateway/user. Account pools use the
resolved account ID; quote and user pools are shared across its local callers.
See README's broker request limits for the policy inventory and source links.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, TypeVar

import anyio
import anyio.to_thread
from typing_extensions import TypedDict

from moomoo_mcp.services.health import run_detached
from moomoo_mcp.services.rate_limit import (
    ProviderRateLimitError,
    ProviderRequestLimiter,
    RateLimitPolicy,
    Reservation,
)

T = TypeVar("T")
PoolKey = tuple[str, int | None]
DOC_ROOT = "https://openapi.moomoo.com/moomoo-api-doc/en/"
READ_TIMEOUT_SECONDS = 15.0
MAX_INFLIGHT_READS = 8
MAX_PENDING_READS = 32


class ReadTiming(TypedDict):
    deadline_seconds: float
    admission_seconds: float
    quota_wait_seconds: float
    worker_queue_seconds: float
    sdk_seconds: float
    total_seconds: float
    sdk_calls_started: int
    sdk_calls_completed: int
    sdk_in_flight: bool


class BrokerReadOverloadError(RuntimeError):
    """This invocation was rejected before creating a timer or dispatching work."""

    def __init__(self, operation: str, pending: int, maximum: int):
        self.operation = operation
        self.pending_reads = pending
        self.max_pending_reads = maximum
        super().__init__(
            f"{operation} read admission is overloaded "
            f"({pending}/{maximum} pending reads). This invocation was rejected "
            "before admission or SDK dispatch; existing SDK calls were not aborted."
        )


class BrokerReadTimeoutError(TimeoutError):
    """The caller's deadline expired, not a claim that OpenD aborted a query."""

    def __init__(self, read: "_ReadCall"):
        self.operation = read.operation
        self.phase = read.phase
        self.timing = read.timing()
        super().__init__(
            f"{self.operation} read await exceeded its "
            f"{read.timeout_seconds:g}s end-to-end deadline during {self.phase}. "
            + (
                "The blocking SDK call may finish in the background; "
                "OpenD execution was not aborted. Late results are ignored."
                if self.timing["sdk_in_flight"]
                else "No further SDK calls will start; unused reservations are "
                "released. Already dispatched calls are not rolled back."
            )
        )


class _ReadCall:
    """Per-invocation state shared only with its own bounded SDK workers."""

    def __init__(self, operation: str, timeout: float, clock: Callable[[], float]):
        self.operation = operation
        self.timeout_seconds = timeout
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + timeout
        self.phase = "admission"
        self._lock = Lock()
        self._cancelled = False
        self._finished: float | None = None
        self._durations: dict[str, float] = {}
        self._starts: dict[str, float] = {}
        self._sdk_started = 0
        self._sdk_completed = 0

    def check(self) -> None:
        with self._lock:
            cancelled = self._cancelled
        if cancelled:
            raise RuntimeError(
                "Read await was cancelled; dispatched SDK calls were not aborted"
            )
        if self.clock() >= self.deadline:
            raise BrokerReadTimeoutError(self)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def finish(self) -> None:
        with self._lock:
            self._finished = self.clock()

    @contextmanager
    def measure(self, phase: str) -> Iterator[None]:
        with self._lock:
            previous = self.phase
            self.phase = phase
            self._starts[phase] = self.clock()
            if phase == "sdk":
                self._sdk_started += 1
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            with self._lock:
                elapsed = self.clock() - self._starts.pop(phase)
                self._durations[phase] = self._durations.get(phase, 0) + elapsed
                if phase == "sdk":
                    self._sdk_completed += 1
                if not self._cancelled and not failed:
                    self.phase = previous

    def timing(self) -> ReadTiming:
        with self._lock:
            now = self.clock() if self._finished is None else self._finished
            durations = dict(self._durations)
            for phase, started in self._starts.items():
                durations[phase] = durations.get(phase, 0) + max(0, now - started)
            return {
                "deadline_seconds": self.timeout_seconds,
                "admission_seconds": round(durations.get("admission", 0), 6),
                "quota_wait_seconds": round(durations.get("quota", 0), 6),
                "worker_queue_seconds": round(durations.get("worker_queue", 0), 6),
                "sdk_seconds": round(durations.get("sdk", 0), 6),
                "total_seconds": round(max(0, now - self.started), 6),
                "sdk_calls_started": self._sdk_started,
                "sdk_calls_completed": self._sdk_completed,
                "sdk_in_flight": "sdk" in self._starts,
            }


_current_read: ContextVar[_ReadCall | None] = ContextVar("broker_read", default=None)


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
    "get_rehab": OperationPolicy(60, "gateway", "quote/get-rehab.html"),
    "get_corporate_actions_dividends": OperationPolicy(
        30, "gateway", "quote/get-corporate-actions-dividends.html"
    ),
    "get_earnings_calendar": OperationPolicy(
        60, "gateway", "quote/get-earnings-calendar.html"
    ),
    "get_option_volatility": OperationPolicy(
        30, "gateway", "quote/get-option-volatility.html"
    ),
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
    "order_fee_query": OperationPolicy(10, "account", "trade/order-fee-query.html"),
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
    "get_history_kl_quota": "quote/get-history-kl-quota.html",
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
        read_timeout_seconds: float = READ_TIMEOUT_SECONDS,
        max_inflight_reads: int = MAX_INFLIGHT_READS,
        max_pending_reads: int = MAX_PENDING_READS,
        deadline_sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ):
        if not math.isfinite(max_wait_seconds) or max_wait_seconds < 0:
            raise ValueError("max_wait_seconds must be finite and non-negative")
        if not math.isfinite(read_timeout_seconds) or read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be finite and positive")
        if (
            isinstance(max_inflight_reads, bool)
            or not isinstance(max_inflight_reads, int)
            or max_inflight_reads < 1
        ):
            raise ValueError("max_inflight_reads must be a positive integer")
        if (
            isinstance(max_pending_reads, bool)
            or not isinstance(max_pending_reads, int)
            or max_pending_reads < 1
        ):
            raise ValueError("max_pending_reads must be a positive integer")
        self.clock = clock
        self.sleep = sleep
        self.max_wait_seconds = max_wait_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_inflight_reads = max_inflight_reads
        self.max_pending_reads = max_pending_reads
        self._deadline_sleep = deadline_sleep
        self._read_workers = 0
        self._pending_reads = 0
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

    @property
    def read_workers_in_flight(self) -> int:
        """Includes abandoned workers until the blocking function really exits."""
        with self._lock:
            return self._read_workers

    @property
    def read_requests_pending(self) -> int:
        """Admitted awaiters, including execution, quota and worker-queue waits."""
        with self._lock:
            return self._pending_reads

    @asynccontextmanager
    async def read_deadline(self, operation: str):
        """Bound admission, worker queues, quota and execution with one deadline.

        Cancellation stops the await, never the synchronous SDK thread. Native
        client cancellation propagates unchanged; a deadline has a typed error.
        One request slot covers the entire await, including quota and worker
        queues. Nested scopes reuse that slot; overload never creates a timer.
        """
        existing = _current_read.get()
        if existing is not None:
            yield existing
            return
        with self._lock:
            if self._pending_reads >= self.max_pending_reads:
                raise BrokerReadOverloadError(
                    operation, self._pending_reads, self.max_pending_reads
                )
            self._pending_reads += 1
        try:
            read = _ReadCall(operation, self.read_timeout_seconds, self.clock)
            token = _current_read.set(read)
            expired = False
            try:
                with anyio.CancelScope() as scope:

                    async def expire() -> None:
                        nonlocal expired
                        await self._deadline_sleep(max(0, read.deadline - self.clock()))
                        expired = True
                        read.cancel()
                        scope.cancel()

                    timer = asyncio.create_task(expire())
                    try:
                        yield read
                        read.check()
                    except BaseException:
                        read.cancel()
                        raise
                    finally:
                        timer.cancel()
                        with anyio.CancelScope(shield=True):
                            await asyncio.gather(timer, return_exceptions=True)
                if expired:
                    raise BrokerReadTimeoutError(read)
            except BaseException:
                read.cancel()
                raise
            finally:
                read.finish()
                _current_read.reset(token)
        finally:
            with self._lock:
                self._pending_reads -= 1

    async def read_worker(
        self, func: Callable[..., T], /, *args: Any, **kwargs: Any
    ) -> T:
        """A bounded daemon worker; its slot is released by the worker, not awaiters.

        AnyIO's abandon_on_cancel releases its limiter when the await ends. That
        alone would allow cancelled, still-running SDK threads to grow unbounded.
        These workers do not occupy the shared mutation/local-work thread pool.
        """
        read = _current_read.get()
        assert read is not None
        with read.measure("worker_queue"):
            while True:
                read.check()
                with self._lock:
                    if self._read_workers < self.max_inflight_reads:
                        self._read_workers += 1
                        break
                await self.sleep(0.05)
        context = copy_context()

        def worker() -> T:
            try:
                read.check()
                return context.run(func, *args, **kwargs)
            finally:
                with self._lock:
                    self._read_workers -= 1

        try:
            future = run_detached(worker, "broker-read")
        except BaseException:
            with self._lock:
                self._read_workers -= 1
            raise
        # Cancellation of this wrapper cannot cancel an already-running Future.
        # Late values/exceptions stay on that abandoned future, not a new caller.
        return await asyncio.wrap_future(future)

    @staticmethod
    def check_read_active() -> None:
        """Check again before publishing reference observations after processing."""
        read = _current_read.get()
        if read is not None:
            read.check()

    @staticmethod
    def _invoke(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        read = _current_read.get()
        if read is None:
            return func(*args, **kwargs)
        read.check()
        with read.measure("sdk"):
            result = func(*args, **kwargs)
        # Discard a late response before service cache updates or follow-up calls.
        read.check()
        return result

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
        read = _current_read.get()
        if read is not None:
            read.check()
        request = self.request(operation, **kwargs)
        lease = self._lease.get()
        if request is None:
            if lease is not None:
                lease.start(None, None)
            return self._invoke(func, *args, **kwargs)
        with self.reserve([request]):
            slots = self._slots.get()
            assert slots is not None
            key = self.key(request)
            if lease is None:
                slots[key].start()
            else:
                lease.start(key, slots[key])
            return self._invoke(func, *args, **kwargs)

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
        read = _current_read.get()
        if read is not None:
            deadline = min(deadline, read.deadline)
        try:
            with read.measure("quota") if read is not None else nullcontext():
                for key, request in self._merged(requests).items():
                    slots[key] = await self.limiter(request).acquire(
                        request.weight, deadline=deadline
                    )
        except BaseException as exc:
            for slot in slots.values():
                slot.release()
            if read is not None and isinstance(exc, ProviderRateLimitError):
                read.check()
            raise
        lease = _DispatchLease(slots)

        def dispatch() -> T:
            lease.begin()
            token = self._slots.set(slots)
            lease_token = self._lease.set(lease)
            try:
                with read.measure("processing") if read is not None else nullcontext():
                    result = func(*args, **kwargs)
                    if read is not None:
                        read.check()
                    return result
            finally:
                self._slots.reset(token)
                self._lease.reset(lease_token)
                lease.finish()

        try:
            if read is not None:
                return await self.read_worker(dispatch)
            return await anyio.to_thread.run_sync(dispatch, abandon_on_cancel=True)
        finally:
            lease.cancel_pending()
