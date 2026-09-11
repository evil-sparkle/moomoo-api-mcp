"""Market data tools for retrieving stock quotes, K-lines, snapshots, and order book."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.market_data_service import validate_candle_filters
from moomoo_mcp.tools.kline_cursor import (
    decode_cursor,
    encode_cursor,
    resolve_date_range,
)
from moomoo_mcp.tools.offload import run_blocking


@mcp.tool()
async def get_stock_quote(
    ctx: Context[ServerSession, AppContext],
    codes: list[str],
) -> list[dict]:
    """Get real-time stock quotes for specified codes.

    Returns current price, open, high, low, volume, and other quote data.
    Automatically subscribes to the stocks before fetching quotes.

    Args:
        codes: List of stock codes (e.g., ['US.AAPL', 'HK.00700']).

    Returns:
        List of quote dictionaries containing:
        - code: Stock code
        - last_price: Latest price
        - open_price: Open price
        - high_price: High price
        - low_price: Low price
        - prev_close_price: Previous close
        - volume: Trading volume
        - turnover: Turnover amount
        - And other quote fields
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    quotes = await run_blocking(market_data_service.get_stock_quote, codes)
    await ctx.info(f"Retrieved quotes for {len(codes)} stocks")
    return quotes


@mcp.tool()
async def get_historical_klines(
    ctx: Context[ServerSession, AppContext],
    code: str,
    ktype: str = "K_DAY",
    start: str | None = None,
    end: str | None = None,
    max_count: int = 100,
    autype: str = "QFQ",
) -> list[dict]:
    """Get historical candlestick (K-line) data for a stock.

    Returns OHLCV (Open, High, Low, Close, Volume) data for technical analysis.

    Args:
        code: Stock code (e.g., 'US.AAPL').
        ktype: K-line type. Options:
            - K_1M: 1 minute
            - K_3M: 3 minutes
            - K_5M: 5 minutes
            - K_15M: 15 minutes
            - K_30M: 30 minutes
            - K_60M: 60 minutes
            - K_DAY: Daily (default)
            - K_WEEK: Weekly
            - K_MON: Monthly
            - K_QUARTER: Quarterly
            - K_YEAR: Yearly
            An unrecognized value is rejected, not quietly replaced with daily
            candles.
        start: Start date in YYYY-MM-DD format. Defaults to 365 days before end.
        end: End date in YYYY-MM-DD format. Defaults to today.
        max_count: Maximum number of candles to return (default 100, max 1000).
        autype: Adjustment type for splits/dividends:
            - QFQ: Forward adjustment (default)
            - HFQ: Backward adjustment
            - NONE: No adjustment
            An unrecognized value is rejected, not quietly replaced with QFQ.

    Returns:
        A SINGLE PAGE of K-line dictionaries. The provider's continuation token
        is discarded here, so for a wide date range this list can be a prefix of
        the range rather than all of it, with no indication that more exists.
        Use get_historical_klines_page when completeness matters.

        Each dictionary contains:
        - time_key: Candlestick timestamp
        - open: Open price
        - high: High price
        - low: Low price
        - close: Close price
        - volume: Volume
        - turnover: Turnover
        - change_rate: Price change rate
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    klines = await run_blocking(
        market_data_service.get_historical_klines,
        code=code,
        ktype=ktype,
        start=start,
        end=end,
        max_count=max_count,
        autype=autype,
    )
    await ctx.info(f"Retrieved {len(klines)} K-lines for {code}")
    return klines


@mcp.tool()
async def get_option_expiration_date(
    ctx: Context[ServerSession, AppContext],
    code: str,
) -> list[dict]:
    """List the option expiration dates available for an underlying.

    Start here when building an option strategy: pick an expiry from this list,
    then call get_option_chain for that single date to get the exact contract
    symbols. Querying a chain without narrowing the expiry returns a much larger
    response.

    Args:
        code: Underlying security code (e.g., 'US.AAPL', 'HK.00700').

    Returns:
        List of expiration dictionaries containing:
        - strike_time: The expiration date, in the market's own timezone
          (US market dates are US Eastern; HK and A-share dates are Beijing).
        - option_expiry_date_distance: Days until expiry; negative if expired.
        - expiration_cycle: Settlement cycle (HK index options only).

        An empty list means the provider reported no expirations for this
        underlying. That is a successful result, not an error — a provider
        failure or a missing permission raises instead.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    expirations = await run_blocking(
        market_data_service.get_option_expiration_date, code
    )
    await ctx.info(f"Retrieved {len(expirations)} option expirations for {code}")
    return expirations


