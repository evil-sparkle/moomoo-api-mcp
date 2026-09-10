"""Market state and trading-calendar queries (R6)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService

STATES = pd.DataFrame(
    [
        {"code": "US.AAPL", "stock_name": "Apple", "market_state": "AFTER_HOURS_BEGIN"},
        {"code": "HK.00700", "stock_name": "Tencent", "market_state": "CLOSED"},
    ]
)

# A holiday inside the range is simply absent, and 2026-01-05 is a half day.
TRADING_DAYS = [
    {"time": "2026-01-02", "trade_date_type": "WHOLE"},
    {"time": "2026-01-05", "trade_date_type": "MORNING"},
    {"time": "2026-01-06", "trade_date_type": "WHOLE"},
]


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.get_market_state.return_value = (0, STATES.copy())
    ctx.request_trading_days.return_value = (0, list(TRADING_DAYS))
    return ctx


@pytest.fixture
def service(quote_ctx):
    return MarketDataService(quote_ctx=quote_ctx)


class TestMarketState:
    """Provider states are reported as observed."""

    def test_returns_provider_states(self, service, quote_ctx):
        states = service.get_market_state(["US.AAPL", "HK.00700"])

        assert states[0]["market_state"] == "AFTER_HOURS_BEGIN"
        assert states[1]["market_state"] == "CLOSED"
        quote_ctx.get_market_state.assert_called_once_with(
            code_list=["US.AAPL", "HK.00700"]
        )

    def test_per_instrument_distinctions_are_preserved(self, service):
        states = service.get_market_state(["US.AAPL", "HK.00700"])

        assert {row["code"]: row["market_state"] for row in states} == {
            "US.AAPL": "AFTER_HOURS_BEGIN",
            "HK.00700": "CLOSED",
        }

    def test_session_boundary_states_are_not_collapsed(self, service, quote_ctx):
        """A midday break is not the same as closed and must stay distinct."""
        quote_ctx.get_market_state.return_value = (
            0,
            pd.DataFrame(
                [
                    {"code": "HK.00700", "stock_name": "T", "market_state": "REST"},
                    {"code": "HK.00005", "stock_name": "H", "market_state": "MORNING"},
                ]
            ),
        )

        states = service.get_market_state(["HK.00700", "HK.00005"])

        assert [row["market_state"] for row in states] == ["REST", "MORNING"]

    def test_codes_are_trimmed_before_forwarding(self, service, quote_ctx):
        service.get_market_state([" US.AAPL "])

        assert quote_ctx.get_market_state.call_args.kwargs["code_list"] == ["US.AAPL"]

    def test_empty_code_list_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="at least one security code"):
            service.get_market_state([])

        quote_ctx.get_market_state.assert_not_called()

    def test_blank_code_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="empty security codes"):
            service.get_market_state(["US.AAPL", "  "])

        quote_ctx.get_market_state.assert_not_called()

    def test_provider_error_raises(self, service, quote_ctx):
        quote_ctx.get_market_state.return_value = (-1, "unknown code")

        with pytest.raises(RuntimeError, match="unknown code"):
            service.get_market_state(["US.NOPE"])

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).get_market_state(["US.AAPL"])


class TestTradingDays:
    """Provider calendars are preserved, not derived."""

    def test_returns_provider_days(self, service, quote_ctx):
        days = service.get_trading_days("US", start="2026-01-01", end="2026-01-07")

        assert days == TRADING_DAYS
        quote_ctx.request_trading_days.assert_called_once_with(
            market="US", start="2026-01-01", end="2026-01-07"
        )

    def test_holiday_in_range_is_absent_not_marked_closed(self, service):
        days = service.get_trading_days("US", start="2026-01-01", end="2026-01-07")

        listed = {row["time"] for row in days}
        # 2026-01-01 and 2026-01-07 were in range but are not trading days.
        assert "2026-01-01" not in listed
        assert "2026-01-07" not in listed
        assert all("closed" not in str(row).lower() for row in days)

    def test_partial_session_metadata_is_preserved(self, service):
        days = service.get_trading_days("HK", start="2026-01-01", end="2026-01-07")

        half_day = next(row for row in days if row["time"] == "2026-01-05")
        assert half_day["trade_date_type"] == "MORNING"

    def test_no_session_times_are_invented(self, service):
        days = service.get_trading_days("US")

        for row in days:
            assert set(row) == {"time", "trade_date_type"}

    def test_empty_calendar_is_an_empty_list(self, service, quote_ctx):
        quote_ctx.request_trading_days.return_value = (0, [])

        days = service.get_trading_days("US", start="2026-01-01", end="2026-01-02")

        assert days == []

    def test_none_payload_becomes_an_empty_list(self, service, quote_ctx):
        quote_ctx.request_trading_days.return_value = (0, None)

        assert service.get_trading_days("US") == []

    @pytest.mark.parametrize("market", ["US", "HK", "CN", "JP", "SG", "MY"])
    def test_supported_markets_are_forwarded(self, service, quote_ctx, market):
        service.get_trading_days(market)

        assert quote_ctx.request_trading_days.call_args.kwargs["market"] == market

    def test_market_is_case_insensitive(self, service, quote_ctx):
        service.get_trading_days("us")

        assert quote_ctx.request_trading_days.call_args.kwargs["market"] == "US"

    def test_unsupported_market_is_rejected_before_the_query(self, service, quote_ctx):
        with pytest.raises(ValueError, match="market must be one of"):
            service.get_trading_days("MARS")

        quote_ctx.request_trading_days.assert_not_called()

    def test_unsupported_market_is_not_normalized_to_another_one(
        self, service, quote_ctx
    ):
        with pytest.raises(ValueError):
            service.get_trading_days("USA")

        quote_ctx.request_trading_days.assert_not_called()

    def test_placeholder_market_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="market must be one of"):
            service.get_trading_days("N/A")

        quote_ctx.request_trading_days.assert_not_called()

    def test_reversed_dates_are_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="is after end"):
            service.get_trading_days("US", start="2026-02-01", end="2026-01-01")

        quote_ctx.request_trading_days.assert_not_called()

    def test_malformed_date_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            service.get_trading_days("US", start="Jan 1 2026")

        quote_ctx.request_trading_days.assert_not_called()

    def test_provider_error_raises_without_a_guessed_calendar(self, service, quote_ctx):
        quote_ctx.request_trading_days.return_value = (-1, "market not supported")

        with pytest.raises(RuntimeError, match="market not supported"):
            service.get_trading_days("US")

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).get_trading_days("US")
