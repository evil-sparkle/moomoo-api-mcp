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


@dataclass(frozen=True)
class RateLimitPolicy:
    provider: str
    operation: str
    calls: int
    period_seconds: float
    safety_margin_seconds: float = 0.1
    max_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.calls < 1:
            raise ValueError("calls must be positive")
        for name, value in (
            ("period_seconds", self.period_seconds),
            ("safety_margin_seconds", self.safety_margin_seconds),
            ("max_wait_seconds", self.max_wait_seconds),
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

    def __init__(self, limiter: "ProviderRequestLimiter"):
        self._limiter = limiter

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
        self._starts: deque[float] = deque()
        self._pending: set[Reservation] = set()

    def _prune(self, now: float) -> None:
        while self._starts and now >= self._starts[0] + self.policy.window_seconds:
            self._starts.popleft()

    def _retry_after(self, now: float) -> float:
        # Pending workers have no known dispatch time. A full window is a
        # conservative estimate; cancellation may free capacity sooner.
        if self._starts:
            return max(0.0, self._starts[0] + self.policy.window_seconds - now)
        return self.policy.window_seconds

    def _try_reserve(self) -> tuple[Reservation | None, float]:
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._starts) + len(self._pending) < self.policy.calls:
                reservation = Reservation(self)
                self._pending.add(reservation)
                return reservation, 0.0
            return None, self._retry_after(now)

    def reserve(self) -> Reservation:
        """Reserve immediately; synchronous callers never sleep for quota."""
        reservation, retry_after = self._try_reserve()
        if reservation is None:
            raise ProviderRateLimitError(self.policy, retry_after)
        return reservation

    async def acquire(self) -> Reservation:
        """Wait for quota without using a worker; timeout never dispatches."""
        if self.policy.max_wait_seconds == 0:
            return self.reserve()
        deadline = self._clock() + self.policy.max_wait_seconds
        while True:
            if self._clock() > deadline:
                with self._lock:
                    now = self._clock()
                    self._prune(now)
                    retry_after = self._retry_after(now)
                raise ProviderRateLimitError(self.policy, retry_after)
            reservation, retry_after = self._try_reserve()
            if reservation is not None:
                return reservation
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise ProviderRateLimitError(self.policy, retry_after)
            # A released pending reservation can free capacity before the next
            # timestamp expires. Short sleeps observe that without binding
            # notifications to one event loop or touching them from SDK threads.
            await self._sleep(min(retry_after, remaining, 0.05))

    def _start(self, reservation: Reservation) -> None:
        with self._lock:
            if reservation not in self._pending:
                raise RuntimeError(
                    "Provider request reservation already released or used"
                )
            self._pending.remove(reservation)
            self._starts.append(self._clock())

    def _release(self, reservation: Reservation) -> None:
        with self._lock:
            self._pending.discard(reservation)
