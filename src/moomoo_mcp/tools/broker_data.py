"""Bounded broker facts, separate from strategy and account policy."""

from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession
from pydantic import Field, StrictStr

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.data_evidence import DataEvidence
from moomoo_mcp.tools.annotations import READ_ONLY_TOOL
from moomoo_mcp.tools.offload import run_blocking

Codes = Annotated[list[StrictStr], Field(min_length=1, max_length=100)]
DataLimit = Annotated[int, Field(strict=True, ge=1, le=1000)]


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_market_snapshot_evidence(
    ctx: Context[ServerSession, AppContext], codes: Codes
) -> DataEvidence:
    """Get bounded snapshots with identifier coverage and UTC receipt times.

    Legacy get_market_snapshot is unchanged. Provider bid/ask/sizes/update_time
    remain verbatim; unknown delay, entitlement and per-side age stay unknown.
    A successful response is not evidence of NBBO, freshness or fillability.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_market_snapshot_evidence,
        codes,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_instrument_facts(
    ctx: Context[ServerSession, AppContext], codes: Codes
) -> DataEvidence:
    """Read contract facts using the existing InstrumentAdapter cache and new snapshots.

    Monetary multiplier, deliverable and lot size remain separate. Each fact
    has source, observation time and KNOWN/UNKNOWN/CONFLICT state. No hardcoded
    multiplier, symbol-derived currency or inferred settlement/cutoff. Static
    metadata may be cached for 300s; snapshot facts are requested anew. Prior
    successful chain discoveries can enrich exact-code facts for the same TTL,
    with their original observation time; this tool never queries a chain.
    Chain strike_price and snapshot option_strike_price describe the strike,
    not the premium multiplier. Unknown lifecycle/deliverable facts cannot
    establish compatibility for consumer simulation. AM/PM is not a verified
    settlement cutoff; STANDARD does not establish complete deliverable components.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_instrument_facts,
        codes,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_history_kl_quota(ctx: Context[ServerSession, AppContext]) -> DataEvidence:
    """Observe aggregate historical K-line quota without security details.

    Used and remaining counts are provider observations, not reservations.
    Missing is unknown; zero is a valid exhausted quota. Distinct from quote
    subscriptions and endpoint frequency limits. Reuse existing candle paging.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_history_kl_quota
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_rehab(
    ctx: Context[ServerSession, AppContext], code: str, limit: DataLimit = 200
) -> DataEvidence:
    """Read stock adjustment factors, bounded to 1–1000 returned rows.

    At most 2000 provider rows/2 MiB are accepted; excess fails explicitly.
    Truncation is explicit. Preserve factor bases, dates and cash fields; this
    is not a complete dividend/split feed or point-in-time event archive.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_rehab, code, limit
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_corporate_actions_dividends(
    ctx: Context[ServerSession, AppContext], code: str, limit: DataLimit = 200
) -> DataEvidence:
    """Read bounded provider dividend records in announcement-date order.

    Dates/status/amount/currency are retained only as supplied; no parsing of
    prose to invent money or timing. Empty dividend_list means no returned
    events, not complete historical coverage. Missing list fails explicitly.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_corporate_actions_dividends,
        code,
        limit,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_earnings_calendar(
    ctx: Context[ServerSession, AppContext],
    market: Literal["US", "HK", "SH", "SZ", "SG", "JP", "AU", "CA"],
    start: str,
    end: str,
    limit: DataLimit = 200,
) -> DataEvidence:
    """Read a market's earnings calendar over explicit dates at most 7 days apart.

    Provider order/timing and actual versus estimated fields are preserved.
    Returns at most limit rows with explicit truncation; no auto-pagination.
    Event/fiscal dates are not historical information-availability evidence.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_earnings_calendar,
        market,
        start,
        end,
        limit,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_option_volatility(
    ctx: Context[ServerSession, AppContext],
    code: str,
    period: Literal["WEEK", "MONTH", "QUARTER", "HALF_YEAR", "YEAR"] = "MONTH",
    hv_days: Annotated[int, Field(strict=True, ge=5, le=250)] = 30,
    limit: DataLimit = 200,
) -> DataEvidence:
    """Read dated option IV/HV in percentage points (25 means 25%, not .25).

    Copy an exact discovered option code; provider classification must be DRVT.
    Underlying stock codes are rejected. HV lookback 5–250 days, default 30.
    Missing values stay missing; no interpolation or forward-fill. Annualization,
    publication/revision and expired-contract coverage remain unverified.
    This is historical context, not an undervaluation or expected-return signal.
    """
    return await run_blocking(
        ctx.request_context.lifespan_context.market_data_service.get_option_volatility,
        code,
        period,
        hv_days,
        limit,
    )
