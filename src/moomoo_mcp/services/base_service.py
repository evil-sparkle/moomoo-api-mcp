"""Quote-connection service and the aggregated gateway health check."""

import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from moomoo import RET_OK, OpenQuoteContext

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.health import (
    HEALTH_DEADLINE_SECONDS,
    SYNC_CONNECT_TIMEOUT_SECONDS,
    BoundedProbe,
    aggregate_status,
    failure,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from moomoo_mcp.services.trade_service import TradeService


class MoomooService:
    """Service to manage Moomoo API connections."""

    def __init__(self, host: str = '127.0.0.1', port: int = 11111):
        self.host = host
        self.port = port
        self.quote_ctx: OpenQuoteContext | None = None
        self._quote_probe = BoundedProbe("quote")

    def connect(self) -> None:
        """Start the quote connection to OpenD without waiting for it.

        OpenQuoteContext's default constructor does not raise when OpenD is
        unreachable: it loops on a six-second retry forever. Calling it inline
        would hang the MCP lifespan before it ever yields, so the server would
        never start and check_health — the one tool an operator needs at exactly
        that moment — would never become reachable.

        ``is_async_connect=True`` returns immediately and leaves the SDK to
        connect in the background, so the context object exists but may not be
        connected. That is precisely why probe_quote asks the gateway rather
        than trusting the object's existence.
        """
        self.quote_ctx = OpenQuoteContext(
            host=self.host, port=self.port, is_async_connect=True
        )
        # Without this, a sync query against a context that is still trying to
        # connect waits in an unbounded loop. The bound keeps a probe worker's
        # lifetime finite even after the health deadline has abandoned it.
        self.quote_ctx.set_sync_query_connect_timeout(SYNC_CONNECT_TIMEOUT_SECONDS)

    def close(self) -> None:
        """Close connection and release the health probe worker."""
        self._quote_probe.close()
        if self.quote_ctx:
            self.quote_ctx.close()
            self.quote_ctx = None

    def probe_quote(self) -> dict[str, Any]:
        """Actively check quote connectivity with a read-only global-state call.

        The context object outliving its socket is exactly the failure this
        replaces, so the result is derived from the gateway's return code, never
        from the presence of ``self.quote_ctx``.
        """
        quote_ctx = self.quote_ctx
        if quote_ctx is None:
            return failure(
                "unavailable", "Quote context not initialized", reason="not_initialized"
            )

        ret, data = quote_ctx.get_global_state()
        if ret != RET_OK:
            return failure("error", data)

        result: dict[str, Any] = {"status": "ok"}
        if isinstance(data, dict):
            version = data.get("server_ver")
            if version:
                result["gateway_version"] = str(version)
            # Reported for diagnostics only. Overall status follows the return
            # code, so a reachable gateway that has not logged in to the quote
            # server is visible without being silently reclassified.
            if "qot_logined" in data:
                result["logged_in"] = str(data.get("qot_logined")) == "1"
        return result

    def submit_probe(self) -> Future:
        """Start (or join) the bounded quote connectivity probe."""
        return self._quote_probe.submit(self.probe_quote)

    def collect_probe(self, future: Future, timeout: float) -> dict[str, Any]:
        """Collect a quote probe result within the remaining health deadline."""
        return self._quote_probe.collect(future, timeout)

    def start_health_check(
        self,
        trade_service: "TradeService | None" = None,
        deadline: float | None = None,
    ) -> "HealthCheck":
        """Start both probes and return a handle for collecting them.

        Submitting is instant — each probe runs on its own dedicated worker —
        so the deadline clock starts here, when the request arrives, and covers
        every wait that follows.

        Args:
            trade_service: Trade service to probe. Reported as unavailable when
                omitted, since an unprobed service is not a healthy one.
            deadline: Total seconds allowed for both probes. Defaults to
                ``HEALTH_DEADLINE_SECONDS``.

        Returns:
            A ``HealthCheck`` whose probes are already running.
        """
        return HealthCheck(
            service=self,
            trade_service=trade_service,
            deadline=HEALTH_DEADLINE_SECONDS if deadline is None else deadline,
            quote_future=self.submit_probe(),
            trade_future=trade_service.submit_probe() if trade_service else None,
        )

    def check_health(
        self,
        trade_service: "TradeService | None" = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Probe quote and trade connectivity and report an observed status.

        Both probes are started together and share one deadline, so the whole
        check is bounded even when both services hang.

        This blocks until the probes answer or the deadline expires. Async
        callers should use ``start_health_check`` and await the futures instead,
        so waiting does not occupy a worker thread.

        Args:
            trade_service: Trade service to probe. Reported as unavailable when
                omitted, since an unprobed service is not a healthy one.
            deadline: Total seconds allowed for both probes. Defaults to
                ``HEALTH_DEADLINE_SECONDS``.

        Returns:
            Health dictionary with the overall ``status`` and ``host``, the UTC
            ``checked_at`` observation time, per-service ``quote`` and ``trade``
            results, the configured ``trading_mode``, and ``gateway_version``
            when the gateway reports one. Connectivity says nothing about
            whether trading is unlocked or any market is authorized.
        """
        return self.start_health_check(
            trade_service=trade_service, deadline=deadline
        ).result()


@dataclass
class HealthCheck:
    """Health probes that are already running, awaiting collection.

    Separating the start from the collection lets an async caller await the
    probe futures on the event loop rather than parking a worker thread on
    them. That matters for more than tidiness: a worker that waits here is a
    worker queued behind every other blocking query, and the health check would
    blow its deadline waiting for a turn it had not yet been given.
    """

    service: "MoomooService"
    trade_service: "TradeService | None"
    deadline: float
    quote_future: Future
    trade_future: Future | None
    started: float = field(default_factory=time.monotonic)

    @property
    def futures(self) -> list[Future]:
        """The probe futures that are still worth waiting on."""
        return [f for f in (self.quote_future, self.trade_future) if f is not None]

    def remaining(self) -> float:
        """Seconds left in the deadline, counted from when the probes started."""
        return self.deadline - (time.monotonic() - self.started)

    def result(self) -> dict[str, Any]:
        """Collect both probes within the remaining deadline and report status.

        Safe to call after the futures have been awaited elsewhere: collection
        of a finished probe returns immediately, and an unfinished one is
        reported as a timeout rather than waited on again.
        """
        quote_result = self.service.collect_probe(
            self.quote_future, self.remaining()
        )
        if self.trade_future is None or self.trade_service is None:
            trade_result = failure(
                "unavailable", "Trade service not configured", reason="not_initialized"
            )
        else:
            trade_result = self.trade_service.collect_probe(
                self.trade_future, self.remaining()
            )

        return {
            "status": aggregate_status(quote_result, trade_result),
            "host": f"{self.service.host}:{self.service.port}",
            "checked_at": utc_now_iso(),
            "quote": quote_result,
            "trade": trade_result,
            "trading_mode": (
                self.trade_service.policy.mode.value if self.trade_service else None
            ),
            "gateway_version": quote_result.get("gateway_version"),
        }
