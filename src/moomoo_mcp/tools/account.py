"""Account tools for trading account information retrieval."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.offload import run_blocking
from moomoo_mcp.tools.serialization import serialize_identifiers


@mcp.tool()
async def get_accounts(
    ctx: Context[ServerSession, AppContext],
    market: str | None = None,
    trd_env: str | None = None,
) -> list[dict]:
    """Get accounts available through this process's configured trade context.

    Optional filters are applied to this response only. ``market="NONE"``
    means all markets returned by the provider context. A named market such as
    ``"US"`` matches account market authorizations; ``trd_env`` accepts
    ``"REAL"`` or ``"SIMULATE"``. These filters neither change the shared
    connection nor grant trading permission. Use the exact returned account ID
    with subsequent read or trading tools.

    IMPORTANT: This returns both REAL and SIMULATE accounts. Listing accounts is
    a read and never requires unlocking. Reading a REAL account's data through
    the other tools may require unlock_trade, but attempt the read first — see
    unlock_trade for why calling it up front can fail unnecessarily.

    Returns:
        List of account dictionaries containing acc_id, trd_env, and other
        provider metadata. No matching accounts returns an empty list; provider
        errors remain errors.

    Args:
        market: Optional securities market filter: NONE, HK, US, CN, HKCC, SG,
            AU, JP, MY, or CA. Omit it to retain all provider-returned markets.
        trd_env: Optional account environment filter: REAL or SIMULATE.

        NOTE: acc_id is returned as a decimal STRING, not a number. It is a
        64-bit value that a JSON client parsing numbers as doubles would
        silently round, and a rounded id addresses a different account. Pass it
        to other tools exactly as received, as a string, without converting it
        to a number at any point.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    accounts = await run_blocking(
        trade_service.get_accounts,
        market=market,
        trd_env=trd_env,
    )
    await ctx.info(f"Retrieved {len(accounts)} accounts")
    return serialize_identifiers(accounts)


