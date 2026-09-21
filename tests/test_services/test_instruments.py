"""The adapter that turns a gateway's answers into numbers the policy can use.

The cases worth having are the untidy ones: the `'N/A'` sentinel where a price
should be, a contract field that arrives as `0.0` because it was never sent, and
a code the classification call simply does not return. Each has to become a
refusal rather than a string or a zero reaching arithmetic.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.instruments import (
    InstrumentAdapter,
    InstrumentLookupError,
    normalize_quote,
)


def _snapshot(**overrides) -> dict:
    row = {
        "code": "US.AAPL",
        "last_price": 150.0,
        "bid_price": 149.0,
        "ask_price": 151.0,
        "lot_size": 1,
        "option_valid": False,
        "option_contract_size": 0.0,
        "option_contract_multiplier": "N/A",
    }
    row.update(overrides)
    return row


def _quote_ctx(snapshots: list[dict], classifications: dict[str, str]) -> MagicMock:
    """A quote context that answers both calls the adapter makes."""
    ctx = MagicMock()
    ctx.get_market_snapshot.return_value = (0, pd.DataFrame(snapshots))

    def basicinfo(market, stock_type, code_list):  # noqa: ARG001 - SDK signature
        rows = [
            {"code": code, "stock_type": stock_type}
            for code in code_list
            if classifications.get(code) == stock_type
        ]
        return 0, pd.DataFrame(rows or [], columns=pd.Index(["code", "stock_type"]))

    ctx.get_stock_basicinfo.side_effect = basicinfo
    return ctx


class TestNormalization:
    """Everything that is not a positive finite number becomes nothing."""

    @pytest.mark.parametrize("value", [150, 150.0, "150.0", " 150 "])
    def test_usable_numbers_survive(self, value):
        assert normalize_quote(value) == 150.0

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "N/A",
            "",
            "abc",
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            0.0,
            -1.0,
            True,
            [1],
        ],
    )
    def test_everything_else_becomes_none(self, value):
        assert normalize_quote(value) is None

    def test_zero_is_treated_as_absent_not_as_zero(self):
        """The SDK copies option_contract_size out of an optional proto field
        with no presence check, so "not sent" and "zero" arrive identically.

        Trusting the zero would value every option contract at nothing.
        """
        assert normalize_quote(0.0) is None


class TestAdapter:
    def test_an_equity_gets_a_multiplier_of_one(self):
        adapter = InstrumentAdapter(
            lambda: _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        )

        facts = adapter(["US.AAPL"])[0]

        assert facts.code == "US.AAPL"
        assert facts.classification == "STOCK"
        assert facts.monetary_multiplier == 1.0
        assert facts.last_price == 150.0
        assert facts.market_reference() == 151.0

    def test_an_etf_gets_a_multiplier_of_one(self):
        adapter = InstrumentAdapter(
            lambda: _quote_ctx([_snapshot(code="US.SPY")], {"US.SPY": "ETF"})
        )

        assert adapter(["US.SPY"])[0].monetary_multiplier == 1.0

    def test_na_prices_become_none_without_a_type_error(self):
        """The common case for an illiquid instrument."""
        adapter = InstrumentAdapter(
            lambda: _quote_ctx(
                [_snapshot(bid_price="N/A", ask_price="N/A")],
                {"US.AAPL": "STOCK"},
            )
        )

        facts = adapter(["US.AAPL"])[0]

        assert facts.bid_price is None
        assert facts.ask_price is None
        assert facts.last_price == 150.0
        assert facts.market_reference() == 150.0

    def test_a_code_missing_from_the_classification_is_refused(self):
        adapter = InstrumentAdapter(lambda: _quote_ctx([_snapshot()], {}))

        with pytest.raises(InstrumentLookupError, match="classification is unknown"):
            adapter(["US.AAPL"])

    def test_a_code_missing_from_the_snapshot_is_refused(self):
        adapter = InstrumentAdapter(lambda: _quote_ctx([], {"US.AAPL": "STOCK"}))

        with pytest.raises(InstrumentLookupError, match="did not return US.AAPL"):
            adapter(["US.AAPL"])

    def test_a_failing_snapshot_call_is_refused(self):
        ctx = _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        ctx.get_market_snapshot.return_value = (-1, "quote server unavailable")
        adapter = InstrumentAdapter(lambda: ctx)

        with pytest.raises(InstrumentLookupError, match="quote server unavailable"):
            adapter(["US.AAPL"])

    def test_a_failing_classification_call_is_refused(self):
        ctx = _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        ctx.get_stock_basicinfo.side_effect = None
        ctx.get_stock_basicinfo.return_value = (-1, "reference data unavailable")
        adapter = InstrumentAdapter(lambda: ctx)

        with pytest.raises(InstrumentLookupError, match="reference data unavailable"):
            adapter(["US.AAPL"])

    def test_no_quote_connection_is_refused(self):
        adapter = InstrumentAdapter(lambda: None)

        with pytest.raises(InstrumentLookupError, match="not available"):
            adapter(["US.AAPL"])

    def test_a_code_without_a_market_prefix_is_refused(self):
        adapter = InstrumentAdapter(lambda: _quote_ctx([_snapshot()], {}))

        with pytest.raises(InstrumentLookupError, match="no market prefix"):
            adapter(["AAPL"])

    def test_the_classification_is_requested_for_the_codes_being_valued(self):
        """An explicit code_list, not an enumeration of the whole venue."""
        ctx = _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        adapter = InstrumentAdapter(lambda: ctx)

        adapter(["US.AAPL"])

        for call in ctx.get_stock_basicinfo.call_args_list:
            assert call.kwargs["code_list"] == ["US.AAPL"]
            assert call.kwargs["market"] == "US"

    def test_an_option_is_refused_while_the_multiplier_is_unverified(self):
        """Which broker field carries an option's monetary multiplier has not
        been confirmed against an independent figure (task 1.1), so valuing one
        would mean guessing what a contract is worth.
        """
        adapter = InstrumentAdapter(
            lambda: _quote_ctx(
                [
                    _snapshot(
                        code="US.AAPL260116C300000",
                        option_valid=True,
                        option_contract_size=100.0,
                    )
                ],
                {"US.AAPL260116C300000": "DRVT"},
            )
        )

        with pytest.raises(InstrumentLookupError, match="has not been verified"):
            adapter(["US.AAPL260116C300000"])

    def test_an_unsupported_classification_carries_no_multiplier(self):
        """The policy refuses it; the adapter does not invent a multiplier."""
        ctx = _quote_ctx([_snapshot(code="US.FUT")], {})

        # The gateway reports a classification this server does not value.
        def unsupported(market, stock_type, code_list):  # noqa: ARG001 - SDK shape
            rows = [{"code": code, "stock_type": "FUTURE"} for code in code_list]
            return 0, pd.DataFrame(rows)

        ctx.get_stock_basicinfo.side_effect = unsupported
        adapter = InstrumentAdapter(lambda: ctx)

        facts = adapter(["US.FUT"])[0]

        assert facts.classification == "FUTURE"
        assert facts.monetary_multiplier is None

    def test_duplicate_codes_are_asked_for_once(self):
        ctx = _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        adapter = InstrumentAdapter(lambda: ctx)

        facts = adapter(["US.AAPL", "US.AAPL"])

        assert len(facts) == 1
        ctx.get_market_snapshot.assert_called_once_with(["US.AAPL"])

    def test_no_codes_is_refused(self):
        adapter = InstrumentAdapter(lambda: _quote_ctx([], {}))

        with pytest.raises(InstrumentLookupError, match="no instrument codes"):
            adapter([])
