"""Trading tools for order management operations."""

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from moomoo_mcp.server import AppContext, mcp
from moomoo_mcp.tools.offload import run_blocking
from moomoo_mcp.tools.serialization import serialize_identifiers


@mcp.tool()
async def place_order(
    ctx: Context[ServerSession, AppContext],
    code: str,
    price: float,
    qty: int,
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
    remark: str = "",
) -> dict:
    """Place a new trading order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the full order details to the
    user for verification including: code, side (BUY/SELL), quantity, price, order
    type, and time in force. Orders placed in REAL environment will use real money.

    IMPORTANT FOR AI AGENTS:
    - trd_env is REQUIRED and has no default. State the environment every time.
    - ALWAYS confirm with user before placing orders.

    TRADING MODE: this server refuses order writes unless MOOMOO_TRADING_MODE
    permits them — READ_ONLY blocks every write, SIMULATE allows only
    trd_env='SIMULATE', and REAL allows both. A refusal is an explicit policy
    error; the request is never rerouted to a different environment. Call
    check_health to see the configured mode.

    ACCOUNT: acc_id='0' resolves an account only when exactly one is eligible —
    matching the environment, authorized for the market, and, in REAL, on the
    configured allowlist. Zero or several eligible accounts is a refusal that
    lists the candidates by their last four digits; name one with acc_id.

    LIMITS: when a notional cap is configured, the order is valued as
    reference price x quantity x contract multiplier, in the instrument's own
    currency, and an order that CANNOT be valued is REFUSED rather than let
    through. The reference price is the limit price for a BUY limit order; every
    other order uses the larger of its own prices and the market, and is refused
    when no market price is available.

    HALT: if a re-lock after an earlier order failed, execution is HALTED and
    this call is refused for trd_env='REAL'. SIMULATE stays allowed, as do
    cancellations. lock_trade is the only way to clear a halt.

    THREE OUTCOMES: an error says which one happened.
    - "no order was sent": refused before anything was dispatched. Safe to fix
      and retry.
    - "may have been sent ... outcome is unknown": the request went out and
      nothing acknowledged it. An order MAY exist. Check get_orders BEFORE any
      retry. Never resend blindly.
    - "acknowledged ... do not resend": the gateway took the request and its
      receipt could not be read. An order DOES exist; find it with get_orders.

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
            - 'SPECIAL_LIMIT': Special limit / Market IOC
              (HK, partial fill then cancel).
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
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. REQUIRED.
        acc_id: Account ID from get_accounts(), or '0' to resolve one when
            exactly one account is eligible.
        remark: Optional order note/remark.

    Returns:
        Dictionary with order details including order_id, order_status and
        time_in_force, plus the acc_id and trd_env the order was submitted
        against.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            trade_service.place_order,
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
    )


@mcp.tool()
async def place_combo_order(
    ctx: Context[ServerSession, AppContext],
    combo_legs: list[dict],
    price: float,
    qty: int,
    trd_env: str,
    order_type: str = "NORMAL",
    time_in_force: str = "DAY",
    acc_id: str = "0",
    remark: str = "",
) -> dict:
    """Place a multi-leg option strategy (vertical spread, straddle, etc.) as a
    single atomic order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display EVERY leg (code, side, ratio)
    plus the net price and quantity for verification. Orders placed in REAL
    environment will use real money.

    Call preview_combo_order first with the same legs, price, quantity, and
    account to show the user the margin and buying-power impact before asking
    for confirmation.

    Prefer this over multiple `place_order` calls for any multi-leg strategy. A
    combo order fills as one unit or not at all. Submitting the legs separately
    risks one filling and the other not, which can convert a defined-risk position
    into an undefined-risk one — for example, closing a call spread leg by leg can
    leave a naked short call.

    IMPORTANT FOR AI AGENTS:
    - trd_env is REQUIRED and has no default. State the environment every time.
    - ALWAYS confirm with user before placing orders.

    TRADING MODE: this server refuses order writes unless MOOMOO_TRADING_MODE
    permits them — READ_ONLY blocks every write, SIMULATE allows only
    trd_env='SIMULATE', and REAL allows both. A refusal is an explicit policy
    error; the request is never rerouted to a different environment. Call
    check_health to see the configured mode.

    ACCOUNT: acc_id='0' resolves an account only when exactly one is eligible —
    matching the environment, authorized for the market, and, in REAL, on the
    configured allowlist. Zero or several eligible accounts is a refusal that
    lists the candidates by their last four digits; name one with acc_id.

    LIMITS: the quantity cap applies to the LARGEST LEG quantity
    (qty x qty_ratio), not the package count. The notional cap applies to the
    PACKAGE PREMIUM: |net price| x qty x the legs' common contract multiplier.

    PREMIUM IS NOT MAXIMUM LOSS. A short package can lose far more than the
    premium it collects; capping premium bounds what the package costs to open,
    not what it can cost to hold.

    While a notional cap is configured, these combos are REFUSED:
    - combos whose premium cannot be computed;
    - combos whose legs have differing contract multipliers or contract sizes;
    - combos that include a stock leg;
    - combos using an order type outside the fixed-limit class, such as MARKET,
      because there is no net package price to measure.

    HALT: if a re-lock after an earlier order failed, execution is HALTED and
    this call is refused for trd_env='REAL'. SIMULATE stays allowed, as do
    cancellations. lock_trade is the only way to clear a halt.

    THREE OUTCOMES: an error says which one happened.
    - "no order was sent": refused before anything was dispatched. Safe to fix
      and retry.
    - "may have been sent ... outcome is unknown": the request went out and
      nothing acknowledged it. An order MAY exist. Check get_orders BEFORE any
      retry. Never resend blindly.
    - "acknowledged ... do not resend": the gateway took the request and its
      receipt could not be read. An order DOES exist; find it with get_orders.

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
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. REQUIRED.
        acc_id: Account ID from get_accounts(), or '0' to resolve one when
            exactly one account is eligible.
        remark: Optional order note/remark.

    Returns:
        Dictionary with order details including order_id and order_status.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    # A combo order's legs each carry a 64-bit position_id.
    return serialize_identifiers(
        await run_blocking(
            trade_service.place_combo_order,
            combo_legs=combo_legs,
            price=price,
            qty=qty,
            order_type=order_type,
            time_in_force=time_in_force,
            trd_env=trd_env,
            acc_id=acc_id,
            remark=remark,
        )
    )


