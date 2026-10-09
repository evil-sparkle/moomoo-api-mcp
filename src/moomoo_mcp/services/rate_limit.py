"""Provider-operation budgets shared by async callers and SDK worker threads.

Pending reservations do not age out: a busy worker pool must not turn old
admissions into a fresh burst. Only dispatch starts the rolling-window clock.
This is process-local state, not coordination with independent OpenD clients.
"""

import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock

import anyio
from pyrate_limiter import InMemoryBucket, Rate, RateItem


@dataclass(frozen=True)
class RateLimitPolicy:
    provider: str
    operation: str
    calls: int
    period_seconds: float
    safety_margin_seconds: float = 0.1
    max_wait_seconds: float = 5.0
    min_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.calls < 1:
            raise ValueError("calls must be positive")
        for name, value in (
            ("period_seconds", self.period_seconds),
            ("safety_margin_seconds", self.safety_margin_seconds),
            ("max_wait_seconds", self.max_wait_seconds),
            ("min_interval_seconds", self.min_interval_seconds),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.period_seconds == 0:
            raise ValueError("period_seconds must be positive")

    @property
    def window_seconds(self) -> float:
        return self.period_seconds + self.safety_margin_seconds


class ProviderRateLimitError(RuntimeError):
    """Admission failed; retry_after_seconds is a rounded-up estimate."""

    def __init__(self, policy: RateLimitPolicy, retry_after: float):
        self.provider = policy.provider
        self.operation = policy.operation
        self.retry_after_seconds = max(1, math.ceil(retry_after))
        super().__init__(
            f"{policy.provider} {policy.operation} rate limit exceeded; "
            f"retry after {self.retry_after_seconds} seconds "
            f"(retry_after_seconds={self.retry_after_seconds})."
        )


class Reservation:
    """One pending dispatch; release is harmless after dispatch has started."""

    def __init__(self, limiter: "ProviderRequestLimiter", weight: int = 1):
        self._limiter = limiter
        self._remaining = weight

    @property
    def remaining(self) -> int:
        with self._limiter._lock:
            return self._remaining

    def start(self) -> None:
        """Commit immediately before the SDK call, or reject a released slot."""
        self._limiter._start(self)

    def release(self) -> None:
        """Return unused capacity without refunding a dispatched attempt."""
        self._limiter._release(self)


class ProviderRequestLimiter:
    """Thread-safe rolling admission, with cancellable async quota waiting.

    Share the instance for every caller of a governed gateway operation.
    The lock only protects in-memory bookkeeping; no SDK call or wait holds it.
    Clock and sleep injection allows tests to advance time without real delays.
    """

    def __init__(
        self,
        policy: RateLimitPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ):
        self.policy = policy
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        # PyrateLimiter owns the exact rolling-window log. Pending reservations
        # deliberately live outside its timestamped history until SDK dispatch.
        self._bucket = InMemoryBucket(
            [Rate(policy.calls, math.ceil(policy.window_seconds * 1000))]
        )
        self._spacing = (
            InMemoryBucket([Rate(1, math.ceil(policy.min_interval_seconds * 1000))])
            if policy.min_interval_seconds
            else None
        )
        self._pending: set[Reservation] = set()
        self._waiters: deque[object] = deque()

    def _prune(self, now: float) -> None:
        timestamp = math.floor(now * 1000)
        self._bucket.leak(timestamp)
        if self._spacing is not None:
            self._spacing.leak(timestamp)

    def _retry_after(self, now: float) -> float:
        # Pending workers have no known dispatch time. A full window is a
        # conservative estimate; cancellation may free capacity sooner.
        oldest = self._bucket.peek(self._bucket.count() - 1)
        if oldest is not None:
            return max(
                0.001,
                (oldest.timestamp + self._bucket.rates[0].interval + 1) / 1000 - now,
            )
        return self.policy.window_seconds

    def _try_reserve(
        self, weight: int = 1, ticket: object | None = None
    ) -> tuple[Reservation | None, float]:
        if weight < 1 or weight > self.policy.calls:
            raise ValueError("Reservation weight must fit the operation budget")
        with self._lock:
            now = self._clock()
            self._prune(now)
            if self._waiters and self._waiters[0] is not ticket:
                return None, self._retry_after(now)
            if self._spacing is not None:
                if weight != 1:
                    raise ValueError(
                        "Paced operations require single-call reservations"
                    )
                # One pending start per paced group prevents delayed workers
                # from consuming previously admitted permits in a later burst.
                if self._pending:
                    return None, self.policy.window_seconds
                previous = self._spacing.peek(0)
                if previous is not None:
                    return None, max(
                        0.001,
                        (previous.timestamp + self._spacing.rates[0].interval + 1)
                        / 1000
                        - now,
                    )
            pending = sum(slot._remaining for slot in self._pending)
            if self._bucket.count() + pending + weight <= self.policy.calls:
                reservation = Reservation(self, weight)
                self._pending.add(reservation)
                return reservation, 0.0
            return None, self._retry_after(now)

    def reserve(self, weight: int = 1) -> Reservation:
        """Reserve immediately; synchronous callers never sleep for quota."""
        reservation, retry_after = self._try_reserve(weight)
        if reservation is None:
            raise ProviderRateLimitError(self.policy, retry_after)
        return reservation

    async def acquire(
        self, weight: int = 1, *, deadline: float | None = None
    ) -> Reservation:
        """Wait for quota without using a worker; timeout never dispatches."""
        if self.policy.max_wait_seconds == 0:
            return self.reserve(weight)
        if deadline is None:
            deadline = self._clock() + self.policy.max_wait_seconds
        ticket = object()
        with self._lock:
            self._waiters.append(ticket)
        try:
            while True:
                if self._clock() > deadline:
                    with self._lock:
                        now = self._clock()
                        self._prune(now)
                        retry_after = self._retry_after(now)
                    raise ProviderRateLimitError(self.policy, retry_after)
                reservation, retry_after = self._try_reserve(weight, ticket)
                if reservation is not None:
                    return reservation
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise ProviderRateLimitError(self.policy, retry_after)
                await self._sleep(min(retry_after, remaining, 0.05))
        finally:
            with self._lock:
                self._waiters.remove(ticket)

    def _start(self, reservation: Reservation) -> None:
        with self._lock:
            if reservation not in self._pending:
                raise RuntimeError(
                    "Provider request reservation already released or used"
                )
            now = self._clock()
            self._prune(now)
            item = RateItem(self.policy.operation, math.floor(now * 1000))
            if not self._bucket.put(item):
                raise RuntimeError("Reserved provider capacity was not available")
            if self._spacing is not None and not self._spacing.put(item):
                raise RuntimeError("Reserved dispatch spacing was not available")
            reservation._remaining -= 1
            if reservation._remaining == 0:
                self._pending.remove(reservation)

    def _release(self, reservation: Reservation) -> None:
        with self._lock:
            self._pending.discard(reservation)
            reservation._remaining = 0
