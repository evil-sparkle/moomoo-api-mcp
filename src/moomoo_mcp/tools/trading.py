"""Trading tools for order management operations."""

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp


@mcp.tool()
def place_order(
    ctx: Context[ServerSession, AppContext],
    code: str,
    price: float,
    qty: int,
    trd_side: str,
    order_type: str = "NORMAL",
    time_in_force: str = "DAY",
    adjust_limit: float = 0,
    aux_price: float | None = None,
    trail_type: str | None = None,
    trail_value: float | None = None,
    trail_spread: float | None = None,
    trd_env: str = "REAL",
    acc_id: str = "0",
    remark: str = "",
) -> dict:
    """Place a new trading order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the full order details to the
    user for verification including: code, side (BUY/SELL), quantity, price, order
    type, and time in force. Orders placed in REAL environment will use real money.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account as per user preference.
    - ALWAYS confirm with user before placing orders.
    - For SIMULATE environment, explicitly set trd_env='SIMULATE'.

    Args:
        code: Stock code (e.g., 'US.AAPL', 'HK.00700').
        price: Order price. For market orders, this is used as price limit.
        qty: Order quantity (number of shares).
        trd_side: Trade side - 'BUY' or 'SELL'.
        order_type: Order type. Supported values:
            - 'NORMAL': Enhanced limit order (HK), limit order (US/A-share).
            - 'MARKET': Market order.
            - 'ABSOLUTE_LIMIT': Limit order (HK only, exact price match required).
            - 'AUCTION': Auction order (HK).
            - 'AUCTION_LIMIT': Auction limit order (HK).
            - 'SPECIAL_LIMIT': Special limit / Market IOC (HK, partial fill then cancel).
            - 'SPECIAL_LIMIT_ALL': Special limit all-or-none (HK, fill all or cancel).
            - 'STOP': Stop market order.
            - 'STOP_LIMIT': Stop limit order.
            - 'MARKET_IF_TOUCHED': Market if touched (take profit).
            - 'LIMIT_IF_TOUCHED': Limit if touched (take profit).
            - 'TRAILING_STOP': Trailing stop market order.
            - 'TRAILING_STOP_LIMIT': Trailing stop limit order.
        time_in_force: Time in force for the order. Default 'DAY'.
            - 'DAY': Order valid for current trading day only.
            - 'GTC': Good-Til-Cancelled, order remains active until filled or cancelled.
        adjust_limit: Adjust limit percentage (0-100). Default 0.
        aux_price: Trigger price for stop/if-touched order types (required for STOP,
            STOP_LIMIT, MARKET_IF_TOUCHED, LIMIT_IF_TOUCHED).
        trail_type: Trailing type for trailing stop orders (required for TRAILING_STOP,
            TRAILING_STOP_LIMIT). Values: 'RATIO' or 'AMOUNT'.
        trail_value: Trailing value (ratio or amount) for trailing stop types.
        trail_spread: Optional trailing spread for trailing stop limit types.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts(). Required if multiple accounts exist.
        remark: Optional order note/remark.

    Returns:
        Dictionary with order details including order_id, order_status,
        time_in_force, etc.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.place_order(
        code=code,
        price=price,
        qty=qty,
        trd_side=trd_side,
        order_type=order_type,
        time_in_force=time_in_force,
        adjust_limit=adjust_limit,
        aux_price=aux_price,
        trail_type=trail_type,
        trail_value=trail_value,
        trail_spread=trail_spread,
        trd_env=trd_env,
        acc_id=acc_id,
        remark=remark,
    )


@mcp.tool()
def place_combo_order(
    ctx: Context[ServerSession, AppContext],
    combo_legs: list[dict],
    price: float,
    qty: int,
    order_type: str = "NORMAL",
    time_in_force: str = "DAY",
    trd_env: str = "REAL",
    acc_id: str = "0",
    remark: str = "",
) -> dict:
    """Place a multi-leg option strategy (vertical spread, straddle, etc.) as a
    single atomic order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display EVERY leg (code, side, ratio)
    plus the net price and quantity for verification. Orders placed in REAL
    environment will use real money.

    Prefer this over multiple `place_order` calls for any multi-leg strategy. A
    combo order fills as one unit or not at all. Submitting the legs separately
    risks one filling and the other not, which can convert a defined-risk position
    into an undefined-risk one — for example, closing a call spread leg by leg can
    leave a naked short call.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account as per user preference.
    - ALWAYS confirm with user before placing orders.
    - For SIMULATE environment, explicitly set trd_env='SIMULATE'.

    Args:
        combo_legs: The strategy's legs, at least two, all in the same market.
            Each leg is a dict:
                {"code": "US.AAPL260320C200000", "trd_side": "SELL",
                 "qty_ratio": 1, "position_id": 123456789}
            - code: Option or stock code for the leg.
            - trd_side: 'BUY' or 'SELL' for that leg.
            - qty_ratio: Required positive integer. It MULTIPLIES the order
              quantity for this leg: actual leg qty = qty x qty_ratio. It is not
              optional, because assuming 1 would silently submit a different
              strategy (a 1:2:1 butterfly would become 1:1:1).
            - position_id: Required when CLOSING an existing position. Get it from
              get_positions(show_option_strategy_view=True), which returns the
              strategy as a COMBINED row plus its LEG rows, each with a
              position_id. Omit when opening a new position.
        price: NET price of the whole package, not a per-leg price.
            NOTE: moomoo's API reference does not document a sign convention for
            debit vs credit packages. Do not assume one. Verify against the app's
            own ticket for the same strategy before pricing a REAL order.
        qty: Number of packages to trade (not the total contracts across legs).
        order_type: Order type. 'NORMAL' is a limit order; 'MARKET' is also
            supported but is rarely appropriate for a multi-leg option package.
        time_in_force: 'DAY' (default) or 'GTC'.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts(). Resolved automatically if omitted.
        remark: Optional order note/remark.

    Returns:
        Dictionary with order details including order_id and order_status.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.place_combo_order(
        combo_legs=combo_legs,
        price=price,
        qty=qty,
        order_type=order_type,
        time_in_force=time_in_force,
        trd_env=trd_env,
        acc_id=acc_id,
        remark=remark,
    )