@mcp.tool()
async def preview_combo_order(
    ctx: Context[ServerSession, AppContext],
    combo_legs: list[dict],
    price: float,
    qty: int,
    order_type: str = "NORMAL",
    trd_env: str = "REAL",
    acc_id: str = "0",
) -> dict[str, Any]:
    """Preview what a multi-leg option package would do to an account.

    READ-ONLY. This submits nothing: no order is placed, modified, or
    cancelled, no trading is unlocked, and no funds are reserved. It works in
    every trading mode, including READ_ONLY, though a broker can still refuse
    the query itself.

    Use it before place_combo_order to show the user the margin and buying-power
    impact of the exact package you are about to propose. Pass the same legs,
    price, quantity, and account you intend to submit — a preview of a different
    package describes a different trade.

    It resolves the account exactly as place_combo_order does, so it previews
    the account the placement would reach, and refuses where the placement would
    refuse rather than previewing an account the order could not use.

    Args:
        combo_legs: Same format as place_combo_order. Each leg is a dict:
            {"code": "US.AAPL260320C200000", "trd_side": "SELL",
             "qty_ratio": 1, "position_id": "123456789"}
            - qty_ratio is required and multiplies the order quantity for that
              leg.
            - position_id is required when CLOSING an existing position. Get it
              from get_positions(show_option_strategy_view=True) and pass the
              decimal string through unchanged.
        price: NET price of the whole package, not a per-leg price. moomoo does
            not document a sign convention for debit vs credit packages, so the
            value is forwarded exactly as given and no convention is assumed.
        qty: Number of packages (not the total contracts across legs).
        order_type: 'NORMAL' for limit, 'MARKET', etc.
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. Default REAL.
        acc_id: Account ID from get_accounts(). Resolved from the legs' market
            when omitted, exactly as place_combo_order would resolve it.

    Returns:
        Dictionary containing:
        - checked_at: UTC observation time (ISO-8601, 'Z' suffix).
        - acc_id: The account the preview was run against, as a decimal string.
        - trd_env: The environment the preview was run against.
        - nlv_change: Change in net liquidation value.
        - initial_margin_change: Change in initial margin requirement.
        - maintenance_margin_change: Change in maintenance margin requirement.
        - option_bp: Option buying power after the package.
        - max_withdraw_change: Change in maximum withdrawable amount.
        - bp_decrease: Decrease in buying power.

        A field the gateway did not supply is null, not zero. Null means "not
        reported"; zero would mean "no impact", which is a different claim.

        These are point-in-time estimates from the broker, not a quote and not
        an acceptance. Values can change before the order is submitted, and a
        successful preview does not mean the order would fill.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    preview = await run_blocking(
        trade_service.preview_combo_order,
        combo_legs=combo_legs,
        price=price,
        qty=qty,
        order_type=order_type,
        trd_env=trd_env,
        acc_id=acc_id,
    )
    return serialize_identifiers(preview)


@mcp.tool()
async def modify_order(
    ctx: Context[ServerSession, AppContext],
    order_id: str,
    modify_order_op: str,
    trd_env: str,
    qty: int | None = None,
    price: float | None = None,
    adjust_limit: float = 0,
    acc_id: str = "0",
) -> dict:
    """Modify an existing order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the order_id and the new
    parameters (price, qty) to the user for verification.

    IMPORTANT FOR AI AGENTS:
    - trd_env is REQUIRED and has no default. State the environment every time.
    - ALWAYS confirm with user before modifying orders.

    TRADING MODE: this server refuses order writes unless MOOMOO_TRADING_MODE
    permits them — READ_ONLY blocks every write, SIMULATE allows only
    trd_env='SIMULATE', and REAL allows both. A refusal is an explicit policy
    error; the request is never rerouted to a different environment. Call
    check_health to see the configured mode.

    ACCOUNT: acc_id='0' resolves an account only when exactly one is eligible
    for the environment, and, in REAL, on the configured allowlist. Zero or
    several eligible accounts is a refusal that lists the candidates by their
    last four digits; name one with acc_id.

    LIMITS: 'NORMAL' and 'ENABLE' are checked as the order that WOULD RESULT,
    not as the fields you sent. The existing order is fetched, your changes are
    merged over it, and the whole order is valued. Changing only the price of a
    100-share order therefore checks 100 x the new price. An order that cannot
    be found is refused. 'CANCEL', 'DISABLE' and 'DELETE' reduce exposure and
    are not checked against limits.

    HALT: if a re-lock after an earlier order failed, execution is HALTED and
    'NORMAL' and 'ENABLE' are refused for trd_env='REAL'. SIMULATE stays
    allowed, as do 'CANCEL', 'DISABLE' and 'DELETE'. lock_trade is the only way
    to clear a halt.

    THREE OUTCOMES: an error says which one happened.
    - "no order was sent": refused before anything was dispatched. Safe to fix
      and retry.
    - "may have been sent ... outcome is unknown": the request went out and
      nothing acknowledged it. The change MAY have taken effect. Check
      get_orders BEFORE any retry. Never resend blindly.
    - "acknowledged ... do not resend": the gateway took the request and its
      receipt could not be read. Find the order with get_orders.

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
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. REQUIRED.
        acc_id: Account ID from get_accounts(), or '0' to resolve one when
            exactly one account is eligible.

    Returns:
        Dictionary with the modified order's details, plus the acc_id and
        trd_env the modification was submitted against.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            trade_service.modify_order,
            order_id=order_id,
            modify_order_op=modify_order_op,
            qty=qty,
            price=price,
            adjust_limit=adjust_limit,
            trd_env=trd_env,
            acc_id=acc_id,
        )
    )


