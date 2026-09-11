"""Market data service for accessing quote data via Moomoo API."""

from moomoo import (
    RET_OK,
    AuType,
    KLType,
    OpenQuoteContext,
    OptionType,
    SubType,
    TradeDateMarket,
)

from moomoo_mcp.services.validation import validate_choice, validate_date_range

# get_option_chain accepts at most a 30-day expiry window (the SDK's own default
# expansion uses 29 days from one supplied bound).
OPTION_CHAIN_MAX_SPAN_DAYS = 29

OPTION_TYPES = ("ALL", "CALL", "PUT")

# Subscription data types a caller may name. NONE is the SDK's placeholder
# and is not a subscription anyone can hold.
SUBSCRIPTION_TYPES = tuple(
    value for value in SubType.get_all_key_list() if value != "N/A"
)

# Candle intervals and adjustments, named as the tools document them. Taken
# from the SDK's own attributes so the accepted set cannot drift from what
# the SDK supports. AuType's wire values are lowercase ('qfq'), so the
# uppercase name is what a caller passes and the value is what is forwarded.
KLINE_TYPES = tuple(name for name in vars(KLType) if name.isupper() and name != "NONE")
ADJUSTMENT_TYPES = tuple(name for name in vars(AuType) if name.isupper())


def validate_candle_filters(ktype: str, autype: str) -> tuple[str, str]:
    """Resolve a candle interval and adjustment, rejecting unknown values.

    These were previously resolved with a defaulting getattr, so 'K_5MIN'
    silently became daily candles and 'UNADJUSTED' silently became forward
    adjustment. The caller got a valid-looking series answering a different
    question than the one asked, with nothing in the response to say so — and
    for the paginated tool, a cursor stamped with the interval that was
    requested rather than the one that was served. An unknown value is an
    error instead.

    Args:
        ktype: Candle interval name, e.g. 'K_DAY'.
        autype: Adjustment name: 'QFQ', 'HFQ', or 'NONE'.

    Returns:
        The SDK enum values to forward.

    Raises:
        ValueError: If either name is not supported.
    """
    interval = validate_choice("ktype", ktype, KLINE_TYPES)
    adjustment = validate_choice("autype", autype, ADJUSTMENT_TYPES)
    return getattr(KLType, interval), getattr(AuType, adjustment)


# Markets the provider will return a trading calendar for. NONE is excluded:
# it is the SDK's 'unspecified' placeholder, not a market a caller can mean.
TRADING_DAY_MARKETS = tuple(
    value for value in TradeDateMarket.get_all_key_list() if value != "N/A"
)


