"""Combo order legs must cross the MCP boundary as JSON, not as SDK objects.

``order_list_query``, ``history_order_list_query`` and ``place_combo_order`` all
return a ``combo_legs`` column holding ``ComboLeg`` instances. Those are plain
Python objects with no JSON representation, so a live account holding one spread
order made the whole tool fail — "Unable to serialize unknown type: ComboLeg" —
rather than degrading a single row. One combo order hid every ordinary order in
the list.

The legs also carry a ``position_id``, which is a 64-bit identifier. R2's
serializer walks dictionaries and lists; it could not see inside an opaque
object, so those identifiers were never converted either.
"""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import ComboLeg

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingMode, TradingPolicy
from tests.conftest import call_mcp_tool

ACCOUNT_ID = "987654321098765432"
# Above 2**53, so a double-precision client cannot represent it exactly.
LEG_POSITION_ID = 9111222333444555666


def _leg(code, trd_side, qty_ratio=1.0, position_id="N/A"):
    """Build a ComboLeg exactly as the SDK's ParseComboLegs does."""
    leg = ComboLeg()
    leg.code = code
    leg.trd_side = trd_side
    leg.qty_ratio = qty_ratio
    leg.position_id = position_id
    return leg


def _spread_row(**overrides):
    """One vertical-spread order row, shaped like the live gateway's."""
    row = {
        "code": "US.AAPL270115C290/300",
        "stock_name": "AAPL Vertical Spread",
        "order_id": "FS1D1CD1B864937000",
        "order_status": "WAITING_SUBMIT",
        "trd_side": "BUY",
        "qty": 1.0,
        "price": 5.0,
        "strategy_type": "SPREAD",
        "combo_legs": [
            _leg("US.AAPL270115C290000", "BUY"),
            _leg("US.AAPL270115C300000", "SELL_SHORT"),
        ],
    }
    row.update(overrides)
    return row


def _plain_row(**overrides):
    """An ordinary single-leg order, which carries an empty leg list."""
    row = {
        "code": "SG.G3B",
        "stock_name": "Amova Singapore STI ETF",
        "order_id": "FS1D1CE4F1AE3A1000",
        "order_status": "WAITING_SUBMIT",
        "trd_side": "BUY",
        "qty": 20.0,
        "price": 5.8,
        "strategy_type": "N/A",
        "combo_legs": [],
    }
    row.update(overrides)
    return row


@pytest.fixture
def trade_ctx_context(mcp_app_context):
    """A real TradeService on a mocked SDK, wired into the lifespan context."""
    trade_ctx = MagicMock()
    service = TradeService(policy=TradingPolicy(TradingMode.SIMULATE))
    service.trade_ctx = trade_ctx
    mcp_app_context.trade_service = service
    yield mcp_app_context, trade_ctx
    service.close()


def _roundtrip_through_ieee754_client(payload):
    """Simulate a client that parses every JSON number as a double."""
    return json.loads(json.dumps(payload), parse_int=float)


class TestOrderListWithACombo:
    """A spread order in today's list must not break the whole response."""

    @pytest.mark.asyncio
    async def test_a_spread_order_serializes(self, trade_ctx_context):
        context, trade_ctx = trade_ctx_context
        trade_ctx.order_list_query.return_value = (0, pd.DataFrame([_spread_row()]))

        result = await call_mcp_tool(
            context, "get_orders", {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"}
        )

        legs = result.json_blocks[0]["combo_legs"]
        assert [leg["code"] for leg in legs] == [
            "US.AAPL270115C290000",
            "US.AAPL270115C300000",
        ]
        assert [leg["trd_side"] for leg in legs] == ["BUY", "SELL_SHORT"]
        assert [leg["qty_ratio"] for leg in legs] == [1.0, 1.0]

    @pytest.mark.asyncio
    async def test_ordinary_orders_are_not_hidden_by_one_combo(
        self, trade_ctx_context
    ):
        """The whole tool failed before, so every other order vanished with it."""
        context, trade_ctx = trade_ctx_context
        trade_ctx.order_list_query.return_value = (
            0,
            pd.DataFrame([_spread_row(), _plain_row()]),
        )

        result = await call_mcp_tool(
            context, "get_orders", {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"}
        )

        codes = [row["code"] for row in result.json_blocks]
        assert codes == ["US.AAPL270115C290/300", "SG.G3B"]

    @pytest.mark.asyncio
    async def test_an_empty_leg_list_stays_empty(self, trade_ctx_context):
        context, trade_ctx = trade_ctx_context
        trade_ctx.order_list_query.return_value = (0, pd.DataFrame([_plain_row()]))

        result = await call_mcp_tool(
            context, "get_orders", {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"}
        )

        assert result.json_blocks[0]["combo_legs"] == []

    @pytest.mark.asyncio
    async def test_history_orders_serialize_legs(self, trade_ctx_context):
        context, trade_ctx = trade_ctx_context
        trade_ctx.history_order_list_query.return_value = (
            0,
            pd.DataFrame([_spread_row(order_status="FILLED_ALL")]),
        )

        result = await call_mcp_tool(
            context,
            "get_history_orders",
            {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"},
        )

        assert len(result.json_blocks[0]["combo_legs"]) == 2

    @pytest.mark.asyncio
    async def test_a_placed_combo_returns_its_legs(self, trade_ctx_context):
        """The write path returns the same column, so it failed the same way."""
        context, trade_ctx = trade_ctx_context
        trade_ctx.place_combo_order.return_value = (0, pd.DataFrame([_spread_row()]))

        result = await call_mcp_tool(
            context,
            "place_combo_order",
            {
                "combo_legs": [
                    {
                        "code": "US.AAPL270115C290000",
                        "trd_side": "BUY",
                        "qty_ratio": 1,
                    },
                    {
                        "code": "US.AAPL270115C300000",
                        "trd_side": "SELL",
                        "qty_ratio": 1,
                    },
                ],
                "price": 5.0,
                "qty": 1,
                "trd_env": "SIMULATE",
                "acc_id": ACCOUNT_ID,
            },
        )

        assert len(result.json["combo_legs"]) == 2


class TestLegIdentifierPrecision:
    """A leg's position_id is a 64-bit id and must leave as a string."""

    @pytest.mark.asyncio
    async def test_leg_position_id_survives_a_double_parsing_client(
        self, trade_ctx_context
    ):
        context, trade_ctx = trade_ctx_context
        closing = _spread_row(
            combo_legs=[
                _leg("US.AAPL270115C290000", "SELL", position_id=LEG_POSITION_ID)
            ]
        )
        trade_ctx.order_list_query.return_value = (0, pd.DataFrame([closing]))

        result = await call_mcp_tool(
            context, "get_orders", {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"}
        )
        received = _roundtrip_through_ieee754_client(result.json_blocks[0])

        assert received["combo_legs"][0]["position_id"] == str(LEG_POSITION_ID)

    @pytest.mark.asyncio
    async def test_an_unbound_leg_keeps_the_gateway_sentinel(self, trade_ctx_context):
        """An opening leg has no position, and 'N/A' is how the SDK says so."""
        context, trade_ctx = trade_ctx_context
        trade_ctx.order_list_query.return_value = (0, pd.DataFrame([_spread_row()]))

        result = await call_mcp_tool(
            context, "get_orders", {"acc_id": ACCOUNT_ID, "trd_env": "SIMULATE"}
        )

        assert result.json_blocks[0]["combo_legs"][0]["position_id"] == "N/A"
