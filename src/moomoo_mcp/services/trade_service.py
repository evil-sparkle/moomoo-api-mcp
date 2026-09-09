"""Trade service for managing Moomoo trading context and account operations."""

import logging
import math
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from moomoo import (
    ComboLeg,
    OpenSecTradeContext,
    OrderStatus,
    RET_OK,
    SecurityFirm,
    TrdMarket,
)

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.health import (
    SYNC_CONNECT_TIMEOUT_SECONDS,
    BoundedProbe,
    failure,
)
from moomoo_mcp.services.trading_policy import TradingPolicy

logger = logging.getLogger(__name__)

# How long startup waits for the trade connection before carrying on
# without it. The worker keeps trying in the background.
CONNECT_TIMEOUT_SECONDS = 5.0


# What the SDK's decoders substitute for a field the gateway did not send.
# Confirmed against ComboOrderTradingInfoQuery.unpack_rsp, which writes this
# string — not a number and not NaN — for every absent impact field.
SDK_MISSING_SENTINEL = "N/A"


def _null_if_missing(value: Any) -> Any:
    """Normalize the SDK's missing-value markers to None.

    A field the gateway omitted reaches us as the string "N/A" from the SDK's
    decoder, or as NaN if it went through a pandas frame that widened a column.
    Both mean "not supplied".

    Reporting null says exactly that. Passing "N/A" through would put a string
    in a numeric field, and substituting 0.0 would claim the package has no
    effect on that measure — a different statement, and a dangerous one when the
    measure is a margin requirement.
    """
    if isinstance(value, str) and value.strip() == SDK_MISSING_SENTINEL:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