@mcp.tool()
async def get_account_summary(
    ctx: Context[ServerSession, AppContext],
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> dict[str, Any]:
    """Get complete account summary including assets and positions in one call.

    This is the recommended tool for getting a full view of an account's status.
    It combines get_assets and get_positions into a single response.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Reading REAL account data may require unlock_trade, depending on the
      gateway. Try the read FIRST and only call unlock_trade if it fails
      asking to be unlocked. Do not unlock pre-emptively: in the default
      READ_ONLY trading mode unlock is denied outright, so calling it first
      turns a read that would have worked into a policy error.
    - If user wants SIMULATE account, they must explicitly request it.

    Use an exact string ID from get_accounts for the requested trd_env.
    acc_id='0' requires exactly one matching account; otherwise the read fails.
    REAL write allowlists do not restrict reads.

    Args:
        trd_env: Trading environment. 'REAL' (default) or 'SIMULATE' (for
            testing). See the note above on unlocking before reading REAL data.
        acc_id: Account ID. Must be obtained from get_accounts().

    Returns:
        Dictionary with 'assets' (cash, market_val, etc.) and 'positions'
        (list of holdings).

        NOTE: acc_id, position_id, and combo_id are returned as decimal STRINGS
        throughout, including inside the nested positions. Balances and
        quantities remain numbers. Pass identifiers on unchanged, as strings.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    summary = await run_blocking(
        trade_service.get_account_summary, trd_env=trd_env, acc_id=acc_id
    )
    await ctx.info(
        f"Retrieved summary for {trd_env} account: "
        f"{len(summary['positions'])} positions"
    )
    return serialize_identifiers(summary)


@mcp.tool()
async def get_assets(
    ctx: Context[ServerSession, AppContext],
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
    currency: str | None = None,
) -> dict[str, Any]:
    """Get account assets including cash, market value, buying power.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Reading REAL account data may require unlock_trade, depending on the
      gateway. Try the read FIRST and only call unlock_trade if it fails
      asking to be unlocked. Do not unlock pre-emptively: in the default
      READ_ONLY trading mode unlock is denied outright, so calling it first
      turns a read that would have worked into a policy error.
    - If user wants SIMULATE account, they must explicitly request it.

    Use an exact string ID from get_accounts for the requested trd_env.
    acc_id='0' requires exactly one matching account; otherwise the read fails.
    REAL write allowlists do not restrict reads.

    Args:
        trd_env: Trading environment. 'REAL' (default) or 'SIMULATE' (for
            testing). See the note above on unlocking before reading REAL data.
        acc_id: Account ID. Must be obtained from get_accounts().
        refresh_cache: Whether to refresh the cache.
        currency: Filter by currency (e.g., 'HKD', 'USD'). Leave None for default.

    Returns:
        Dictionary with asset information including cash, market_val, total_assets, etc.
        Any acc_id in the response is a decimal STRING; monetary values remain
        numbers.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    assets = await run_blocking(
        trade_service.get_assets,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
        currency=currency,
    )
    await ctx.info(f"Retrieved assets for {trd_env} account")
    return serialize_identifiers(assets)


@mcp.tool()
async def get_positions(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    market: str = "",
    pl_ratio_min: float | None = None,
    pl_ratio_max: float | None = None,
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
    show_option_strategy_view: bool = False,
) -> list[dict]:
    """Get current positions.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Reading REAL account data may require unlock_trade, depending on the
      gateway. Try the read FIRST and only call unlock_trade if it fails
      asking to be unlocked. Do not unlock pre-emptively: in the default
      READ_ONLY trading mode unlock is denied outright, so calling it first
      turns a read that would have worked into a policy error.
    - If user wants SIMULATE account, they must explicitly request it.

    Use an exact string ID from get_accounts for the requested trd_env.
    acc_id='0' requires exactly one matching account; otherwise the read fails.
    REAL write allowlists do not restrict reads.

    Args:
        code: Filter by stock code (e.g., 'US.AAPL').
        market: Filter by market (e.g., 'US', 'HK', 'CN', 'SG', 'JP').
        pl_ratio_min: Minimum profit/loss ratio filter.
        pl_ratio_max: Maximum profit/loss ratio filter.
        trd_env: Trading environment. 'REAL' (default) or 'SIMULATE' (for
            testing). See the note above on unlocking before reading REAL data.
        acc_id: Account ID. Must be obtained from get_accounts().
        refresh_cache: Whether to refresh cache.
        show_option_strategy_view: Group multi-leg option positions into strategies.
            Set this to True before closing a spread: each strategy comes back as a
            'COMBINED' row plus its 'LEG' rows, and every row carries the
            'position_id' that place_combo_order requires on a closing order. Legs
            may also show a non-zero 'can_sell_qty' here while showing 0 in the
            default flat view, since such positions are closed as a package.

    Returns:
        List of position dictionaries with code, qty, cost_price, market_val,
        pl_ratio, etc.
        With show_option_strategy_view=True, also position_id, combo_id,
        strategy_type, and position_type ('COMBINED' or 'LEG').

        NOTE: acc_id, position_id, and combo_id are returned as decimal
        STRINGS, not numbers. They are 64-bit values that a JSON client parsing
        numbers as doubles would silently round, and a rounded id would be
        submitted against the wrong position. Pass them to place_combo_order
        exactly as received, as strings, without converting them to numbers at
        any point.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    positions = await run_blocking(
        trade_service.get_positions,
        code=code,
        market=market,
        pl_ratio_min=pl_ratio_min,
        pl_ratio_max=pl_ratio_max,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
        show_option_strategy_view=show_option_strategy_view,
    )
    await ctx.info(f"Retrieved {len(positions)} positions from {trd_env} account")
    return serialize_identifiers(positions)


@mcp.tool()
async def get_max_tradable(
    ctx: Context[ServerSession, AppContext],
    order_type: str,
    code: str,
    price: float,
    order_id: str = "",
    adjust_limit: float = 0,
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> dict[str, Any]:
    """Get maximum tradable quantity for a stock.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Reading REAL account data may require unlock_trade, depending on the
      gateway. Try the read FIRST and only call unlock_trade if it fails
      asking to be unlocked. Do not unlock pre-emptively: in the default
      READ_ONLY trading mode unlock is denied outright, so calling it first
      turns a read that would have worked into a policy error.
    - If user wants SIMULATE account, they must explicitly request it.

    Use an exact string ID from get_accounts for the requested trd_env.
    acc_id='0' requires exactly one matching account; otherwise the read fails.
    REAL write allowlists do not restrict reads.

    Args:
        order_type: Order type (e.g., 'NORMAL', 'LIMIT', 'MARKET').
        code: Stock code (e.g., 'US.AAPL').
        price: Target price for the order.
        order_id: Optional order ID for modification scenarios.
        adjust_limit: Adjust limit percentage.
        trd_env: Trading environment. 'REAL' (default) or 'SIMULATE' (for
            testing). See the note above on unlocking before reading REAL data.
        acc_id: Account ID. Must be obtained from get_accounts().

    Returns:
        Dictionary with max_cash_buy, max_cash_and_margin_buy, max_position_sell, etc.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    max_qty = await run_blocking(
        trade_service.get_max_tradable,
        order_type=order_type,
        code=code,
        price=price,
        order_id=order_id,
        adjust_limit=adjust_limit,
        trd_env=trd_env,
        acc_id=acc_id,
    )
    await ctx.info(f"Retrieved max tradable for {code} in {trd_env} account")
    return serialize_identifiers(max_qty)


@mcp.tool()
async def get_margin_ratio(
    ctx: Context[ServerSession, AppContext],
    code_list: list[str],
) -> list[dict]:
    """Get margin ratio for stocks.

    Args:
        code_list: List of stock codes (e.g., ['US.AAPL', 'US.TSLA']).

    Returns:
        List of margin ratio dictionaries.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    ratios = await run_blocking(trade_service.get_margin_ratio, code_list=code_list)
    await ctx.info(f"Retrieved margin ratios for {len(code_list)} stocks")
    return ratios


@mcp.tool()
async def get_cash_flow(
    ctx: Context[ServerSession, AppContext],
    clearing_date: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> list[dict]:
    """Get account cash flow history.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Reading REAL account data may require unlock_trade, depending on the
      gateway. Try the read FIRST and only call unlock_trade if it fails
      asking to be unlocked. Do not unlock pre-emptively: in the default
      READ_ONLY trading mode unlock is denied outright, so calling it first
      turns a read that would have worked into a policy error.
    - If user wants SIMULATE account, they must explicitly request it.

    Use an exact string ID from get_accounts for the requested trd_env.
    acc_id='0' requires exactly one matching account; otherwise the read fails.
    REAL write allowlists do not restrict reads.

    Args:
        clearing_date: Filter by clearing date ('YYYY-MM-DD'). Some brokers
            (e.g., FUTUSG) require clearing_date to be specified.
        trd_env: Trading environment. 'REAL' (default) or 'SIMULATE' (for
            testing). See the note above on unlocking before reading REAL data.
        acc_id: Account ID. Must be obtained from get_accounts().

    Returns:
        List of cash flow record dictionaries.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    cash_flows = await run_blocking(
        trade_service.get_cash_flow,
        clearing_date=clearing_date,
        trd_env=trd_env,
        acc_id=acc_id,
    )
    await ctx.info(
        f"Retrieved {len(cash_flows)} cash flow records from {trd_env} account"
    )
    return cash_flows


@mcp.tool()
async def unlock_trade(
    ctx: Context[ServerSession, AppContext],
    password: str | None = None,
    password_md5: str | None = None,
) -> dict[str, Any]:
    """Unlock the gateway by hand. Only for deployments that store no credential.

    Unlocking is about the GATEWAY's trading lock. It is a separate thing from
    this server's trading mode, and from whether you can read account data.

    DO NOT call this pre-emptively.

    - When this server stores a trade credential, this tool is REFUSED. The
      server manages the lock itself: it keeps the gateway locked at rest and
      unlocks only for the instant one order is dispatched. A manual unlock
      would leave the gateway unlocked indefinitely and undo that.
    - Unlocking requires MOOMOO_TRADING_MODE=REAL. The default is READ_ONLY, so
      calling this "just in case" before a read produces a policy error for a
      read that would have succeeded on its own.
    - There is no environment fallback. Supply a password or a hash explicitly.

    Workflow for accessing REAL account data:
    1. Call the read you actually want (get_assets, get_positions,
       get_account_summary, get_max_tradable, get_cash_flow) with
       trd_env='REAL'. Many gateways serve these without any unlock.
    2. Only if that read fails asking for trading to be unlocked, and only if
       this server stores no credential, call
       unlock_trade(password='your_trading_password').
    3. Retry the read.

    SIMULATE accounts never need unlocking.

    Args:
        password: Plain text trade password (the password you set in the Moomoo
            app). Required unless password_md5 is given.
        password_md5: MD5 hash of the trade password, as an alternative to
            password. Provide one or the other, not both.

    Returns:
        Success status dictionary with {'status': 'unlocked'}.

    Note:
        A manual unlock PERSISTS. The gateway stays unlocked until lock_trade is
        called or the gateway restarts, and the SDK replays the unlock after
        every reconnect. Nothing re-locks it on your behalf on this path, so
        call lock_trade when you are done.

        Call check_health to see the configured mode.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    await ctx.info("Attempting to unlock using the supplied credentials")
    await run_blocking(
        trade_service.unlock_trade, password=password, password_md5=password_md5
    )
    await ctx.info("Trade unlocked successfully - REAL account data is now accessible")
    return {
        "status": "unlocked",
        "message": (
            "You can now access REAL account data by setting trd_env='REAL' in "
            "other tools. This unlock PERSISTS: the gateway stays unlocked until "
            "lock_trade is called or the gateway restarts."
        ),
    }


@mcp.tool()
async def lock_trade(
    ctx: Context[ServerSession, AppContext],
) -> dict[str, Any]:
    """Lock the gateway, and clear an execution halt.

    This is also the ONLY way to clear an execution halt. The server halts when
    a re-lock after an order fails, because the gateway may then still be
    unlocked: while halted it refuses REAL placements, REAL combo placements
    and REAL NORMAL/ENABLE modifications, and allows cancellations and
    SIMULATE writes.

    A successful call locks the gateway and clears the halt. A REFUSED call
    leaves the halt exactly as it was — it never reports a clear it did not
    achieve. If the lock keeps failing, the gateway may be unable to resolve the
    REAL account its lock path requires, and an operator has to fix that before
    execution can resume.

    It waits for any order already in flight to finish, so it never locks the
    gateway underneath a write.

    Returns:
        Status dictionary with:
        - status: 'locked'.
        - execution_halted: whether execution is still halted afterwards.
        - halt_cleared: whether this call cleared a halt that was in effect.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    result = await run_blocking(trade_service.lock_trade)
    await ctx.info("Trade locked successfully on OpenD gateway")
    return {
        "status": "locked",
        "message": "Trading on OpenD is now locked",
        "execution_halted": result.get("execution_halted", False),
        "halt_cleared": result.get("halt_cleared", False),
    }