@mcp.tool()
async def get_option_chain(
    ctx: Context[ServerSession, AppContext],
    code: str,
    start: str | None = None,
    end: str | None = None,
    option_type: str = "ALL",
) -> list[dict]:
    """Get option contracts for an underlying within a range of expiry dates.

    Use the returned `code` values verbatim as leg symbols for
    preview_combo_order and place_combo_order, and as codes for get_stock_quote.
    Do not construct an option symbol by hand.

    Args:
        code: Underlying security code (e.g., 'US.AAPL').
        start: First expiration date to include, 'YYYY-MM-DD'. Set start and end
            to the same date to fetch one expiry, which is usually what you
            want. Omit both and the provider uses today plus 30 days.
        end: Last expiration date to include, 'YYYY-MM-DD' (inclusive).
        option_type: 'ALL' (default), 'CALL', or 'PUT'.

    Returns:
        List of contract dictionaries containing:
        - code: The exact contract symbol to use in other tools.
        - name: Contract name.
        - stock_owner: The underlying's code.
        - option_type: 'CALL' or 'PUT'.
        - strike_time: Expiration date, in the market's own timezone.
        - strike_price: Strike price.
        - lot_size: Contract multiplier / lot size.
        - suspension, stock_id, index_option_type, expiration_cycle,
          option_standard_type, option_settlement_mode.

        An empty list means no contracts matched — a successful result. A
        provider rejection (unsupported underlying, missing options permission,
        gateway failure) raises an error instead, so the two are never confused.

    Note:
        The provider accepts a range of at most 30 days. A wider range is
        rejected with an error rather than being silently truncated, so you are
        never handed a partial chain believing it is complete.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    contracts = await run_blocking(
        market_data_service.get_option_chain,
        code=code,
        start=start,
        end=end,
        option_type=option_type,
    )
    await ctx.info(f"Retrieved {len(contracts)} option contracts for {code}")
    return contracts


@mcp.tool()
async def get_historical_klines_page(
    ctx: Context[ServerSession, AppContext],
    code: str,
    ktype: str = "K_DAY",
    start: str | None = None,
    end: str | None = None,
    max_count: int = 100,
    autype: str = "QFQ",
    cursor: str | None = None,
) -> dict[str, Any]:
    """Get historical candles one page at a time, with explicit continuation.

    Use this instead of get_historical_klines when you need the whole date
    range: get_historical_klines returns a single page and discards the
    provider's continuation, so its list can silently be a prefix of what you
    asked for.

    One call fetches one page. To read a full range, loop:

        page = get_historical_klines_page(code="US.AAPL", start=..., end=...)
        rows = page["data"]
        while page["has_more"]:
            page = get_historical_klines_page(
                code="US.AAPL", start=..., end=..., cursor=page["next_cursor"]
            )
            rows += page["data"]

    Bound that loop. Stop when next_cursor is null, and surface an error rather
    than treating a failed page as the end of the data.

    Args:
        code: Stock code (e.g., 'US.AAPL').
        ktype: K-line type: K_1M, K_3M, K_5M, K_10M, K_15M, K_30M, K_60M,
            K_120M, K_180M, K_240M, K_DAY (default), K_WEEK, K_MON, K_QUARTER,
            K_YEAR. An unrecognized value is rejected rather than quietly
            treated as daily.
        start: Start date 'YYYY-MM-DD'. Defaults to 365 days before end.
        end: End date 'YYYY-MM-DD'. Defaults to today.
        max_count: Maximum candles per page (default 100).
        autype: Adjustment for splits/dividends: 'QFQ' (forward, default),
            'HFQ' (backward), or 'NONE'. An unrecognized value is rejected
            rather than quietly treated as QFQ.
        cursor: The next_cursor from the previous page. Omit for the first page.
            Pass every other argument unchanged alongside it — a cursor belongs
            to one specific query, and changing a filter mid-traversal is a new
            query, not a continuation of this one.

    Returns:
        Dictionary containing:
        - data: List of candle dictionaries (time_key, open, high, low, close,
          volume, turnover, change_rate), in the provider's order.
        - next_cursor: Opaque cursor for the following page, or null when the
          range is exhausted.
        - has_more: True when next_cursor is non-null.

        An empty data list with has_more=true is possible and does not mean the
        range is finished — keep going until next_cursor is null.

    Note:
        When start or end is omitted, the range is resolved once on the first
        page and carried in the cursor, so a traversal that crosses midnight
        keeps reading the same window. A malformed cursor, or one from a
        different query, is rejected before any provider request.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service

    # Before touching a cursor: an unsupported interval or adjustment must not
    # be bound into one, and the error should name the bad filter rather than
    # surfacing later as a confusing cursor mismatch.
    validate_candle_filters(ktype, autype)

    query = {
        "code": code,
        "ktype": ktype,
        "start": start,
        "end": end,
        "max_count": max_count,
        "autype": autype,
    }

    if cursor is None:
        page_req_key = None
        resolved = resolve_date_range(start, end)
    else:
        page_req_key, resolved = decode_cursor(cursor, query)

    resolved_start, resolved_end = resolved
    rows, next_token = await run_blocking(
        market_data_service.get_historical_klines_page,
        code=code,
        ktype=ktype,
        start=resolved_start,
        end=resolved_end,
        max_count=max_count,
        autype=autype,
        page_req_key=page_req_key,
    )

    next_cursor = (
        encode_cursor(next_token, query, resolved) if next_token is not None else None
    )
    await ctx.info(f"Retrieved {len(rows)} K-lines for {code}")
    return {
        "data": rows,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


@mcp.tool()
async def get_market_snapshot(
    ctx: Context[ServerSession, AppContext],
    codes: list[str],
) -> list[dict]:
    """Get market snapshot for multiple stocks efficiently.

    This is ideal for checking current status of a watchlist without subscription.
    Returns comprehensive market data including price, volume, and fundamentals.

    Args:
        codes: List of stock codes (up to 400).
            E.g., ['US.AAPL', 'US.TSLA', 'HK.00700'].

    Returns:
        List of snapshot dictionaries containing:
        - code: Stock code
        - name: Stock name
        - last_price: Latest price
        - open_price: Open price
        - high_price: High price
        - low_price: Low price
        - prev_close_price: Previous close
        - volume: Trading volume
        - turnover: Turnover amount
        - turnover_rate: Turnover rate (%)
        - pe_ratio: P/E ratio
        - pb_ratio: P/B ratio
        - And many more fields
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    snapshots = await run_blocking(market_data_service.get_market_snapshot, codes)
    await ctx.info(f"Retrieved snapshots for {len(codes)} stocks")
    return snapshots


@mcp.tool()
async def get_order_book(
    ctx: Context[ServerSession, AppContext],
    code: str,
    num: int = 10,
) -> dict:
    """Get order book (market depth) showing bid/ask price levels.

    Returns the top N bid and ask levels with prices and volumes.
    Useful for analyzing liquidity and market sentiment.
    Automatically subscribes to the stock before fetching order book.

    Args:
        code: Stock code (e.g., 'HK.00700', 'US.AAPL').
        num: Number of price levels to return (default 10).

    Returns:
        Dictionary containing:
        - code: Stock code
        - Bid: List of bid levels, each as (price, volume, order_count, details)
        - Ask: List of ask levels, each as (price, volume, order_count, details)
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    order_book = await run_blocking(market_data_service.get_order_book, code, num=num)
    await ctx.info(f"Retrieved order book for {code} with {num} levels")
    return order_book


@mcp.tool()
async def get_market_state(
    ctx: Context[ServerSession, AppContext],
    codes: list[str],
) -> dict[str, Any]:
    """Get the current session state of each instrument's market.

    Reports what the provider observes right now. It is not a schedule: it says
    nothing about when the next session begins, and it does not mean the account
    is permitted to trade the instrument.

    Args:
        codes: Security codes (e.g., ['US.AAPL', 'HK.00700']).

    Returns:
        Dictionary containing:
        - checked_at: UTC observation time (ISO-8601, 'Z' suffix). The state was
          true as of this moment and can change at any session boundary.
        - data: List of {code, stock_name, market_state} dictionaries.

        market_state is the provider's own value — for example 'MORNING',
        'REST' (midday break), 'AFTERNOON', 'CLOSED', 'PRE_MARKET_BEGIN',
        'AFTER_HOURS_BEGIN', 'AUCTION'. These are preserved rather than reduced
        to open/closed, because a pre-market session and a regular session do
        not accept the same orders. Instruments in different markets can be in
        different states in the same response.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    states = await run_blocking(market_data_service.get_market_state, codes)
    await ctx.info(f"Retrieved market state for {len(codes)} instruments")
    return {"checked_at": utc_now_iso(), "data": states}


@mcp.tool()
async def get_trading_days(
    ctx: Context[ServerSession, AppContext],
    market: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Get a market's trading calendar for a date range.

    Use this instead of assuming weekdays are trading days: holidays and
    shortened sessions vary by market and are not derivable from the server's
    clock or timezone.

    Args:
        market: Calendar market. One of 'HK', 'US', 'CN', 'NT' (Shenzhen/Shanghai
            Connect), 'ST' (Stock Connect), 'JP', 'SG', 'MY', 'JP_FUTURE',
            'SG_FUTURE'.
        start: First date to include, 'YYYY-MM-DD'. Omit both dates for the 365
            days ending today.
        end: Last date to include, 'YYYY-MM-DD' (inclusive).

    Returns:
        Dictionary containing:
        - market: The market queried.
        - date_basis: Always 'market_local'. The dates below are calendar dates
          in the market's own timezone, not the server's, and carry no time of
          day.
        - start / end: The requested bounds, echoed back (null when omitted and
          resolved by the provider).
        - data: List of {time, trade_date_type} dictionaries, one per trading
          day. trade_date_type is 'WHOLE' for a full session, or a half-day
          value such as 'MORNING' or 'AFTERNOON'.

        Only trading days appear. A holiday is simply absent — it is not listed
        and marked closed. Session opening and closing times are not part of
        this response and must not be inferred from it.

        A date appearing here does NOT mean a particular instrument trades that
        day, or that the account is permitted to trade it. Combine with
        get_market_state for the current session and with account permissions
        for what may actually be traded.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    days = await run_blocking(
        market_data_service.get_trading_days, market=market, start=start, end=end
    )
    await ctx.info(f"Retrieved {len(days)} trading days for {market}")
    return {
        "market": market,
        "date_basis": "market_local",
        "start": start,
        "end": end,
        "data": days,
    }


# The provider reports some usage figures for this connection and others across
# every client attached to the same OpenD. Splitting them makes the scope of
# each number explicit instead of leaving a caller to guess.
_CONNECTION_QUOTA_FIELDS = {
    "own_used": "used_quota",
    "own_option_used_quota": "option_used_quota",
    "own_security_firm": "security_firm",
}
_PROVIDER_QUOTA_FIELDS = {
    "total_used": "total_used",
    "remain": "remain",
    "option_used_quota": "option_used_quota",
    "option_remain_quota": "option_remain_quota",
}


@mcp.tool()
async def get_subscriptions(
    ctx: Context[ServerSession, AppContext],
) -> dict[str, Any]:
    """List the market-data subscriptions held by this server's connection.

    get_stock_quote and get_order_book subscribe automatically, so symbols
    appear here after you read them. Use this to see what is being held before
    releasing anything with unsubscribe_market_data.

    Returns:
        Dictionary containing:
        - checked_at: UTC observation time (ISO-8601, 'Z' suffix).
        - connection: What THIS server's quote connection holds.
          - subscriptions: {subscription_type: [codes]}.
          - used_quota / option_used_quota / security_firm, when the provider
            reports them.
        - provider: Usage counted across EVERY client attached to the same
          OpenD gateway, not just this server — total_used, remain,
          option_used_quota, option_remain_quota, when reported.

        Only fields the provider actually returns are present. No limit is
        inferred or invented, and an absent quota field means "not reported",
        not "unlimited".

        The provider quota is shared: another client on the same gateway
        consumes from it, so `connection` being small does not mean there is
        headroom.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    report = await run_blocking(market_data_service.get_subscriptions)

    connection: dict[str, Any] = {"subscriptions": report.get("sub_list", {})}
    for source, name in _CONNECTION_QUOTA_FIELDS.items():
        if source in report:
            connection[name] = report[source]

    provider = {
        name: report[source]
        for source, name in _PROVIDER_QUOTA_FIELDS.items()
        if source in report
    }

    held = sum(len(codes) for codes in connection["subscriptions"].values())
    await ctx.info(f"This connection holds {held} subscriptions")
    return {
        "checked_at": utc_now_iso(),
        "connection": connection,
        "provider": provider,
    }


@mcp.tool()
async def unsubscribe_market_data(
    ctx: Context[ServerSession, AppContext],
    codes: list[str],
    sub_types: list[str],
) -> dict[str, Any]:
    """Release specific market-data subscriptions held by this connection.

    Only the codes and types you name are released, and only on this server's
    own connection — another client subscribed to the same security keeps its
    subscription. There is no global release.

    Reading a released symbol again with get_stock_quote or get_order_book
    simply re-subscribes it.

    Args:
        codes: Security codes to release (e.g., ['US.AAPL', 'HK.00700']).
        sub_types: Subscription types to release, e.g. ['QUOTE'] or
            ['QUOTE', 'ORDER_BOOK']. Valid values: QUOTE, ORDER_BOOK,
            ORDER_BOOK_ODD, TICKER, BROKER, RT_DATA, K_1M, K_3M, K_5M, K_10M,
            K_15M, K_30M, K_60M, K_120M, K_180M, K_240M, K_DAY, K_WEEK, K_MON,
            K_QUARTER, K_YEAR.

    Returns:
        Dictionary containing:
        - requested_at: UTC time the release was requested.
        - codes / sub_types: Exactly what was submitted for release.
        - status: 'acknowledged' — the provider accepted the request.
        - scope: 'this_connection'.

        'acknowledged' means the request was accepted, not that provider state
        has already settled. Call get_subscriptions afterwards if you need to
        confirm what is still held.

    Note:
        Providers commonly impose a minimum subscription duration and can refuse
        an early release. That refusal is returned as an error and is not
        retried — re-asking does not shorten a cooldown. An unsupported
        subscription type or an empty selection fails before the request is
        sent.
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    await run_blocking(
        market_data_service.unsubscribe, codes=codes, sub_types=sub_types
    )
    await ctx.info(f"Requested release of {len(codes)} subscriptions")
    return {
        "requested_at": utc_now_iso(),
        "codes": codes,
        "sub_types": sub_types,
        "status": "acknowledged",
        "scope": "this_connection",
    }


@mcp.tool()
async def get_user_security_group(
    ctx: Context[ServerSession, AppContext],
    group_type: int = 0,
) -> list[dict]:
    """Get list of user-defined security groups (watchlists).

    Returns the user's custom watchlist groups from the Moomoo app.

    Args:
        group_type: Type of groups to return. Options:
            - 0: All groups (default)
            - 1: Custom groups only
            - 2: System groups only

    Returns:
        List of group dictionaries containing:
        - group_name: Name of the group
        - group_id: Unique identifier for the group
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    groups = await run_blocking(
        market_data_service.get_user_security_group, group_type=group_type
    )
    await ctx.info(f"Retrieved {len(groups)} security groups")
    return groups


@mcp.tool()
async def get_user_security(
    ctx: Context[ServerSession, AppContext],
    group_name: str,
) -> list[dict]:
    """Get list of securities in a specific user-defined group (watchlist).

    Returns all stocks/securities that the user has added to a specific watchlist.

    Args:
        group_name: Name of the security group (e.g., 'Favorites', 'Tech').

    Returns:
        List of security dictionaries containing:
        - code: Stock code (e.g., 'US.AAPL')
        - name: Stock name
        - lot_size: Lot size for trading
        - stock_type: Type of security
    """
    market_data_service = ctx.request_context.lifespan_context.market_data_service
    securities = await run_blocking(market_data_service.get_user_security, group_name)
    await ctx.info(f"Retrieved {len(securities)} securities from group '{group_name}'")
    return securities