class TradeService:
    """Service to manage Moomoo Trade API connections and account operations."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        security_firm: str | None = None,
        policy: TradingPolicy | None = None,
    ):
        """Initialize TradeService.

        Args:
            host: Host address of OpenD gateway.
            port: Port number of OpenD gateway.
            security_firm: Securities firm identifier (e.g., 'FUTUSG' for Singapore,
                'FUTUSECURITIES' for HK). If None, no filter is applied.
            policy: Trading policy governing which order environments this
                service may write to. Defaults to read-only, so a service
                constructed without an explicit intent cannot send an order.
        """
        self.host = host
        self.port = port
        self.security_firm = security_firm
        self.policy = policy or TradingPolicy()
        self.trade_ctx: OpenSecTradeContext | None = None
        self._trade_probe = BoundedProbe("trade")
        self._connect_lock = threading.Lock()
        self._connect_executor: ThreadPoolExecutor | None = None
        self._connect_future: Future | None = None
        self._closed = False

    def _convert_status_filter(
        self, status_filter_list: list[str] | None
    ) -> list[OrderStatus]:
        """Convert string status values to OrderStatus enum values.

        The Moomoo SDK expects OrderStatus enum values, not strings.
        This method converts user-provided string values to the proper enum format.

        Args:
            status_filter_list: List of status strings like ['SUBMITTED', 'FILLED_ALL'].

        Returns:
            List of OrderStatus enum values. Returns an empty list if input is None.

        Raises:
            ValueError: If an invalid status string is provided.
        """
        if status_filter_list is None:
            return []

        converted = []
        for status_str in status_filter_list:
            # OrderStatus has the attribute matching the string (e.g., OrderStatus.SUBMITTED)
            status_enum = getattr(OrderStatus, status_str.upper(), None)
            if status_enum is None:
                valid_statuses = [
                    "UNSUBMITTED", "WAITING_SUBMIT", "SUBMITTING", "SUBMIT_FAILED",
                    "SUBMITTED", "FILLED_PART", "FILLED_ALL",
                    "CANCELLING_PART", "CANCELLING_ALL", "CANCELLED_PART", "CANCELLED_ALL",
                    "REJECTED", "DISABLED", "DELETED", "FAILED", "NONE"
                ]
                raise ValueError(
                    f"Invalid order status: '{status_str}'. "
                    f"Valid options: {valid_statuses}"
                )
            converted.append(status_enum)
        return converted

    def _get_market_from_code(self, code: str) -> str | None:
        """Extract market from stock code (e.g., 'JP' from 'JP.8058')."""
        if "." in code:
            return code.split(".")[0].upper()
        return None

    def _find_best_account(self, trd_env: str, market: str) -> int:
        """Find the best account for the given environment and market.

        Args:
            trd_env: Trading environment ('REAL' or 'SIMULATE').
            market: Target market (e.g., 'JP', 'US', 'HK').

        Returns:
            Account ID if found, otherwise 0 (default).
            
        Raises:
             ValueError: If no suitable account is found.
        """
        try:
            accounts = self.get_accounts()
        except Exception as e:
            # Re-raise as a ValueError to ensure the caller knows account finding failed.
            raise ValueError("Failed to retrieve account list from the API.") from e

        # Filter by environment
        env_accounts = [acc for acc in accounts if acc.get("trd_env") == trd_env]
        
        if not env_accounts:
            # Raise an error if no accounts are found for the environment.
            raise ValueError(f"No accounts found for the '{trd_env}' environment.")

        # Moomoo market codes mapping to market_auth strings
        # Adjust as needed based on actual API values
        target_market = market.upper()
        
        supported_markets = []

        for acc in env_accounts:
            # Check market_auth which is a list like ['HK', 'US']
            # Note: The field name might be 'trdmarket_auth' based on debug output
            market_auth = acc.get("market_auth") or acc.get("trdmarket_auth") or []
            supported_markets.extend(market_auth)
            
            if target_market in market_auth:
                return acc["acc_id"]
        
        # If we are here, we found accounts for the env, but none support the market
        unique_supported = sorted(list(set(supported_markets)))
        raise ValueError(
            f"No account found in {trd_env} environment that supports trading in {market}. "
            f"Available accounts support: {unique_supported}"
        )

    def _open_trade_context(self) -> None:
        """Construct the SDK trade context and publish it when it is ready."""
        kwargs = {"host": self.host, "port": self.port}

        # Add security_firm if specified
        if self.security_firm:
            # Convert string to SecurityFirm enum
            firm_enum = getattr(SecurityFirm, self.security_firm, None)
            if firm_enum:
                kwargs["security_firm"] = firm_enum

        trade_ctx = OpenSecTradeContext(**kwargs)
        trade_ctx.set_sync_query_connect_timeout(SYNC_CONNECT_TIMEOUT_SECONDS)

        with self._connect_lock:
            if self._closed:
                # close() ran while this was still retrying. Publishing the
                # context now would leak a live connection past shutdown.
                should_close = True
            else:
                self.trade_ctx = trade_ctx
                should_close = False
        if should_close:
            trade_ctx.close()

    def connect(self, timeout: float | None = None) -> None:
        """Start the trade connection, waiting at most ``timeout`` seconds.

        OpenSecTradeContext offers no async-connect option and its constructor
        does not raise when OpenD is unreachable — it retries every six seconds
        forever. Calling it inline would hang the MCP lifespan before it yields,
        taking check_health down with the gateway it exists to diagnose.

        So it runs on a single background worker. If the gateway is down this
        returns once the timeout elapses, health reports the trade service as
        unavailable, and the worker publishes the context if OpenD later
        appears. Only one such worker ever exists: repeated calls join the one
        in flight rather than stacking up connection attempts.

        Args:
            timeout: Seconds to wait for the connection before returning.
                Defaults to ``CONNECT_TIMEOUT_SECONDS``.
        """
        if timeout is None:
            timeout = CONNECT_TIMEOUT_SECONDS

        with self._connect_lock:
            self._closed = False
            if self._connect_executor is None:
                self._connect_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="trade-connect"
                )
            future = self._connect_future
            if future is None or future.done():
                future = self._connect_executor.submit(self._open_trade_context)
                self._connect_future = future

        try:
            future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning(
                f"Trade connection to {self.host}:{self.port} is still being "
                f"established after {timeout:.0f}s. The server remains available; "
                "check_health reports the trade service until it connects."
            )
        except Exception as exc:  # noqa: BLE001 - startup must stay available
            logger.error(f"Trade connection failed: {exc}")

    def close(self) -> None:
        """Close trade context connection and release background workers."""
        self._trade_probe.close()
        with self._connect_lock:
            # Set before releasing the lock so a worker that is still retrying
            # closes whatever it eventually builds instead of publishing it.
            self._closed = True
            executor = self._connect_executor
            self._connect_executor = None
            self._connect_future = None
            trade_ctx = self.trade_ctx
            self.trade_ctx = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if trade_ctx:
            trade_ctx.close()

    def probe_trade(self) -> dict[str, Any]:
        """Actively check trade connectivity with a read-only account listing.

        ``get_acc_list`` is the lightest trade read that proves the trade socket
        is answering; it neither unlocks trading nor mutates anything. Only the
        return code and the number of visible accounts are reported — never
        account identifiers, balances, or positions.
        """
        trade_ctx = self.trade_ctx
        if trade_ctx is None:
            return failure(
                "unavailable", "Trade context not initialized", reason="not_initialized"
            )

        ret, data = trade_ctx.get_acc_list()
        if ret != RET_OK:
            return failure("error", data)

        try:
            account_count = len(data)
        except TypeError:
            account_count = 0
        return {"status": "ok", "account_count": account_count}

    def submit_probe(self) -> Future:
        """Start (or join) the bounded trade connectivity probe."""
        return self._trade_probe.submit(self.probe_trade)

    def collect_probe(self, future: Future, timeout: float) -> dict[str, Any]:
        """Collect a trade probe result within the remaining health deadline."""
        return self._trade_probe.collect(future, timeout)

    def get_accounts(self) -> list[dict]:
        """Get list of trading accounts.

        Returns:
            List of account dictionaries with acc_id, trd_env, etc.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {data}")

        return data.to_dict("records")

    def get_assets(
        self,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
        currency: str | None = None,
    ) -> dict:
        """Get account assets (cash, market value, etc.).

        Args:
            trd_env: Trading environment, 'REAL' or 'SIMULATE'.
            acc_id: Account ID. Must be obtained from get_accounts().
            refresh_cache: Whether to refresh the cache.
            currency: Filter by currency (e.g., 'HKD', 'USD'). Leave None for default.

        Returns:
            Dictionary with asset information.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        kwargs = {
            "trd_env": trd_env,
            "acc_id": acc_id,
            "refresh_cache": refresh_cache,
        }
        if currency is not None:
            normalized_currency = currency.strip().upper()
            if normalized_currency:
                kwargs["currency"] = normalized_currency

        ret, data = self.trade_ctx.accinfo_query(**kwargs)
        if ret != RET_OK:
            raise RuntimeError(f"accinfo_query failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    def get_positions(
        self,
        code: str = "",
        market: str = "",
        pl_ratio_min: float | None = None,
        pl_ratio_max: float | None = None,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
        show_option_strategy_view: bool = False,
    ) -> list[dict]:
        """Get current positions.

        Args:
            code: Filter by stock code.
            market: Filter by market (e.g., 'US', 'HK', 'CN', 'SG', 'JP').
            pl_ratio_min: Minimum profit/loss ratio filter.
            pl_ratio_max: Maximum profit/loss ratio filter.
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().
            refresh_cache: Whether to refresh cache.
            show_option_strategy_view: Group multi-leg option positions into
                strategies. Each strategy is returned as a 'COMBINED' row
                alongside its 'LEG' rows, and every row carries the
                'position_id' that place_combo_order requires when closing.

        Returns:
            List of position dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Map market string to TrdMarket enum
        # Note: CN is for A-share simulation only; HKCC for Stock Connect (live only)
        market_map = {
            "US": TrdMarket.US,
            "HK": TrdMarket.HK,
            "CN": TrdMarket.CN,
            "HKCC": TrdMarket.HKCC,
            "SG": TrdMarket.SG,
            "JP": TrdMarket.JP,
        }
        position_market = TrdMarket.NONE
        if market:
            try:
                # Try direct map first
                position_market = market_map.get(market.upper())
                if position_market is None:
                    # Try to use getattr for other potential values
                    position_market = getattr(TrdMarket, market.upper())
            except AttributeError:
                position_market = TrdMarket.NONE

        ret, data = self.trade_ctx.position_list_query(
            code=code,
            position_market=position_market,
            pl_ratio_min=pl_ratio_min,
            pl_ratio_max=pl_ratio_max,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
            show_option_strategy_view=show_option_strategy_view,
        )
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query failed: {data}")

        return data.to_dict("records")

    def get_max_tradable(
        self,
        order_type: str,
        code: str,
        price: float,
        order_id: str = "",
        adjust_limit: float = 0,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Get maximum tradable quantity for a stock.

        Args:
            order_type: Order type string (e.g., 'NORMAL').
            code: Stock code.
            price: Target price.
            order_id: Optional order ID for modification.
            adjust_limit: Adjust limit percentage.
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().

        Returns:
            Dictionary with max quantities for buy/sell.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.acctradinginfo_query(
            order_type=order_type,
            code=code,
            price=price,
            order_id=order_id,
            adjust_limit=adjust_limit,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"acctradinginfo_query failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    def get_margin_ratio(self, code_list: list[str]) -> list[dict]:
        """Get margin ratio for stocks.

        Args:
            code_list: List of stock codes.

        Returns:
            List of margin ratio dictionaries.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_margin_ratio(code_list=code_list)
        if ret != RET_OK:
            raise RuntimeError(f"get_margin_ratio failed: {data}")

        return data.to_dict("records")

    def get_cash_flow(
        self,
        clearing_date: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get account cash flow history.

        Args:
            clearing_date: Filter by clearing date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().

        Returns:
            List of cash flow record dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_acc_cash_flow(
            clearing_date=clearing_date,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_cash_flow failed: {data}")

        return data.to_dict("records")

    def unlock_trade(
        self, password: str | None = None, password_md5: str | None = None
    ) -> None:
        """Unlock trade for trading operations.

        Args:
            password: Plain text trade password.
            password_md5: MD5 hash of trade password (alternative to password).

        Raises:
            TradingPolicyError: If the configured mode does not permit unlocking.
            RuntimeError: If unlock fails.
        """
        # Checked before the connection check so a denied unlock never reaches
        # the gateway, whatever the connection state.
        self.policy.check_unlock()

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.unlock_trade(
            password=password,
            password_md5=password_md5,
            is_unlock=True,
        )
        if ret != RET_OK:
            raise RuntimeError(f"unlock_trade failed: {data}")

    def place_order(
        self,
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
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        remark: str = "",
    ) -> dict:
        """Place a new trading order.

        Args:
            code: Stock code (e.g., 'US.AAPL').
            price: Order price.
            qty: Order quantity.
            trd_side: Trade side ('BUY' or 'SELL').
            order_type: Order type ('NORMAL', 'MARKET', etc.).
            time_in_force: Time in force ('DAY' or 'GTC'). Defaults to 'DAY'.
            adjust_limit: Adjust limit percentage.
            aux_price: Trigger price for stop/if-touched order types.
            trail_type: Trailing type ('RATIO' or 'AMOUNT') for trailing stop types.
            trail_value: Trailing value (ratio or amount) for trailing stop types.
            trail_spread: Optional trailing spread for trailing stop limit types.
            trd_env: Trading environment ('REAL' or 'SIMULATE').
            acc_id: Account ID. Must be obtained from get_accounts().
            remark: Order remark/note.

        Returns:
            Dictionary with order details including order_id.

        Raises:
            TradingPolicyError: If the configured mode does not permit a write
                to trd_env.
        """
        # Checked first, before the account lookup below: a denied order must
        # make no gateway request at all, not even to resolve an account.
        self.policy.check_write("place_order", trd_env)

        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Smart account selection if acc_id is default (0)
        if acc_id == 0:
            market = self._get_market_from_code(code)
            if market:
                # Try to find a specific account for this market
                # If valid account found, use it. 
                # If none found that support the market, it will raise ValueError
                acc_id = self._find_best_account(trd_env, market)

        stop_order_types = {
            "STOP",
            "STOP_LIMIT",
            "MARKET_IF_TOUCHED",
            "LIMIT_IF_TOUCHED",
        }
        trailing_order_types = {"TRAILING_STOP", "TRAILING_STOP_LIMIT"}
        if order_type in stop_order_types and aux_price is None:
            raise ValueError("aux_price is required for stop/if-touched order types")
        if order_type in trailing_order_types and (
            trail_type is None or trail_value is None
        ):
            raise ValueError(
                "trail_type and trail_value are required for trailing stop order types"
            )

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=qty,
            code=code,
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
        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    def _build_combo_legs(self, combo_legs: list[dict]) -> list[ComboLeg]:
        """Validate leg dictionaries and convert them to SDK ComboLeg objects.

        Validation happens before any gateway call so that a malformed strategy
        fails fast with an actionable message rather than an opaque protocol error.

        Args:
            combo_legs: Leg dictionaries, each with 'code', 'trd_side', 'qty_ratio'.

        Returns:
            List of ComboLeg objects ready for the SDK.

        Raises:
            ValueError: If the leg list is malformed.
        """
        if len(combo_legs) < 2:
            raise ValueError(
                "A combo order requires at least two legs. "
                "Use place_order for single-leg orders."
            )

        valid_sides = {"BUY", "SELL"}
        legs: list[ComboLeg] = []
        markets: set[str] = set()

        for index, leg in enumerate(combo_legs):
            code = str(leg.get("code") or "").strip()
            if not code:
                raise ValueError(f"Leg {index} is missing a non-empty 'code'.")

            trd_side = str(leg.get("trd_side") or "").strip().upper()
            if trd_side not in valid_sides:
                raise ValueError(
                    f"Invalid trd_side '{leg.get('trd_side')}' on leg {index}. "
                    f"Valid options: {sorted(valid_sides)}"
                )

            # qty_ratio is a multiplier, not a convenience default: the actual
            # quantity of a leg is (order qty x qty_ratio). Assuming 1 for an
            # omitted ratio would silently submit a different strategy — a 1:2:1
            # butterfly would become 1:1:1 — so an absent ratio is an error.
            if "qty_ratio" not in leg:
                raise ValueError(
                    f"Leg {index} is missing 'qty_ratio'. It multiplies the order "
                    "quantity for this leg, so it must be stated explicitly."
                )
            qty_ratio = leg["qty_ratio"]
            if not isinstance(qty_ratio, int) or isinstance(qty_ratio, bool):
                raise ValueError(
                    f"Leg {index} has a non-integer 'qty_ratio': {qty_ratio!r}"
                )
            if qty_ratio <= 0:
                raise ValueError(
                    f"Leg {index} has a 'qty_ratio' of {qty_ratio}; it must be a "
                    "positive integer."
                )

            market = self._get_market_from_code(code)
            if market:
                markets.add(market)

            combo_leg = ComboLeg()
            combo_leg.code = code
            combo_leg.trd_side = trd_side
            combo_leg.qty_ratio = qty_ratio

            # Required by the gateway when the order closes an existing position.
            # Obtained from get_positions(show_option_strategy_view=True).
            # Accept an exact integer, or a decimal string. Strings matter because
            # these identifiers exceed the range JSON consumers can represent
            # exactly. Anything lossy is refused rather than coerced: int(True) is
            # 1 and int(123.75) is 123, and silently trading on either would target
            # the wrong position.
            position_id = leg.get("position_id")
            if position_id is not None:
                if isinstance(position_id, bool):
                    raise ValueError(
                        f"Leg {index} has a boolean 'position_id': {position_id!r}"
                    )
                if isinstance(position_id, int):
                    combo_leg.position_id = position_id
                elif isinstance(position_id, str) and position_id.strip().isdigit():
                    combo_leg.position_id = int(position_id.strip())
                else:
                    raise ValueError(
                        f"Leg {index} has a non-integer 'position_id': "
                        f"{position_id!r}. Provide an integer or a decimal string."
                    )

            legs.append(combo_leg)

        if len(markets) > 1:
            raise ValueError(
                "All combo legs must belong to the same market, got: "
                f"{sorted(markets)}"
            )

        return legs

    def place_combo_order(
        self,
        combo_legs: list[dict],
        price: float,
        qty: int,
        order_type: str = "NORMAL",
        time_in_force: str = "DAY",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        remark: str = "",
    ) -> dict:
        """Place a multi-leg option strategy as a single atomic order.

        The package fills as one unit or not at all, so a strategy can never be
        left half-executed the way independent single-leg orders can.

        Args:
            combo_legs: Legs of the strategy. Each is a dict with 'code',
                'trd_side' ('BUY' or 'SELL'), and 'qty_ratio' (positive int,
                required — it multiplies the order qty for that leg). May also
                carry 'position_id', which the gateway requires when the order
                closes an existing position; obtain it from
                get_positions(show_option_strategy_view=True).
            price: Net price of the whole package, not a per-leg price.
            qty: Number of packages to trade.
            order_type: Order type ('NORMAL' for limit, 'MARKET', etc.).
            time_in_force: Time in force ('DAY' or 'GTC'). Defaults to 'DAY'.
            trd_env: Trading environment ('REAL' or 'SIMULATE').
            acc_id: Account ID. Must be obtained from get_accounts().
            remark: Order remark/note.

        Returns:
            Dictionary with order details including order_id.

        Raises:
            TradingPolicyError: If the configured mode does not permit a write
                to trd_env.
            ValueError: If the leg list is malformed.
            RuntimeError: If not connected, or the gateway rejects the order.
        """
        self.policy.check_write("place_combo_order", trd_env)

        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        legs = self._build_combo_legs(combo_legs)

        # Smart account selection mirrors place_order, keyed off the legs' market.
        if acc_id == 0:
            market = self._get_market_from_code(legs[0].code)
            if market:
                acc_id = self._find_best_account(trd_env, market)

        ret, data = self.trade_ctx.place_combo_order(
            combo_leg_list=legs,
            price=price,
            qty=qty,
            order_type=order_type,
            time_in_force=time_in_force,
            trd_env=trd_env,
            acc_id=acc_id,
            remark=remark,
        )
        if ret != RET_OK:
            raise RuntimeError(f"place_combo_order failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    # The account-impact fields comboorder_tradinginfo_query returns. Listed
    # here rather than passed through wholesale so a caller sees a stable set of
    # keys, with an explicit null for anything the gateway did not supply.
    COMBO_PREVIEW_FIELDS = (
        "nlv_change",
        "initial_margin_change",
        "maintenance_margin_change",
        "option_bp",
        "max_withdraw_change",
        "bp_decrease",
    )

    def preview_combo_order(
        self,
        combo_legs: list[dict],
        price: float,
        qty: int,
        order_type: str = "NORMAL",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Preview the account impact of a multi-leg package without submitting it.

        This is a read: it asks the gateway what the package would do to margin
        and buying power. Nothing is placed, modified, cancelled, unlocked, or
        reserved, so it is permitted in every trading mode — though the broker
        can still refuse the query itself.

        Leg validation and account selection are shared with place_combo_order,
        so a package that previews is the same package that would be submitted.

        Args:
            combo_legs: Legs of the strategy, in the same format as
                place_combo_order: 'code', 'trd_side', 'qty_ratio' (required),
                and 'position_id' when closing an existing position.
            price: Net price of the whole package. The sign convention is passed
                through untouched: moomoo does not document a debit/credit
                convention, and normalizing it here would invent one.
            qty: Number of packages.
            order_type: Order type ('NORMAL' for limit, 'MARKET', etc.).
            trd_env: Trading environment ('REAL' or 'SIMULATE').
            acc_id: Account ID. Resolved from the legs' market when omitted.

        Returns:
            Dictionary with 'checked_at' (UTC observation time), the resolved
            'acc_id' and 'trd_env', and the gateway's impact fields. A field the
            gateway did not supply is None, never a substituted zero.

            The values are a point-in-time calculation. They are not a quote, an
            acceptance, or a guarantee that the package would fill.

        Raises:
            ValueError: If the leg list is malformed.
            RuntimeError: If not connected, or the gateway rejects the query.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        legs = self._build_combo_legs(combo_legs)

        # Same account selection as placement, so the preview describes the
        # account the order would actually reach.
        if acc_id == 0:
            market = self._get_market_from_code(legs[0].code)
            if market:
                acc_id = self._find_best_account(trd_env, market)

        ret, data = self.trade_ctx.comboorder_tradinginfo_query(
            combo_leg_list=legs,
            price=price,
            qty=qty,
            order_type=order_type,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"comboorder_tradinginfo_query failed: {data}")

        records = data.to_dict("records") if data is not None else []
        record = records[0] if records else {}

        preview: dict[str, Any] = {
            "checked_at": utc_now_iso(),
            "acc_id": acc_id,
            "trd_env": trd_env,
        }
        for field in self.COMBO_PREVIEW_FIELDS:
            preview[field] = _null_if_missing(record.get(field))
        return preview

    def modify_order(
        self,
        order_id: str,
        modify_order_op: str,
        qty: int | None = None,
        price: float | None = None,
        adjust_limit: float = 0,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Modify an existing order.

        Args:
            order_id: Order ID to modify.
            modify_order_op: Modification operation ('NORMAL', 'CANCEL', 'DISABLE', 'ENABLE', 'DELETE').
            qty: New quantity (optional).
            price: New price (optional).
            adjust_limit: Adjust limit percentage.
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            Dictionary with modified order details.

        Raises:
            TradingPolicyError: If the configured mode does not permit a write
                to trd_env.
        """
        self.policy.check_write(f"modify_order ({modify_order_op})", trd_env)

        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=modify_order_op,
            order_id=order_id,
            qty=qty,
            price=price,
            adjust_limit=adjust_limit,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"modify_order failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    def cancel_order(
        self,
        order_id: str,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Cancel an existing order.

        Convenience wrapper around modify_order with CANCEL operation.

        Args:
            order_id: Order ID to cancel.
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            Dictionary with cancelled order details.

        Raises:
            TradingPolicyError: If the configured mode does not permit a write
                to trd_env. Cancellation is a write like any other: a read-only
                deployment cannot cancel an order it was never able to place.
        """
        self.policy.check_write("cancel_order", trd_env)

        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.modify_order(
            modify_order_op="CANCEL",
            order_id=order_id,
            qty=0,
            price=0,
            adjust_limit=0,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")

        records = data.to_dict("records")
        return records[0] if records else {}

    def get_orders(
        self,
        code: str = "",
        status_filter_list: list[str] | None = None,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
    ) -> list[dict]:
        """Get list of today's orders.

        Args:
            code: Filter by stock code.
            status_filter_list: Filter by order statuses (as strings).
                Valid options: UNSUBMITTED, WAITING_SUBMIT, SUBMITTING, SUBMIT_FAILED,
                SUBMITTED, FILLED_PART, FILLED_ALL, CANCELLING_PART, CANCELLING_ALL,
                CANCELLED_PART, CANCELLED_ALL, REJECTED, DISABLED, DELETED, FAILED, NONE.
            trd_env: Trading environment.
            acc_id: Account ID.
            refresh_cache: Whether to refresh cache.

        Returns:
            List of order dictionaries. Returns empty list if no orders found.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Convert string status values to OrderStatus enum values
        converted_status_filter = self._convert_status_filter(status_filter_list)

        ret, data = self.trade_ctx.order_list_query(
            code=code,
            status_filter_list=converted_status_filter,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
        if ret != RET_OK:
            raise RuntimeError(f"order_list_query failed: {data}")

        # Handle None or empty DataFrame gracefully
        if data is None or data.empty:
            return []

        return data.to_dict("records")

    def get_deals(
        self,
        code: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
    ) -> list[dict]:
        """Get list of today's deals (executed trades).

        Args:
            code: Filter by stock code.
            trd_env: Trading environment.
            acc_id: Account ID.
            refresh_cache: Whether to refresh cache.

        Returns:
            List of deal dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.deal_list_query(
            code=code,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
        if ret != RET_OK:
            raise RuntimeError(f"deal_list_query failed: {data}")

        return data.to_dict("records")

    def get_history_orders(
        self,
        code: str = "",
        status_filter_list: list[str] | None = None,
        start: str = "",
        end: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get historical orders.

        Args:
            code: Filter by stock code.
            status_filter_list: Filter by order statuses (as strings).
                Valid options: UNSUBMITTED, WAITING_SUBMIT, SUBMITTING, SUBMIT_FAILED,
                SUBMITTED, FILLED_PART, FILLED_ALL, CANCELLING_PART, CANCELLING_ALL,
                CANCELLED_PART, CANCELLED_ALL, REJECTED, DISABLED, DELETED, FAILED, NONE.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            List of historical order dictionaries. Returns empty list if no orders found.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Convert string status values to OrderStatus enum values
        converted_status_filter = self._convert_status_filter(status_filter_list)

        ret, data = self.trade_ctx.history_order_list_query(
            code=code,
            status_filter_list=converted_status_filter,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"history_order_list_query failed: {data}")

        # Handle None or empty DataFrame gracefully
        if data is None or data.empty:
            return []

        return data.to_dict("records")

    def get_history_deals(
        self,
        code: str = "",
        start: str = "",
        end: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get historical deals (executed trades).

        Args:
            code: Filter by stock code.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            List of historical deal dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.history_deal_list_query(
            code=code,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"history_deal_list_query failed: {data}")

        return data.to_dict("records")
