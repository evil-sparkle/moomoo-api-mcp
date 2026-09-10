"""Discovery to preview, through actual MCP tool calls (R5, R4).

The point of these tests is the seam between tools: a contract symbol that
comes out of get_option_chain must be the symbol that reaches the SDK's
tradability query, with no re-derivation, re-formatting, or hand-built symbol
anywhere in between.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.conftest import call_mcp_tool

IMPACT_COLUMNS = [
    "nlv_change",
    "initial_margin_change",
    "maintenance_margin_change",
    "option_bp",
    "max_withdraw_change",
    "bp_decrease",
]


@pytest.fixture
def quote_ctx():
    ctx = MagicMock()
    ctx.get_option_expiration_date.return_value = (
        0,
        pd.DataFrame(
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
        ),
    )
    ctx.get_option_chain.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": "US.XYZ260116C100000",
                    "option_type": "CALL",
                    "strike_time": "2026-01-16",
                    "strike_price": 100.0,
                },
                {
                    "code": "US.XYZ260116C105000",
                    "option_type": "CALL",
                    "strike_time": "2026-01-16",
                    "strike_price": 105.0,
                },
            ]
        ),
    )
    ctx.get_stock_quote.return_value = (
        0,
        pd.DataFrame([{"code": "US.XYZ260116C100000", "last_price": 4.2}]),
    )
    ctx.subscribe.return_value = (0, None)
    return ctx


@pytest.fixture
def trade_ctx():
    ctx = MagicMock()
    ctx.comboorder_tradinginfo_query.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "nlv_change": -5.0,
                    "initial_margin_change": 500.0,
                    "maintenance_margin_change": 400.0,
                    "option_bp": 9000.0,
                    "max_withdraw_change": -500.0,
                    "bp_decrease": 500.0,
                }
            ],
            columns=IMPACT_COLUMNS,
        ),
    )
    return ctx


@pytest.fixture
def workflow_context(mcp_app_context, quote_ctx, trade_ctx):
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)
    # Read-only on purpose: the whole workflow below is reads and a preview.
    trade_service = TradeService(policy=TradingPolicy(TradingMode.READ_ONLY))
    trade_service.trade_ctx = trade_ctx
    mcp_app_context.trade_service = trade_service
    return mcp_app_context


@pytest.mark.asyncio
async def test_expirations_then_chain_then_preview(
    workflow_context, quote_ctx, trade_ctx
):
    # 1. Pick an expiry from the provider's own list.
    expirations = await call_mcp_tool(
        workflow_context, "get_option_expiration_date", {"code": "US.XYZ"}
    )
    expiry = expirations.json_blocks[0]["strike_time"]
    assert expiry == "2026-01-16"

    # 2. Fetch that one expiry's calls.
    chain = await call_mcp_tool(
        workflow_context,
        "get_option_chain",
        {"code": "US.XYZ", "start": expiry, "end": expiry, "option_type": "CALL"},
    )
    contracts = chain.structured["result"]
    assert [row["code"] for row in contracts] == [
        "US.XYZ260116C100000",
        "US.XYZ260116C105000",
    ]
    chain_kwargs = quote_ctx.get_option_chain.call_args.kwargs
    assert chain_kwargs["start"] == chain_kwargs["end"] == expiry
    assert chain_kwargs["option_type"] == "CALL"

    # 3. Preview a vertical spread built from those exact symbols.
    preview = await call_mcp_tool(
        workflow_context,
        "preview_combo_order",
        {
            "combo_legs": [
                {"code": contracts[0]["code"], "trd_side": "BUY", "qty_ratio": 1},
                {"code": contracts[1]["code"], "trd_side": "SELL", "qty_ratio": 1},
            ],
            "price": 2.0,
            "qty": 1,
            "trd_env": "REAL",
            "acc_id": "456",
        },
    )

    submitted = trade_ctx.comboorder_tradinginfo_query.call_args.kwargs[
        "combo_leg_list"
    ]
    assert [leg.code for leg in submitted] == [
        "US.XYZ260116C100000",
        "US.XYZ260116C105000",
    ]
    assert preview.structured["initial_margin_change"] == 500.0
    trade_ctx.place_combo_order.assert_not_called()


@pytest.mark.asyncio
async def test_selected_contract_symbol_reaches_a_quote_unchanged(
    workflow_context, quote_ctx
):
    chain = await call_mcp_tool(
        workflow_context, "get_option_chain", {"code": "US.XYZ"}
    )
    selected = chain.structured["result"][0]["code"]

    await call_mcp_tool(workflow_context, "get_stock_quote", {"codes": [selected]})

    assert quote_ctx.get_stock_quote.call_args.args[0] == ["US.XYZ260116C100000"]


@pytest.mark.asyncio
async def test_empty_chain_returns_an_empty_list_not_an_error(
    workflow_context, quote_ctx
):
    quote_ctx.get_option_chain.return_value = (0, pd.DataFrame([], columns=["code"]))

    result = await call_mcp_tool(
        workflow_context, "get_option_chain", {"code": "US.XYZ"}
    )

    assert result.structured["result"] == []


@pytest.mark.asyncio
async def test_provider_rejection_is_an_error_not_an_empty_chain(
    workflow_context, quote_ctx
):
    quote_ctx.get_option_chain.return_value = (-1, "no option quote permission")

    with pytest.raises(Exception, match="no option quote permission"):
        await call_mcp_tool(workflow_context, "get_option_chain", {"code": "US.XYZ"})


@pytest.mark.asyncio
async def test_reversed_dates_fail_before_the_gateway(workflow_context, quote_ctx):
    with pytest.raises(Exception, match="is after end"):
        await call_mcp_tool(
            workflow_context,
            "get_option_chain",
            {"code": "US.XYZ", "start": "2026-02-01", "end": "2026-01-16"},
        )

    quote_ctx.get_option_chain.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_option_type_fails_before_the_gateway(
    workflow_context, quote_ctx
):
    with pytest.raises(Exception, match="option_type must be one of"):
        await call_mcp_tool(
            workflow_context,
            "get_option_chain",
            {"code": "US.XYZ", "option_type": "BUTTERFLY"},
        )

    quote_ctx.get_option_chain.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_does_not_subscribe_or_trade(
    workflow_context, quote_ctx, trade_ctx
):
    await call_mcp_tool(
        workflow_context, "get_option_expiration_date", {"code": "US.XYZ"}
    )
    await call_mcp_tool(workflow_context, "get_option_chain", {"code": "US.XYZ"})

    quote_ctx.subscribe.assert_not_called()
    assert trade_ctx.method_calls == []