@mcp.tool()
async def cancel_order(
    ctx: Context[ServerSession, AppContext],
    order_id: str,
    trd_env: str,
    acc_id: str = "0",
) -> dict:
    """Cancel an existing order.

    CRITICAL: You MUST ask the user for explicit confirmation before calling this
    tool, especially if `trd_env` is 'REAL'. Display the order_id to the user
    for verification before cancellation.

    IMPORTANT FOR AI AGENTS:
    - trd_env is REQUIRED and has no default. State the environment every time.
    - ALWAYS confirm with user before cancelling orders.

    TRADING MODE: this server refuses order writes unless MOOMOO_TRADING_MODE
    permits them — READ_ONLY blocks every write, SIMULATE allows only
    trd_env='SIMULATE', and REAL allows both. A refusal is an explicit policy
    error; the request is never rerouted to a different environment. Call
    check_health to see the configured mode.

    ACCOUNT: acc_id='0' resolves an account only when exactly one is eligible
    for the environment, and, in REAL, on the configured allowlist. Zero or
    several eligible accounts is a refusal that lists the candidates by their
    last four digits; name one with acc_id.

    HALT: cancellation stays ALLOWED while execution is halted. Reducing
    exposure is exactly what an operator needs during a halt. lock_trade is
    what clears the halt, once the gateway accepts a lock again.

    THREE OUTCOMES: an error says which one happened.
    - "no order was sent": refused before anything was dispatched. Safe to fix
      and retry.
    - "may have been sent ... outcome is unknown": the request went out and
      nothing acknowledged it. The order MAY have been cancelled. Check
      get_orders BEFORE any retry. Never resend blindly.
    - "acknowledged ... do not resend": the gateway took the request and its
      receipt could not be read. Check get_orders for the order's state.

    Args:
        order_id: Order ID to cancel. Get from get_orders().
        trd_env: Trading environment - 'REAL' or 'SIMULATE'. REQUIRED.
        acc_id: Account ID from get_accounts(), or '0' to resolve one when
            exactly one account is eligible.

    Returns:
        Dictionary with the cancelled order's details, plus the acc_id and
        trd_env the cancellation was submitted against.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            trade_service.cancel_order,
            order_id=order_id,
            trd_env=trd_env,
            acc_id=acc_id,
        )
    )


@mcp.tool()
async def get_orders(
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
    # A combo order's legs each carry a 64-bit position_id.
    return serialize_identifiers(
        await run_blocking(
            trade_service.get_orders,
            code=code,
            status_filter_list=status_filter_list,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
    )


@mcp.tool()
async def get_deals(
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

        NOTE: deal_id is returned as a decimal STRING, not a number.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            trade_service.get_deals,
            code=code,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
    )


@mcp.tool()
async def get_history_orders(
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
    # A combo order's legs each carry a 64-bit position_id.
    return serialize_identifiers(
        await run_blocking(
            trade_service.get_history_orders,
            code=code,
            status_filter_list=status_filter_list,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
    )


@mcp.tool()
async def get_history_deals(
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

        NOTE: deal_id is returned as a decimal STRING, not a number.
    """
    trade_service = ctx.request_context.lifespan_context.trade_service
    return serialize_identifiers(
        await run_blocking(
            trade_service.get_history_deals,
            code=code,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
    )
