"""Option expiration and chain discovery (R5)."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService

EXPIRATIONS = pd.DataFrame(
    [
        {
            "strike_time": "2026-01-16",
            "option_expiry_date_distance": 128,
            "expiration_cycle": "N/A",
        },
        {
            "strike_time": "2026-02-20",
            "option_expiry_date_distance": 163,
            "expiration_cycle": "N/A",
        },
    ]
)

CHAIN = pd.DataFrame(
    [
        {
            "code": "US.XYZ260116C100000",
            "name": "XYZ 260116 100.00 C",
            "stock_owner": "US.XYZ",
            "option_type": "CALL",
            "strike_time": "2026-01-16",
            "strike_price": 100.0,
            "lot_size": 100,
        },
        {
            "code": "US.XYZ260116P100000",
            "name": "XYZ 260116 100.00 P",
            "stock_owner": "US.XYZ",
            "option_type": "PUT",
            "strike_time": "2026-01-16",
            "strike_price": 100.0,
            "lot_size": 100,
        },
    ]
)


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.get_option_expiration_date.return_value = (0, EXPIRATIONS.copy())
    ctx.get_option_chain.return_value = (0, CHAIN.copy())
    return ctx


@pytest.fixture
def service(quote_ctx):
    return MarketDataService(quote_ctx=quote_ctx)


class TestExpirationLookup:
    """Provider expiration metadata is preserved as reported."""

    def test_returns_provider_dates(self, service, quote_ctx):
        expirations = service.get_option_expiration_date("US.XYZ")

        assert [row["strike_time"] for row in expirations] == [
            "2026-01-16",
            "2026-02-20",
        ]
        assert expirations[0]["option_expiry_date_distance"] == 128
        quote_ctx.get_option_expiration_date.assert_called_once_with(code="US.XYZ")

    def test_empty_result_is_an_empty_list(self, service, quote_ctx):
        quote_ctx.get_option_expiration_date.return_value = (
            0,
            pd.DataFrame([], columns=["strike_time"]),
        )

        assert service.get_option_expiration_date("US.XYZ") == []

    def test_provider_error_raises(self, service, quote_ctx):
        quote_ctx.get_option_expiration_date.return_value = (-1, "no option permission")

        with pytest.raises(RuntimeError, match="no option permission"):
            service.get_option_expiration_date("US.XYZ")

    def test_empty_code_is_rejected_before_the_query(self, service, quote_ctx):
        with pytest.raises(ValueError, match="non-empty security code"):
            service.get_option_expiration_date("  ")

        quote_ctx.get_option_expiration_date.assert_not_called()

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).get_option_expiration_date("US.XYZ")


class TestChainLookup:
    """Filters are forwarded exactly and symbols are preserved."""

    def test_returns_exact_contract_symbols(self, service):
        contracts = service.get_option_chain(
            "US.XYZ", start="2026-01-16", end="2026-01-16"
        )

        assert [row["code"] for row in contracts] == [
            "US.XYZ260116C100000",
            "US.XYZ260116P100000",
        ]
        assert contracts[0]["strike_price"] == 100.0
        assert contracts[0]["stock_owner"] == "US.XYZ"

    @pytest.mark.parametrize("option_type", ["ALL", "CALL", "PUT"])
    def test_option_type_enum_is_forwarded(self, service, quote_ctx, option_type):
        service.get_option_chain("US.XYZ", option_type=option_type)

        assert quote_ctx.get_option_chain.call_args.kwargs["option_type"] == option_type

    def test_option_type_is_case_insensitive(self, service, quote_ctx):
        service.get_option_chain("US.XYZ", option_type="call")

        assert quote_ctx.get_option_chain.call_args.kwargs["option_type"] == "CALL"

    def test_dates_are_forwarded_unchanged(self, service, quote_ctx):
        service.get_option_chain("US.XYZ", start="2026-01-01", end="2026-01-16")

        kwargs = quote_ctx.get_option_chain.call_args.kwargs
        assert kwargs["start"] == "2026-01-01"
        assert kwargs["end"] == "2026-01-16"
        assert kwargs["code"] == "US.XYZ"

    def test_single_expiry_range_is_supported(self, service, quote_ctx):
        service.get_option_chain("US.XYZ", start="2026-01-16", end="2026-01-16")

        kwargs = quote_ctx.get_option_chain.call_args.kwargs
        assert kwargs["start"] == kwargs["end"] == "2026-01-16"

    def test_omitted_dates_are_passed_through_as_none(self, service, quote_ctx):
        service.get_option_chain("US.XYZ")

        kwargs = quote_ctx.get_option_chain.call_args.kwargs
        assert kwargs["start"] is None
        assert kwargs["end"] is None

    def test_empty_chain_is_an_empty_list(self, service, quote_ctx):
        quote_ctx.get_option_chain.return_value = (
            0,
            pd.DataFrame([], columns=["code"]),
        )

        assert service.get_option_chain("US.XYZ") == []

    def test_provider_error_raises_rather_than_returning_empty(
        self, service, quote_ctx
    ):
        quote_ctx.get_option_chain.return_value = (-1, "unsupported underlying")

        with pytest.raises(RuntimeError, match="unsupported underlying"):
            service.get_option_chain("US.XYZ")


class TestChainValidation:
    """Bad filters fail before an SDK call."""

    def test_reversed_dates_are_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="is after end"):
            service.get_option_chain("US.XYZ", start="2026-02-01", end="2026-01-16")

        quote_ctx.get_option_chain.assert_not_called()

    @pytest.mark.parametrize("bad", ["16-01-2026", "2026/01/16", "next friday", ""])
    def test_malformed_dates_are_rejected(self, service, quote_ctx, bad):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            service.get_option_chain("US.XYZ", start=bad, end="2026-02-01")

        quote_ctx.get_option_chain.assert_not_called()

    def test_range_wider_than_the_provider_limit_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="at most 30 days"):
            service.get_option_chain("US.XYZ", start="2026-01-01", end="2026-06-30")

        quote_ctx.get_option_chain.assert_not_called()

    def test_exactly_thirty_days_is_accepted(self, service, quote_ctx):
        service.get_option_chain("US.XYZ", start="2026-01-01", end="2026-01-30")

        quote_ctx.get_option_chain.assert_called_once()

    def test_unsupported_option_type_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="option_type must be one of"):
            service.get_option_chain("US.XYZ", option_type="STRADDLE")

        quote_ctx.get_option_chain.assert_not_called()

    def test_unsupported_option_type_is_not_replaced_with_a_default(
        self, service, quote_ctx
    ):
        with pytest.raises(ValueError):
            service.get_option_chain("US.XYZ", option_type="CALLS")

        quote_ctx.get_option_chain.assert_not_called()

    def test_empty_code_is_rejected(self, service, quote_ctx):
        with pytest.raises(ValueError, match="non-empty security code"):
            service.get_option_chain("")

        quote_ctx.get_option_chain.assert_not_called()

    def test_requires_a_connection(self):
        with pytest.raises(RuntimeError, match="Quote context not connected"):
            MarketDataService(quote_ctx=None).get_option_chain("US.XYZ")
