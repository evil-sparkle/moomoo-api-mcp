"""Bounded, single-flight probes used by the health check.

The moomoo SDK exposes only synchronous, non-cancellable queries. Wrapping one
in ``asyncio.wait_for`` bounds the *caller*, not the worker: the blocked thread
keeps running. Repeatedly calling a health tool against a gateway that has
stopped answering would therefore accumulate one abandoned thread per call.

``BoundedProbe`` keeps at most one worker per service. While a probe is stuck,
later health calls attach to the same in-flight future and time out against the
deadline instead of starting more work.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

# Total wall-clock budget for a full health check, per the system-health spec.
HEALTH_DEADLINE_SECONDS = 5.0

# How long a synchronous SDK query may wait for a connection that is not yet
# ready. The SDK otherwise spins in an unbounded loop, so an abandoned probe
# worker would live forever. Deliberately *shorter* than
# HEALTH_DEADLINE_SECONDS so a probe against a down gateway comes back with
# the gateway's own 'Connect timeout' diagnostic rather than being cut off by
# the health deadline, which can only report that something took too long.
#
# This bounds the wait for the socket to become ready, not the query itself
# (the SDK caps that separately at 12s). On a healthy connection it is never
# reached.
SYNC_CONNECT_TIMEOUT_SECONDS = 3.0

# Error text is echoed from the gateway, so it is truncated and flattened before
# it reaches a client. Health must not become a channel for account contents.
_MAX_ERROR_CHARS = 200

# Best-effort classification of a gateway rejection. Permission problems mean a
# reachable gateway that refuses the request, which is a different operator
# action than a transport failure, so they are reported separately rather than
# collapsed into one opaque error.
_PERMISSION_MARKERS = (
    "permission",
    "not authorized",
    "unauthorized",
    "no right",
    "权限",
)


def sanitize_error(value: object) -> str:
    """Flatten and truncate a gateway error for safe inclusion in a response."""
    text = " ".join(str(value).split())
    if len(text) > _MAX_ERROR_CHARS:
        text = text[: _MAX_ERROR_CHARS - 1] + "…"
    return text


def classify_failure(message: str) -> str:
    """Label a failed probe as a permission or a gateway/transport problem."""
    lowered = message.lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        return "permission"
    return "gateway_error"


def failure(status: str, error: object, reason: str | None = None) -> dict[str, Any]:
    """Build a failed service-probe result with a sanitized diagnostic."""
    message = sanitize_error(error)
    return {
        "status": status,
        "reason": reason or classify_failure(message),
        "error": message,
    }


class BoundedProbe:
    """Run a blocking probe on at most one worker thread at a time."""

    def __init__(self, name: str):
        """Initialize the probe runner.

        Args:
            name: Service label used for the worker thread name and diagnostics.
        """
        self.name = name
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._inflight: Future | None = None

    def submit(self, probe: Callable[[], dict[str, Any]]) -> Future:
        """Start ``probe``, or return the future of one already in flight."""
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"health-{self.name}"
                )
            inflight = self._inflight
            if inflight is not None and not inflight.done():
                return inflight
            future = self._executor.submit(probe)
            self._inflight = future
            return future

    def collect(self, future: Future, timeout: float) -> dict[str, Any]:
        """Wait up to ``timeout`` seconds for ``future`` and normalize failures."""
        try:
            result = future.result(timeout=max(timeout, 0.0))
        except FutureTimeoutError:
            # The worker is left running deliberately: it cannot be cancelled,
            # and holding on to it is what keeps the worker count bounded.
            return failure(
                "timeout",
                f"{self.name} probe exceeded the "
                f"{HEALTH_DEADLINE_SECONDS:.0f}s health deadline",
                reason="timeout",
            )
        except Exception as exc:  # noqa: BLE001 - health must never raise
            self._release(future)
            return failure("error", exc)
        self._release(future)
        return result

    def _release(self, future: Future) -> None:
        with self._lock:
            if self._inflight is future:
                self._inflight = None

    @property
    def in_flight(self) -> bool:
        """Whether a worker is currently running a probe for this service."""
        with self._lock:
            return self._inflight is not None and not self._inflight.done()

    def close(self) -> None:
        """Release the worker thread pool, abandoning any stuck probe."""
        with self._lock:
            executor = self._executor
            self._executor = None
            self._inflight = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def aggregate_status(*service_results: dict[str, Any]) -> str:
    """Reduce per-service probe results to an overall connectivity status."""
    succeeded = sum(1 for result in service_results if result.get("status") == "ok")
    if succeeded == len(service_results):
        return "connected"
    if succeeded:
        return "degraded"
    return "disconnected"
