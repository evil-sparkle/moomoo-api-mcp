"""Client-neutral review and read-only preview tools; no operator decision tool."""

from typing import Annotated

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field, StrictInt

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.annotations import MUTATING_TOOL, READ_ONLY_TOOL
from moomoo_mcp.tools.offload import run_blocking
from moomoo_mcp.tools.serialization import serialize_identifiers
from moomoo_mcp.tools.trading_contract import (
    ExactAccountId,
    ExecutionSession,
    OrderPrice,
    OrderQuantity,
    TimeInForce,
    TradingEnvironment,
)


@mcp.tool(annotations=READ_ONLY_TOOL)
async def preview_order(
    ctx: Context[ServerSession, AppContext],
    code: str,
    price: OrderPrice,
    qty: OrderQuantity,
    trd_side: str,
    trd_env: TradingEnvironment,
    order_type: str = "NORMAL",
    time_in_force: TimeInForce = "DAY",
    adjust_limit: float = 0,
    aux_price: float | None = None,
    trail_type: str | None = None,
    trail_value: float | None = None,
    trail_spread: float | None = None,
    acc_id: ExactAccountId = "0",
    session: ExecutionSession = "N/A",
) -> dict:
    """Preview one order without placing, unlocking or admitting it.

    Returns instrument validation, conservative submission value and documented
    maximum quantities. Derivative margin fields are not universal stock account
    impact or a fee estimate. A preview never guarantees acceptance or a fill.
    N/A preserves broker default session selection; paper execution remains RTH.
    RTH is execution eligibility, not a local submission-time gate. Outside-RTH
    submission needs appropriate authorization; broker acceptance, DAY validity
    and fills remain unknown. No local future submission is scheduled.
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
    method: Annotated[
        str,
        Field(
            description="place_order, place_combo_order, modify_order or cancel_order"
        ),
    ],
    parameters: Annotated[
        dict,
        Field(
            description="Exact arguments from the selected mutation's tools/list "
            "schema, with operation_id generated once per new authorized intent "
            "and admission_epoch copied from its environment's health journal. "
            "Both tokens: nonblank printable strings of 1-64 characters. Paper "
            "price: positive plain decimal STRING; qty: positive whole shares. "
            "Preserve decimal-string account/broker IDs. No approval_id on a new "
            "preparation; only the separate operator channel can approve."
        ),
    ],
    ttl_seconds: StrictInt = 300,
) -> dict:
    """Persist exact review intent without any broker mutation or unlock.

    method is place_order, place_combo_order, modify_order or cancel_order.
    parameters use that tool's arguments, including caller-owned operation_id
    and the correct environment admission_epoch copied from check_health. Generate
    operation_id once per authorized intent (for example uuid.uuid4().hex); both
    tokens are nonblank printable strings of 1-64 characters. Consult tools/list
    for the selected mutation schema; paper prices are positive plain decimal
    strings and quantities are positive whole shares. Never replace an admitted
    or uncertain identity, refresh its epoch, or transfer an approval to a new ID.
    This existing durable review route stores the exact intent; it does not trade.
    The returned parameters name an
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
