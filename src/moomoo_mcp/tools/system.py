"""System tools for server and gateway health."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.offload import await_futures


@mcp.tool()
async def check_health(ctx: Context[ServerSession, AppContext]) -> dict[str, Any]:
    """Check connectivity to the Moomoo OpenD gateway and MCP server health.

    Actively probes the quote and trade connections with read-only calls rather
    than reporting on the existence of a connection object, and completes within
    a five-second deadline.

    A `connected` result means the gateway answered both probes. It does NOT
    mean trading is unlocked, that an order would be accepted, or that any
    particular market is authorized for the account.

    Returns:
        Dictionary containing:
        - status: 'connected' (both probes succeeded), 'degraded' (exactly one
          succeeded), or 'disconnected' (neither succeeded).
        - host: Configured OpenD endpoint as 'host:port'.
        - checked_at: UTC observation time (ISO-8601, 'Z' suffix).
        - quote / trade: Per-service results, each with a status of 'ok',
          'error', 'timeout', or 'unavailable', plus a 'reason' and a sanitized
          'error' when the probe failed.
        - trading_mode: This server's configured MOOMOO_TRADING_MODE
          ('READ_ONLY', 'SIMULATE', or 'REAL'). It says which writes the server
          will issue at all, which is separate from whether the gateway would
          accept one.
        - gateway_version: OpenD version when the gateway reports one, else null.
    """
    lifespan_context = ctx.request_context.lifespan_context
    moomoo_service = lifespan_context.moomoo_service
    trade_service = lifespan_context.trade_service

    # Both probes start immediately on their own dedicated workers, so health
    # never queues behind ordinary SDK traffic, and the deadline covers every
    # wait from this point on. Awaiting the probe futures directly — rather than
    # parking a shared worker thread on them — keeps the event loop free to
    # answer while a stuck gateway is still being diagnosed.
    check = moomoo_service.start_health_check(trade_service=trade_service)
    await await_futures(check.futures, check.remaining())
    status = check.result()

    await ctx.info(f"Health check status: {status.get('status')}")

    return status
