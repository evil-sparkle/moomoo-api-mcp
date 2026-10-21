"""Read-only documented capabilities and bounded callback observations."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import StrictInt

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.annotations import READ_ONLY_TOOL
from moomoo_mcp.tools.offload import run_blocking
from moomoo_mcp.tools.serialization import serialize_identifiers
from moomoo_mcp.tools.trading_contract import ExactAccountId, TradingEnvironment


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_trading_capabilities(
    ctx: Context[ServerSession, AppContext],
    code: str,
    trd_env: TradingEnvironment,
    acc_id: ExactAccountId = "0",
) -> dict[str, Any]:
    """Inspect documented support, server permission and advisory readiness.

    Reads account and instrument facts without unlocking or dispatching orders.
    Unknown support remains null; market permission and READY never guarantee
    broker acceptance. REAL and SIMULATE are explicit. Paper broker NORMAL/MARKET
    support is separate from this executor's NORMAL/DAY/RTH US equity scope.
    Deal callbacks/queries and order fees are unavailable in paper. Approval,
    limits, price and quantity validation are enforced again during mutations.
    Use exact string account IDs returned by get_accounts; '0' resolves only
    when one discovered account matches the environment. submission_policy reports
    no local RTH submission-time gate: execution eligibility is distinct from
    authorized submission timing, broker acceptance, DAY validity and fills.
    This read grants no trading authority and schedules no future submission.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            service.get_trading_capabilities, code=code, trd_env=trd_env, acc_id=acc_id
        )
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_trade_events(
    ctx: Context[ServerSession, AppContext],
    trd_env: TradingEnvironment,
    acc_id: ExactAccountId = "0",
    after_sequence: StrictInt = 0,
    event_epoch: str | None = None,
    limit: StrictInt = 100,
) -> dict[str, Any]:
    """Read process-local broker callback observations for one exact account.

    Orders are observed for REAL and SIMULATE; deals only for REAL. The ring
    retains at most 1024 observations for fifteen minutes across accounts.
    Use returned next_sequence and event_epoch together to continue, with a
    limit from 1 to 200. Retention/restart gaps and reconnect_count disclose
    possible missed observations. Callback absence never proves no order or
    fill occurred. This is not a complete audit stream or journal recovery
    evidence. Callback order may precede the placement response; observations
    retain broker IDs without inventing caller operation correlation.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            service.get_trade_events,
            trd_env=trd_env,
            acc_id=acc_id,
            after_sequence=after_sequence,
            event_epoch=event_epoch,
            limit=limit,
        )
    )
