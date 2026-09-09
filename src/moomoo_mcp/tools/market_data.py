
"""Market data tools for retrieving stock quotes, K-lines, snapshots, and order book."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.services.clock import utc_now_iso


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
    quotes = market_data_service.get_stock_quote(codes)
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
        start: Start date in YYYY-MM-DD format. Defaults to 365 days before end.
        end: End date in YYYY-MM-DD format. Defaults to today.
        max_count: Maximum number of candles to return (default 100, max 1000).
        autype: Adjustment type for splits/dividends:
            - QFQ: Forward adjustment (default)
            - HFQ: Backward adjustment
            - NONE: No adjustment

    Returns:
        List of K-line dictionaries containing:
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
    klines = market_data_service.get_historical_klines(
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
    expirations = market_data_service.get_option_expiration_date(code)
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
    contracts = market_data_service.get_option_chain(
        code=code,
        start=start,
        end=end,
        option_type=option_type,
    )
    await ctx.info(f"Retrieved {len(contracts)} option contracts for {code}")
    return contracts


@mcp.tool()
async def get_market_snapshot(
    ctx: Context[ServerSession, AppContext],
    codes: list[str],
) -> list[dict]:
    """Get market snapshot for multiple stocks efficiently.

    This is ideal for checking current status of a watchlist without subscription.
    Returns comprehensive market data including price, volume, and fundamentals.

    Args:
        codes: List of stock codes (up to 400). E.g., ['US.AAPL', 'US.TSLA', 'HK.00700'].

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
    snapshots = market_data_service.get_market_snapshot(codes)
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
    order_book = market_data_service.get_order_book(code, num=num)
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
    states = market_data_service.get_market_state(codes)
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
    days = market_data_service.get_trading_days(market=market, start=start, end=end)
    await ctx.info(f"Retrieved {len(days)} trading days for {market}")
    return {
        "market": market,
        "date_basis": "market_local",
        "start": start,
        "end": end,
        "data": days,
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
    groups = market_data_service.get_user_security_group(group_type=group_type)
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
    securities = market_data_service.get_user_security(group_name)
    await ctx.info(f"Retrieved {len(securities)} securities from group '{group_name}'")
    return securities