@mcp.tool()
def modify_order(
    ctx: Context[ServerSession, AppContext],
    order_id: str,
    modify_order_op: str,
    qty: int | None = None,
    price: float | None = None,
    adjust_limit: float = 0,
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> dict:
    """Modify an existing order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the order_id and the new
    parameters (price, qty) to the user for verification.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account as per user preference.
    - ALWAYS confirm with user before modifying orders.

    Args:
        order_id: Order ID to modify. Get from get_orders().
        modify_order_op: Modification operation:
            - 'NORMAL': Modify price/quantity.
            - 'CANCEL': Cancel the order.
            - 'DISABLE': Disable the order.
            - 'ENABLE': Enable a disabled order.
            - 'DELETE': Delete the order.
        qty: New quantity (optional, for NORMAL operation).
        price: New price (optional, for NORMAL operation).
        adjust_limit: Adjust limit percentage (0-100). Default 0.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().

    Returns:
        Dictionary with modified order details.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.modify_order(
        order_id=order_id,
        modify_order_op=modify_order_op,
        qty=qty,
        price=price,
        adjust_limit=adjust_limit,
        trd_env=trd_env,
        acc_id=acc_id,
    )


@mcp.tool()
def cancel_order(
    ctx: Context[ServerSession, AppContext],
    order_id: str,
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> dict:
    """Cancel an existing order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the order_id to the user
    for verification before cancellation.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account as per user preference.
    - ALWAYS confirm with user before cancelling orders.

    Args:
        order_id: Order ID to cancel. Get from get_orders().
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().

    Returns:
        Dictionary with cancelled order details.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.cancel_order(
        order_id=order_id,
        trd_env=trd_env,
        acc_id=acc_id,
    )


@mcp.tool()
def get_orders(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    status_filter_list: list[str] | None = None,
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
) -> list[dict]:
    """Get list of today's orders.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Only use SIMULATE if the user explicitly requests it.

    Args:
        code: Filter by stock code (e.g., 'US.AAPL'). Empty string for all.
        status_filter_list: Filter by order statuses. Options:
            - 'UNSUBMITTED', 'WAITING_SUBMIT', 'SUBMITTING', 'SUBMIT_FAILED'
            - 'SUBMITTED', 'FILLED_PART', 'FILLED_ALL'
            - 'CANCELLING_PART', 'CANCELLING_ALL', 'CANCELLED_PART', 'CANCELLED_ALL'
            - 'REJECTED', 'DISABLED', 'DELETED', 'FAILED', 'NONE'
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().
        refresh_cache: Whether to refresh the cache. Default False.

    Returns:
        List of order dictionaries with order_id, code, qty, price, trd_side,
        order_type, order_status, created_time, updated_time, etc.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.get_orders(
        code=code,
        status_filter_list=status_filter_list,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
    )


@mcp.tool()
def get_deals(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
    refresh_cache: bool = False,
) -> list[dict]:
    """Get list of today's deals (executed trades).

    A deal represents a filled order or partial fill. One order can result in
    multiple deals if filled in parts.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Only use SIMULATE if the user explicitly requests it.

    Args:
        code: Filter by stock code (e.g., 'US.AAPL'). Empty string for all.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().
        refresh_cache: Whether to refresh the cache. Default False.

    Returns:
        List of deal dictionaries with deal_id, order_id, code, qty, price,
        trd_side, create_time, etc.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.get_deals(
        code=code,
        trd_env=trd_env,
        acc_id=acc_id,
        refresh_cache=refresh_cache,
    )


@mcp.tool()
def get_history_orders(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    status_filter_list: list[str] | None = None,
    start: str = "",
    end: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> list[dict]:
    """Get historical orders.

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Only use SIMULATE if the user explicitly requests it.

    Args:
        code: Filter by stock code (e.g., 'US.AAPL'). Empty string for all.
        status_filter_list: Filter by order statuses (see get_orders for options).
        start: Start date in 'YYYY-MM-DD' format. Empty for max range.
        end: End date in 'YYYY-MM-DD' format. Empty for today.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().

    Returns:
        List of historical order dictionaries.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.get_history_orders(
        code=code,
        status_filter_list=status_filter_list,
        start=start,
        end=end,
        trd_env=trd_env,
        acc_id=acc_id,
    )


@mcp.tool()
def get_history_deals(
    ctx: Context[ServerSession, AppContext],
    code: str = "",
    start: str = "",
    end: str = "",
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> list[dict]:
    """Get historical deals (executed trades).

    IMPORTANT FOR AI AGENTS:
    - Default is REAL account. You MUST notify the user clearly that you are
      accessing their REAL trading account before proceeding.
    - Only use SIMULATE if the user explicitly requests it.

    Args:
        code: Filter by stock code (e.g., 'US.AAPL'). Empty string for all.
        start: Start date in 'YYYY-MM-DD' format. Empty for max range.
        end: End date in 'YYYY-MM-DD' format. Empty for today.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts().

    Returns:
        List of historical deal dictionaries.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return trade_service.get_history_deals(
        code=code,
        start=start,
        end=end,
        trd_env=trd_env,
        acc_id=acc_id,
    )
