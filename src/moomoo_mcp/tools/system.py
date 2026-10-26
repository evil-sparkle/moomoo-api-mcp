"""System tools for server and gateway health."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.client_authorization import (
    client_authorization_status,
    current_client_grants,
    visible_journal_health,
)
from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.annotations import READ_ONLY_TOOL
from moomoo_mcp.tools.offload import await_futures
from moomoo_mcp.tools.trading_contract import server_contract_identity


@mcp.tool(annotations=READ_ONLY_TOOL)
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
          'error' when the probe failed. Restricted clients omit the global
          trading account count.
        - trading_mode: This server's configured MOOMOO_TRADING_MODE
          ('READ_ONLY', 'SIMULATE', or 'REAL'). It says which writes the server
          will issue at all, which is separate from whether the gateway would
          accept one.
        - trade_market: The configured MOOMOO_TRADING_MARKET discovery filter
          ('NONE' for all securities markets or one named market). It reports
          configuration, not account availability or trading permission.
        - execution_journal: Journal readiness, pending_operation_ids and durable
          recovery_updates, including actual check times and assumed-absence decisions.
          Background recovery reports its result on the next tool reply.
        - real_execution_journal: Independent REAL readiness, admission epoch,
          pending IDs and broker recovery evidence. REAL recovery never assumes
          absence or automatically resubmits an operation.
        - gateway_version: OpenD version when the gateway reports one, else null.
        - gateway_lock_at_rest: Last connect/reconnect lock attempt status
          ('not_applicable', 'not_attempted', 'succeeded', or 'failed') and nullable
          checked_at. Historical observation, not current gateway lock state.
          SIMULATE does not request a lock and reports 'not_applicable'.
        - mcp_server: Package and contract versions, source_revision (unknown when
          unavailable), and SHA-256 of the currently authorized tools/list contract.
          Compare with the served tools/list using the same credential; this does
          not attest to an external host cache or refresh it.
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
    journal_future = trade_service.start_journal_health()
    real_journal_future = trade_service.start_real_journal_health()
    await await_futures(
        (*check.futures, journal_future, real_journal_future), check.remaining()
    )
    status = check.result()
    if current_client_grants() is not None and isinstance(status.get("trade"), dict):
        # The probe counts every broker account, without environment or account
        # identities. Retain connectivity without exposing that global count.
        status["trade"] = {
            key: value
            for key, value in status["trade"].items()
            if key != "account_count"
        }
    status["execution_journal"] = trade_service.collect_journal_health(journal_future)
    status["real_execution_journal"] = trade_service.collect_real_journal_health(
        real_journal_future
    )
    status["execution_journal"] = visible_journal_health(
        status["execution_journal"], "SIMULATE"
    )
    status["real_execution_journal"] = visible_journal_health(
        status["real_execution_journal"], "REAL"
    )
    status["client_authorization"] = client_authorization_status()
    status["mcp_server"] = server_contract_identity(await mcp.list_tools())

    await ctx.info(f"Health check status: {status.get('status')}")

    return status