class MarketDataService:
    """Service to access market data via OpenQuoteContext.

    This service provides methods to retrieve market quotes, historical K-line data,
    snapshots, and order book data. It uses the shared OpenQuoteContext from
    MoomooService.
    """

    def __init__(self, quote_ctx: OpenQuoteContext):
        """Initialize MarketDataService with an existing quote context.

        Args:
            quote_ctx: An already-connected OpenQuoteContext instance.
        """
        self.quote_ctx = quote_ctx

    def subscribe(self, codes: list[str], sub_types: list[SubType]) -> None:
        """Subscribe to real-time data for specified stocks and data types.

        Args:
            codes: List of stock codes (e.g., ['US.AAPL', 'HK.00700']).
            sub_types: List of subscription types (e.g.,
                [SubType.QUOTE, SubType.ORDER_BOOK]).

        Raises:
            RuntimeError: If subscription fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        ret, err = self.quote_ctx.subscribe(codes, sub_types, subscribe_push=False)
        if ret != RET_OK:
            raise RuntimeError(f"subscribe failed: {err}")

    def get_subscriptions(self) -> dict:
        """Get the subscriptions held by this server's quote connection.

        Returns:
            The provider's subscription report, scoped to this connection with
            ``is_all_conn=False``. 'sub_list' maps each subscription type to the
            codes this connection holds; quota fields describe usage, and some
            of them are provider-wide rather than per-connection — the caller is
            told which is which at the tool boundary.

        Raises:
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        # is_all_conn=False: report what this connection holds, not what every
        # client attached to the same OpenD holds.
        ret, data = self.quote_ctx.query_subscription(is_all_conn=False)
        if ret != RET_OK:
            raise RuntimeError(f"query_subscription failed: {data}")

        return data if isinstance(data, dict) else {}

    def unsubscribe(self, codes: list[str], sub_types: list[str]) -> None:
        """Release specific subscriptions held by this connection.

        Args:
            codes: Security codes to release (e.g., ['US.AAPL']).
            sub_types: Subscription types to release (e.g., ['QUOTE']).

        Raises:
            ValueError: If either list is empty, a code is blank, or a
                subscription type is unsupported.
            RuntimeError: If not connected, or the provider refuses the release
                — a minimum-hold period or a permission problem, for example.
                The failure is surfaced as-is and not retried: repeatedly
                re-asking a provider that imposes a cooldown does not shorten
                it.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        if not codes:
            raise ValueError("codes must contain at least one security code.")
        cleaned_codes = [str(code or "").strip() for code in codes]
        if not all(cleaned_codes):
            raise ValueError("codes must not contain empty security codes.")

        if not sub_types:
            raise ValueError(
                "sub_types must contain at least one subscription type, "
                f"such as 'QUOTE'. Valid values: {list(SUBSCRIPTION_TYPES)}."
            )
        cleaned_types = [
            validate_choice("sub_types", value, SUBSCRIPTION_TYPES)
            for value in sub_types
        ]

        # unsubscribe_all is never passed: this server releases only what it was
        # asked to release, on its own connection.
        ret, err = self.quote_ctx.unsubscribe(
            code_list=cleaned_codes, subtype_list=cleaned_types
        )
        if ret != RET_OK:
            raise RuntimeError(f"unsubscribe failed: {err}")

    def get_stock_quote(self, codes: list[str]) -> list[dict]:
        """Get real-time quotes for stocks.

        This method automatically subscribes to the stocks before fetching quotes.
        Returns current price, open, high, low, close, volume and other quote data.

        Args:
            codes: List of stock codes (e.g., ['US.AAPL']).

        Returns:
            List of quote dictionaries with price, volume, and other quote fields.

        Raises:
            RuntimeError: If quote retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        # Auto-subscribe before getting quotes
        self.subscribe(codes, [SubType.QUOTE])

        ret, data = self.quote_ctx.get_stock_quote(codes)
        if ret != RET_OK:
            raise RuntimeError(f"get_stock_quote failed: {data}")

        return data.to_dict("records")

    def get_historical_klines(
        self,
        code: str,
        ktype: str = "K_DAY",
        start: str | None = None,
        end: str | None = None,
        max_count: int = 100,
        autype: str = "QFQ",
    ) -> list[dict]:
        """Get historical candlestick (K-line) data.

        Args:
            code: Stock code (e.g., 'US.AAPL').
            ktype: K-line type. Options: K_1M, K_3M, K_5M, K_15M, K_30M, K_60M,
                   K_DAY, K_WEEK, K_MON, K_QUARTER, K_YEAR.
            start: Start date (YYYY-MM-DD format). Defaults to 365 days before end.
            end: End date (YYYY-MM-DD format). Defaults to today.
            max_count: Maximum number of candles to return (default 100).
            autype: Adjustment type. Options: QFQ (forward), HFQ (backward), NONE.

        Returns:
            List of K-line dictionaries with time_key, open, high, low, close, volume.
            This is one page: the provider's continuation token is discarded, so
            the list can be a prefix of the requested range. Use
            get_historical_klines_page to traverse the whole range.

        Raises:
            RuntimeError: If K-line retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        ktype_enum, autype_enum = validate_candle_filters(ktype, autype)

        ret, data, _ = self.quote_ctx.request_history_kline(
            code=code,
            start=start,
            end=end,
            ktype=ktype_enum,
            autype=autype_enum,
            max_count=max_count,
        )
        if ret != RET_OK:
            raise RuntimeError(f"request_history_kline failed: {data}")

        return data.to_dict("records")

    def get_historical_klines_page(
        self,
        code: str,
        ktype: str = "K_DAY",
        start: str | None = None,
        end: str | None = None,
        max_count: int = 100,
        autype: str = "QFQ",
        page_req_key: bytes | None = None,
    ) -> tuple[list[dict], bytes | None]:
        """Fetch one page of historical candles and the provider's continuation.

        Unlike get_historical_klines, the provider's continuation token is
        returned instead of discarded, so a caller can walk the whole range.

        Args:
            code: Stock code (e.g., 'US.AAPL').
            ktype: K-line type (K_1M ... K_YEAR).
            start: Start date (YYYY-MM-DD). Resolved by the caller for paging.
            end: End date (YYYY-MM-DD).
            max_count: Maximum candles in this page.
            autype: Adjustment type: QFQ, HFQ, or NONE.
            page_req_key: Continuation token from the previous page, or None for
                the first page.

        Returns:
            Tuple of (candle dictionaries, continuation token). The token is
            None when the provider has no further pages. A page can be empty
            while still carrying a token.

        Raises:
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        ktype_enum, autype_enum = validate_candle_filters(ktype, autype)

        ret, data, next_page_req_key = self.quote_ctx.request_history_kline(
            code=code,
            start=start,
            end=end,
            ktype=ktype_enum,
            autype=autype_enum,
            max_count=max_count,
            page_req_key=page_req_key,
        )
        if ret != RET_OK:
            raise RuntimeError(f"request_history_kline failed: {data}")

        rows = data.to_dict("records") if data is not None else []
        return rows, next_page_req_key

    def get_option_expiration_date(self, code: str) -> list[dict]:
        """Get the option expiration dates available for an underlying.

        Args:
            code: Underlying security code (e.g., 'US.AAPL', 'HK.00700').

        Returns:
            List of provider expiration records with 'strike_time' (the
            expiration date, in the market's own timezone),
            'option_expiry_date_distance' (days until expiry; negative when
            already expired), and 'expiration_cycle'. An empty list means the
            provider reported no expirations, which is not the same as an error.

        Raises:
            ValueError: If the code is empty.
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        underlying = str(code or "").strip()
        if not underlying:
            raise ValueError("code must be a non-empty security code, e.g. 'US.AAPL'.")

        ret, data = self.quote_ctx.get_option_expiration_date(code=underlying)
        if ret != RET_OK:
            raise RuntimeError(f"get_option_expiration_date failed: {data}")

        return data.to_dict("records")

    def get_option_chain(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
        option_type: str = "ALL",
    ) -> list[dict]:
        """Get option contracts for an underlying within an expiry date range.

        Args:
            code: Underlying security code (e.g., 'US.AAPL').
            start: First expiration date to include (YYYY-MM-DD). When omitted,
                the provider derives it from ``end``; when both are omitted it
                uses today and the following 30 days.
            end: Last expiration date to include (YYYY-MM-DD), inclusive.
            option_type: 'ALL' (default), 'CALL', or 'PUT'.

        Returns:
            List of contract records with the exact provider 'code' — the symbol
            to pass to quote, preview, and order tools — plus 'name',
            'stock_owner', 'option_type', 'strike_time', 'strike_price',
            'lot_size', and related metadata. An empty list means no contracts
            matched, which is a successful result.

        Raises:
            ValueError: If the code is empty, a date is malformed, start follows
                end, the range exceeds the provider's 30-day limit, or
                option_type is unsupported.
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        underlying = str(code or "").strip()
        if not underlying:
            raise ValueError("code must be a non-empty security code, e.g. 'US.AAPL'.")

        validate_date_range(
            start,
            end,
            max_span_days=OPTION_CHAIN_MAX_SPAN_DAYS,
            span_label="30 days",
        )
        normalized_type = validate_choice("option_type", option_type, OPTION_TYPES)

        ret, data = self.quote_ctx.get_option_chain(
            code=underlying,
            start=start,
            end=end,
            option_type=getattr(OptionType, normalized_type),
        )
        if ret != RET_OK:
            raise RuntimeError(f"get_option_chain failed: {data}")

        return data.to_dict("records")

    def get_market_snapshot(self, codes: list[str]) -> list[dict]:
        """Get market snapshot for multiple stocks.

        This is efficient for batch queries and does not require subscription.
        Returns current price, change, volume, and comprehensive market data.

        Args:
            codes: List of stock codes (up to 400). E.g., ['US.AAPL', 'US.TSLA'].

        Returns:
            List of snapshot dictionaries with last_price, open_price, high_price,
            low_price, prev_close_price, volume, turnover, and more.

        Raises:
            RuntimeError: If snapshot retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        if not codes:
            return []

        ret, data = self.quote_ctx.get_market_snapshot(codes)
        if ret != RET_OK:
            raise RuntimeError(f"get_market_snapshot failed: {data}")

        return data.to_dict("records")

    def get_order_book(self, code: str, num: int = 10) -> dict:
        """Get order book (market depth) for a stock.

        This method automatically subscribes to the stock before fetching
        the order book. Returns bid and ask price levels with volumes.

        Args:
            code: Stock code (e.g., 'HK.00700').
            num: Number of price levels to return (default 10).

        Returns:
            Dictionary with 'code', 'Bid' (list of tuples), and 'Ask' (list of tuples).
            Each tuple contains (price, volume, order_count, order_details).

        Raises:
            RuntimeError: If order book retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        # Auto-subscribe before getting order book
        self.subscribe([code], [SubType.ORDER_BOOK])

        ret, data = self.quote_ctx.get_order_book(code, num=num)
        if ret != RET_OK:
            raise RuntimeError(f"get_order_book failed: {data}")

        return data

    def get_market_state(self, codes: list[str]) -> list[dict]:
        """Get the provider's reported session state for each instrument.

        Args:
            codes: Security codes (e.g., ['US.AAPL', 'HK.00700']).

        Returns:
            List of dictionaries with 'code', 'stock_name', and 'market_state'.
            The state is the provider's own value (for example 'MORNING',
            'REST', 'CLOSED', 'PRE_MARKET_BEGIN'), preserved rather than reduced
            to an open/closed boolean, because those states are not equivalent
            and the distinctions matter for what can be traded.

        Raises:
            ValueError: If the code list is empty or contains a blank code.
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        if not codes:
            raise ValueError("codes must contain at least one security code.")
        cleaned = [str(code or "").strip() for code in codes]
        if not all(cleaned):
            raise ValueError("codes must not contain empty security codes.")

        ret, data = self.quote_ctx.get_market_state(code_list=cleaned)
        if ret != RET_OK:
            raise RuntimeError(f"get_market_state failed: {data}")

        return data.to_dict("records")

    def get_trading_days(
        self,
        market: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict]:
        """Get the provider's trading calendar for a market and date range.

        Args:
            market: Calendar market, one of TRADING_DAY_MARKETS (e.g., 'US',
                'HK', 'CN', 'JP', 'SG').
            start: First date to include (YYYY-MM-DD). When omitted, the
                provider derives it from ``end``; when both are omitted it uses
                the 365 days ending today.
            end: Last date to include (YYYY-MM-DD), inclusive.

        Returns:
            List of dictionaries with 'time' (a market-local calendar date) and
            'trade_date_type' (e.g., 'WHOLE', 'MORNING', 'AFTERNOON'). Only
            trading days appear: a holiday is absent from the list rather than
            being marked closed, and a shortened session is distinguished by its
            trade_date_type. Session opening and closing times are not part of
            this response and are not inferred.

        Raises:
            ValueError: If the market is unsupported, a date is malformed, or
                start follows end.
            RuntimeError: If not connected, or the provider rejects the query.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        normalized_market = validate_choice("market", market, TRADING_DAY_MARKETS)
        validate_date_range(start, end)

        ret, data = self.quote_ctx.request_trading_days(
            market=normalized_market,
            start=start,
            end=end,
        )
        if ret != RET_OK:
            raise RuntimeError(f"request_trading_days failed: {data}")

        # request_trading_days returns a list of dicts, not a DataFrame.
        return list(data or [])

    def get_user_security_group(self, group_type: int = 0) -> list[dict]:
        """Get list of user-defined security groups (watchlists).

        Args:
            group_type: Type of groups to return. Options:
                - 0: All groups (default)
                - 1: Custom groups only
                - 2: System groups only

        Returns:
            List of group dictionaries with group_name, group_id, etc.

        Raises:
            RuntimeError: If retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        from moomoo import UserSecurityGroupType

        group_type_enum = UserSecurityGroupType.ALL
        if group_type == 1:
            group_type_enum = UserSecurityGroupType.CUSTOM
        elif group_type == 2:
            group_type_enum = UserSecurityGroupType.SYSTEM

        ret, data = self.quote_ctx.get_user_security_group(group_type=group_type_enum)
        if ret != RET_OK:
            raise RuntimeError(f"get_user_security_group failed: {data}")

        return data.to_dict("records")

    def get_user_security(self, group_name: str) -> list[dict]:
        """Get list of securities in a specific user-defined group (watchlist).

        Args:
            group_name: Name of the security group (e.g., 'Favorites').

        Returns:
            List of security dictionaries with code, name, lot_size, etc.

        Raises:
            RuntimeError: If retrieval fails.
        """
        if not self.quote_ctx:
            raise RuntimeError("Quote context not connected")

        ret, data = self.quote_ctx.get_user_security(group_name)
        if ret != RET_OK:
            raise RuntimeError(f"get_user_security failed: {data}")

        return data.to_dict("records")
