"""Additive typed trading reads; legacy list tools retain their contracts."""

from typing import Annotated

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.data_evidence import DataEvidence, evidence
from moomoo_mcp.services.query_contracts import (
    OrderFeeResult,
    OrderQueryResult,
    QueryPage,
)
from moomoo_mcp.tools.annotations import READ_ONLY_TOOL
from moomoo_mcp.tools.offload import run_blocking

PageSize = Annotated[int, Field(strict=True, ge=1, le=200)]
FeeIdentifiers = Annotated[list[str], Field(min_length=1, max_length=400)]


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_order(
    ctx: Context[ServerSession, AppContext],
    order_id: str,
    source: str = "CURRENT",
    code: str = "",
    start: str = "",
    end: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
) -> OrderQueryResult:
    """Get one exact broker order with explicit account and retrieval scope.

    CURRENT uses the broker's order-ID filter over open/recent terminal orders.
    HISTORY requires explicit start/end dates or timestamps covering at most
    366 days and matches the exact ID locally; its SDK has no order-ID filter.
    found=false means absent from this response's scope, not proof that the
    order never existed. Ambiguous duplicate matches fail explicitly.

    qty/price describe the requested order. dealt_qty/dealt_avg_price describe
    broker-reported executions, including a partially filled cancelled order.
    Missing numeric fields remain null. raw preserves additional broker fields.
    All identifiers are exact strings; pass them unchanged. acc_id='0' requires
    exactly one discovered account. REAL write allowlists do not restrict reads.
    Notify the user before accessing REAL account data; use SIMULATE only when
    explicitly requested. This read never unlocks or mutates the broker.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return await run_blocking(
        service.get_order,
        order_id=order_id,
        source=source,
        code=code,
        start=start,
        end=end,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_order_fees(
    ctx: Context[ServerSession, AppContext],
    order_id_list: FeeIdentifiers,
    trd_env: str = "REAL",
    acc_id: str = "0",
    include_evidence: bool = False,
) -> list[OrderFeeResult] | DataEvidence:
    """Retrieve actual fees for 1–400 exact broker order identifiers.

    Available only for REAL accounts, excluding Moomoo CA. The provider supports
    orders after 2018-01-01 and ten queries per thirty seconds per account.
    These are actual reported fees, not estimates. Missing amounts/details stay
    null; currency is not supplied by this API and is not inferred. Returned
    rows may omit orders whose fees the provider did not report.

    include_evidence=true returns requested/returned/missing identifiers,
    receipt/source evidence and separate amount/detail coverage. The default
    false preserves the legacy list. No total or currency is invented; partial
    results cannot establish the complete charges for a package. Do not double
    count combo parents and legs or assume equal fee allocation across fills.

    Notify the user before accessing REAL account data. acc_id='0' requires
    exactly one matching account. Identifiers are exact strings. This read
    never unlocks or mutates the broker.
    """
    service = ctx.request_context.lifespan_context.trade_service
    started = utc_now_iso()
    rows = await run_blocking(
        service.get_order_fees,
        order_id_list=order_id_list,
        trd_env=trd_env,
        acc_id=acc_id,
    )
    if not include_evidence:
        return rows
    missing_amounts = [row["order_id"] for row in rows if row["fee_amount"] is None]
    missing_details = [
        row["order_id"]
        for row in rows
        if row["fee_details"] is None
        or any(detail["amount"] is None for detail in row["fee_details"])
    ]
    return evidence(
        "order_fee_query",
        started,
        [dict(row) for row in rows],
        requested=list(dict.fromkeys(order_id_list)),
        key="order_id",
        scope={
            "trd_env": trd_env,
            "acc_id_selector": acc_id,
            "currency": None,
            "missing_amount_ids": missing_amounts,
            "missing_detail_ids": missing_details,
            "amounts_complete": len(rows) == len(set(order_id_list))
            and not missing_amounts,
            "details_complete": len(rows) == len(set(order_id_list))
            and not missing_details,
        },
        limitations=[
            "Actual historical charges, not pre-trade estimates.",
            "Currency/attribution need separately authorized linked evidence.",
        ],
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_orders_page(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    status_filter_list: list[str] | None = None,
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
    limit: PageSize = 100,
    cursor: str | None = None,
) -> QueryPage:
    """Get 1–200 normalized current orders from an immutable server snapshot.

    Covers provider open and recent terminal orders, not a calendar-day ledger.
    Repeat all filters/account/environment/limit unchanged with next_cursor.
    Continuations perform no SDK query, use the original fetched_at and expire
    after 120 seconds or cache eviction. Snapshots are limited to 10,000 rows
    and 4 MiB; narrow filters when exceeded. Prices/qty are requested values;
    dealt_avg_price/dealt_qty are actual broker-reported executions. Null means
    unavailable, and raw preserves extra fields. Identifiers are exact strings.

    Notify the user before REAL reads; use SIMULATE only when requested.
    acc_id='0' requires one matching account for the initial fetch. Legacy
    get_orders remains available with its list-shaped broker response.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return await run_blocking(
        service.get_orders_page,
        code=code,
        status_filter_list=status_filter_list,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
        limit=limit,
        cursor=cursor,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_deals_page(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
    limit: PageSize = 100,
    cursor: str | None = None,
) -> QueryPage:
    """Get 1–200 normalized REAL deal rows, with actual fill qty and price.

    SIMULATE deal APIs are unsupported; use order executed quantities there.
    Follow get_orders_page snapshot limits and repeat all arguments unchanged
    with next_cursor. Continuations are local, immutable and expire after 120
    seconds or eviction. This is provider-returned current activity, not proof
    of complete lifetime accounting. Identifiers are exact strings, raw retains
    additional fields, and null means unavailable. Notify the user before REAL
    reads; acc_id='0' requires one matching account for the initial fetch.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return await run_blocking(
        service.get_deals_page,
        code=code,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
        limit=limit,
        cursor=cursor,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_history_orders_page(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    status_filter_list: list[str] | None = None,
    start: str = "",
    end: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
    limit: PageSize = 100,
    cursor: str | None = None,
) -> QueryPage:
    """Get bounded normalized historical order pages with immutable scope.

    Omitted dates use the installed SDK's 90-day rules: both omitted means
    today minus 90 days through today; end only means end minus 90 days; start
    only means start through start plus 90 days. Explicit dates/timestamps scope
    the query and do not establish lifetime completeness or include derivatives
    automatically. Status filters may exclude orders with partial executions.
    qty/price are requested; dealt_qty/dealt_avg_price summarize actual fills,
    including CANCELLED_PART. Individual REAL fills use historical deal tools.

    Follow get_orders_page bounds/cursor rules. Continuations use the first
    snapshot and never re-resolve omitted dates or query the broker. Notify the
    user before REAL reads; use SIMULATE only when requested. Identifiers are
    exact strings; acc_id='0' requires one account for the initial fetch.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return await run_blocking(
        service.get_history_orders_page,
        code=code,
        status_filter_list=status_filter_list,
        start=start,
        end=end,
        trd_env=trd_env,
        acc_id=acc_id,
        limit=limit,
        cursor=cursor,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_history_deals_page(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    start: str = "",
    end: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
    limit: PageSize = 100,
    cursor: str | None = None,
) -> QueryPage:
    """Get bounded historical REAL fills with actual execution qty/price.

    SIMULATE deal APIs are unsupported; query order execution fields there.
    Use get_history_orders_page's omitted-date rules and get_orders_page's
    1–200 page size, scope binding, expiry and snapshot bounds. Repeat filters
    unchanged with next_cursor; no broker query occurs on continuation. An empty
    or successful response does not establish lifetime completeness. Identifiers
    are exact strings and raw preserves extra broker fields. Notify the user
    before REAL reads; acc_id='0' requires one account for the initial fetch.
    """
    service = ctx.request_context.lifespan_context.trade_service
    return await run_blocking(
        service.get_history_deals_page,
        code=code,
        start=start,
        end=end,
        trd_env=trd_env,
        acc_id=acc_id,
        limit=limit,
        cursor=cursor,
    )
