"""Quote-connection service and the aggregated gateway health check."""

import time
from typing import TYPE_CHECKING, Any

from moomoo import RET_OK, OpenQuoteContext

from moomoo_mcp.services.health import (
    HEALTH_DEADLINE_SECONDS,
    BoundedProbe,
    aggregate_status,
    failure,
    utc_now_iso,
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
        """Initialize connection to OpenD."""
        # OpenQuoteContext connects on initialization
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)

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

    def check_health(
        self,
        trade_service: "TradeService | None" = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Probe quote and trade connectivity and report an observed status.

        Both probes are started together and share one deadline, so the whole
        check is bounded even when both services hang.

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
        if deadline is None:
            deadline = HEALTH_DEADLINE_SECONDS
        started = time.monotonic()

        quote_future = self._quote_probe.submit(self.probe_quote)
        trade_future = trade_service.submit_probe() if trade_service else None

        def remaining() -> float:
            return deadline - (time.monotonic() - started)

        quote_result = self._quote_probe.collect(quote_future, remaining())
        if trade_future is None:
            trade_result = failure(
                "unavailable", "Trade service not configured", reason="not_initialized"
            )
        else:
            trade_result = trade_service.collect_probe(trade_future, remaining())

        return {
            "status": aggregate_status(quote_result, trade_result),
            "host": f"{self.host}:{self.port}",
            "checked_at": utc_now_iso(),
            "quote": quote_result,
            "trade": trade_result,
            "trading_mode": (
                trade_service.policy.mode.value if trade_service else None
            ),
            "gateway_version": quote_result.get("gateway_version"),
        }
