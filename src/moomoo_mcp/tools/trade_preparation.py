"""Client-neutral review and read-only preview tools; no operator decision tool."""

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import StrictInt

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.annotations import MUTATING_TOOL, READ_ONLY_TOOL
from moomoo_mcp.tools.offload import run_blocking
from moomoo_mcp.tools.serialization import serialize_identifiers


@mcp.tool(annotations=READ_ONLY_TOOL)
async def preview_order(
    ctx: Context[ServerSession, AppContext],
    code: str,
    price: str | float,
    qty: StrictInt,
    trd_side: str,
    trd_env: str,
    order_type: str = "NORMAL",
    time_in_force: str = "DAY",
    adjust_limit: float = 0,
    aux_price: float | None = None,
    trail_type: str | None = None,
    trail_value: float | None = None,
    trail_spread: float | None = None,
    acc_id: str = "0",
    session: str = "N/A",
) -> dict:
    """Preview one order without placing, unlocking or admitting it.

    Returns instrument validation, conservative submission value and documented
    maximum quantities. Derivative margin fields are not universal stock account
    impact or a fee estimate. A preview never guarantees acceptance or a fill.
    N/A preserves broker default session selection; paper execution remains RTH.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            service.preview_order,
            code=code,
            price=price,
            qty=qty,
            trd_side=trd_side,
            trd_env=trd_env,
            order_type=order_type,
            time_in_force=time_in_force,
            adjust_limit=adjust_limit,
            aux_price=aux_price,
            trail_type=trail_type,
            trail_value=trail_value,
            trail_spread=trail_spread,
            acc_id=acc_id,
            session=session,
        )
    )


@mcp.tool(annotations=MUTATING_TOOL)
async def prepare_trade_request(
    ctx: Context[ServerSession, AppContext],
    method: str,
    parameters: dict,
    ttl_seconds: StrictInt = 300,
) -> dict:
    """Persist exact review intent without any broker mutation or unlock.

    method is place_order, place_combo_order, modify_order or cancel_order.
    parameters use that tool's arguments, including caller-owned operation_id
    and the current environment admission_epoch. The returned parameters name an
    explicit account. Show the complete review to the operator; only the separate
    authenticated operator channel can approve or reject it. An agent cannot
    grant approval through a tool or an approved=true field. After approval call
    the original mutation with these parameters plus approval_id=request_id.
    Preserve operation_id/admission_epoch and the exact request across retries.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            service.prepare_trade_request,
            method=method,
            parameters=parameters,
            ttl_seconds=ttl_seconds,
        )
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_prepared_trade(
    ctx: Context[ServerSession, AppContext],
    request_id: str,
) -> dict:
    """Read immutable intent and trusted decision status; never grants approval."""
    return serialize_identifiers(
        await run_blocking(
            ctx.request_context.lifespan_context.trade_service.get_prepared_trade,
            request_id=request_id,
        )
    )
