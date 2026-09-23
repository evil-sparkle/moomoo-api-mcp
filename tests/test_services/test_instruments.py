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
from moomoo_mcp.services.trading_policy import (
    LegFacts,
    OrderFacts,
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
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
        # Mirrors the real packer: with a non-empty code_list the SDK sets
        # market=0 and secType=0, so stock_type is ignored and every requested
        # code comes back once, carrying its own classification.
        rows = [
            {"code": code, "stock_type": classifications[code]}
            for code in code_list
            if code in classifications
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

    def test_the_classification_costs_one_call_per_market(self):
        """It used to ask once per candidate type.

        With a non-empty code_list the SDK ignores market and stock_type and
        lets the security list drive the query, so those were three identical
        requests — three times the latency on the order path, and three times
        the chance of a fail-closed refusal.
        """
        ctx = _quote_ctx([_snapshot()], {"US.AAPL": "STOCK"})
        adapter = InstrumentAdapter(lambda: ctx)

        adapter(["US.AAPL"])

        assert ctx.get_stock_basicinfo.call_count == 1

    def test_codes_are_grouped_into_one_call_per_market(self):
        ctx = _quote_ctx(
            [_snapshot(), _snapshot(code="US.SPY"), _snapshot(code="HK.00700")],
            {"US.AAPL": "STOCK", "US.SPY": "ETF", "HK.00700": "STOCK"},
        )
        adapter = InstrumentAdapter(lambda: ctx)

        adapter(["US.AAPL", "US.SPY", "HK.00700"])

        assert ctx.get_stock_basicinfo.call_count == 2
        markets = {
            call.kwargs["market"] for call in ctx.get_stock_basicinfo.call_args_list
        }
        assert markets == {"US", "HK"}

    def test_one_call_still_classifies_mixed_types(self):
        """An equity and an ETF in the same request keep their own types."""
        ctx = _quote_ctx(
            [_snapshot(), _snapshot(code="US.SPY")],
            {"US.AAPL": "STOCK", "US.SPY": "ETF"},
        )
        adapter = InstrumentAdapter(lambda: ctx)

        facts = {
            item.code: item.classification for item in adapter(["US.AAPL", "US.SPY"])
        }

        assert facts == {"US.AAPL": "STOCK", "US.SPY": "ETF"}

    @pytest.mark.parametrize("multiplier", [10.0, 100.0, "150"])
    def test_option_uses_reported_multiplier_separately_from_size(self, multiplier):
        code = "US.AAPL260116C300000"
        ctx = _quote_ctx(
            [
                _snapshot(
                    code=code,
                    option_contract_size=25.0,
                    option_contract_multiplier=multiplier,
                )
            ],
            {code: "DRVT"},
        )
        facts = InstrumentAdapter(lambda: ctx)([code])[0]

        assert facts.monetary_multiplier == float(multiplier)
        assert facts.contract_size == 25.0

    @pytest.mark.parametrize(
        "multiplier",
        [
            None,
            "N/A",
            "",
            "bad",
            0,
            -1,
            True,
            float("nan"),
            float("inf"),
            float("-inf"),
        ],
    )
    def test_option_refuses_invalid_multiplier_without_size_fallback(self, multiplier):
        code = "US.AAPL260116C300000"
        ctx = _quote_ctx(
            [
                _snapshot(
                    code=code,
                    option_contract_size=100.0,
                    option_contract_multiplier=multiplier,
                )
            ],
            {code: "DRVT"},
        )
        with pytest.raises(InstrumentLookupError) as error:
            InstrumentAdapter(lambda: ctx)([code])
        assert "no usable option_contract_multiplier" in str(error.value)

    def test_option_refuses_absent_multiplier_without_size_fallback(self):
        code = "US.AAPL260116C300000"
        snapshot = _snapshot(code=code, option_contract_size=100.0)
        del snapshot["option_contract_multiplier"]
        ctx = _quote_ctx([snapshot], {code: "DRVT"})
        with pytest.raises(InstrumentLookupError) as error:
            InstrumentAdapter(lambda: ctx)([code])
        assert "no usable option_contract_multiplier" in str(error.value)

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


@pytest.mark.parametrize("is_combo", [False, True])
def test_adapter_to_policy_values_premium_using_multiplier(is_combo):
    codes = (
        ["US.AAPL260116C300000", "US.AAPL260116C310000"]
        if is_combo
        else ["US.AAPL260116C300000"]
    )
    ctx = _quote_ctx(
        [
            _snapshot(
                code=code, option_contract_size=10, option_contract_multiplier=100
            )
            for code in codes
        ],
        dict.fromkeys(codes, "DRVT"),
    )
    facts = InstrumentAdapter(lambda: ctx)(codes)
    order = OrderFacts(
        order_type="NORMAL",
        trd_side="BUY",
        qty=5,
        price=3,
        legs=[LegFacts(instrument=fact) for fact in facts],
        is_combo=is_combo,
    )
    policy = TradingPolicy(TradingMode.SIMULATE, max_order_notional={"USD": 1000})
    with pytest.raises(TradingPolicyError) as error:
        policy.assess_order("place_order", order)
    assert "1,500.00 USD" in str(error.value)

    # At the exact cap the same broker facts pass; options are no longer
    # unconditionally refused merely because multiplier selection was disabled.
    TradingPolicy(TradingMode.SIMULATE, max_order_notional={"USD": 1500}).assess_order(
        "place_order", order
    )
