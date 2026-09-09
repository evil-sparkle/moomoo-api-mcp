"""Precision tests for 64-bit identifiers crossing the MCP boundary (R2)."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.tools.serialization import (
    IdentifierSerializationError,
    serialize_identifiers,
)

# Above 2**53, so a double-precision client cannot represent it exactly.
UNSAFE_ID = 9007199254740993
ACCOUNT_ID = 2222222222222222222
STRATEGY_ID = 3333333333333333333
LEG_ID = 4444444444444444444


def _roundtrip_through_ieee754_client(payload):
    """Simulate a client that parses every JSON number as a double.

    This is what a JavaScript-based MCP client does, and it is where the
    corruption happens — before any server-side input validation can see it.
    """
    return json.loads(json.dumps(payload), parse_int=float)


class TestIdentifierSerializer:
    """Field-aware conversion rules."""

    def test_ids_serialized_as_strings(self):
        rows = [
            {
                "code": "US.X",
                "acc_id": ACCOUNT_ID,
                "position_id": STRATEGY_ID,
                "combo_id": LEG_ID,
            }
        ]

        out = serialize_identifiers(rows)

        assert out[0]["acc_id"] == str(ACCOUNT_ID)
        assert out[0]["position_id"] == str(STRATEGY_ID)
        assert out[0]["combo_id"] == str(LEG_ID)

    def test_numpy_ints_from_pandas_are_handled(self):
        df = pd.DataFrame([{"position_id": STRATEGY_ID, "combo_id": LEG_ID}])
        rows = df.to_dict("records")

        out = serialize_identifiers(rows)

        assert out[0]["position_id"] == str(STRATEGY_ID)
        assert out[0]["combo_id"] == str(LEG_ID)

    def test_missing_ids_left_alone(self):
        rows = [{"code": "US.AAPL", "qty": 10}]

        assert serialize_identifiers(rows) == rows

    def test_none_ids_left_alone(self):
        rows = [{"code": "US.AAPL", "position_id": None}]

        assert serialize_identifiers(rows)[0]["position_id"] is None

    def test_existing_strings_pass_through(self):
        rows = [{"acc_id": "123", "position_id": str(STRATEGY_ID)}]

        assert serialize_identifiers(rows) == rows

    def test_sdk_missing_sentinel_passes_through(self):
        """The SDK reports an absent combo_id as the string 'N/A', not a number."""
        rows = [{"code": "US.A", "position_id": STRATEGY_ID, "combo_id": "N/A"}]

        out = serialize_identifiers(rows)

        assert out[0]["combo_id"] == "N/A"
        assert out[0]["position_id"] == str(STRATEGY_ID)

    def test_nan_is_treated_as_a_missing_identifier(self):
        """A pandas NaN gap is an absent identifier, not a corrupted one."""
        rows = [{"code": "US.B", "position_id": float("nan")}]

        assert serialize_identifiers(rows)[0]["position_id"] is None

    def test_float_identifier_is_refused(self):
        with pytest.raises(IdentifierSerializationError, match="position_id"):
            serialize_identifiers([{"position_id": 9007199254740992.0}])

    def test_boolean_identifier_is_refused(self):
        with pytest.raises(IdentifierSerializationError, match="boolean"):
            serialize_identifiers([{"acc_id": True}])

    def test_non_identifier_values_unchanged(self):
        row = {
            "acc_id": ACCOUNT_ID,
            "cash": 1234.56,
            "qty": 100,
            "can_sell_qty": 0,
            "code": "US.AAPL",
            "is_real": True,
        }

        out = serialize_identifiers(row)

        assert out["cash"] == 1234.56
        assert out["qty"] == 100
        assert out["can_sell_qty"] == 0
        assert out["is_real"] is True

    def test_nested_structures_are_traversed(self):
        payload = {
            "assets": {"acc_id": ACCOUNT_ID, "cash": 10.0},
            "positions": [{"position_id": STRATEGY_ID, "legs": [{"combo_id": LEG_ID}]}],
        }

        out = serialize_identifiers(payload)

        assert out["assets"]["acc_id"] == str(ACCOUNT_ID)
        assert out["positions"][0]["position_id"] == str(STRATEGY_ID)
        assert out["positions"][0]["legs"][0]["combo_id"] == str(LEG_ID)

    def test_source_is_not_mutated(self):
        rows = [{"position_id": STRATEGY_ID, "nested": {"acc_id": ACCOUNT_ID}}]

        serialize_identifiers(rows)

        assert rows[0]["position_id"] == STRATEGY_ID
        assert rows[0]["nested"]["acc_id"] == ACCOUNT_ID


class TestIeee754Roundtrip:
    """The corruption this guards against, demonstrated and then prevented."""

    def test_numeric_ids_are_corrupted_by_a_double_parsing_client(self):
        """Establish the bug exists: raw ints do not survive the roundtrip."""
        payload = [{"position_id": UNSAFE_ID}]

        received = _roundtrip_through_ieee754_client(payload)

        assert int(received[0]["position_id"]) != UNSAFE_ID
        assert int(received[0]["position_id"]) == 9007199254740992

    def test_string_ids_survive_the_roundtrip(self):
        """With the fix applied, the identifier arrives intact."""
        payload = serialize_identifiers([{"position_id": UNSAFE_ID}])

        received = _roundtrip_through_ieee754_client(payload)

        assert received[0]["position_id"] == str(UNSAFE_ID)


class TestAccountToolsThroughMcp:
    """Every covered tool must serialize, not just the helper.

    These dispatch by tool name through the real FastMCP server, so a tool that
    forgot to call the serializer fails here even though the helper is correct.
    """

    @pytest.mark.asyncio
    async def test_get_accounts_emits_string_ids(self, call_tool, mock_trade_service):
        mock_trade_service.get_accounts.return_value = [
            {"acc_id": UNSAFE_ID, "trd_env": "REAL", "cash": 1000.5}
        ]

        result = await call_tool("get_accounts")

        assert result.json_blocks[0]["acc_id"] == str(UNSAFE_ID)
        assert result.structured["result"][0]["acc_id"] == str(UNSAFE_ID)
        assert result.structured["result"][0]["cash"] == 1000.5

    @pytest.mark.asyncio
    async def test_get_assets_emits_string_ids(self, call_tool, mock_trade_service):
        mock_trade_service.get_assets.return_value = {
            "acc_id": ACCOUNT_ID,
            "cash": 12345.67,
            "total_assets": 98765.43,
        }

        result = await call_tool("get_assets", {"acc_id": str(ACCOUNT_ID)})

        assert result.json["acc_id"] == str(ACCOUNT_ID)
        assert result.structured["acc_id"] == str(ACCOUNT_ID)
        assert result.structured["cash"] == 12345.67

    @pytest.mark.asyncio
    async def test_get_positions_emits_string_ids(self, call_tool, mock_trade_service):
        mock_trade_service.get_positions.return_value = [
            {
                "code": "US.XYZ260101C100000",
                "position_id": UNSAFE_ID,
                "combo_id": STRATEGY_ID,
                "qty": 2,
            }
        ]

        result = await call_tool(
            "get_positions", {"show_option_strategy_view": True}
        )

        row = result.structured["result"][0]
        assert row["position_id"] == str(UNSAFE_ID)
        assert row["combo_id"] == str(STRATEGY_ID)
        assert row["qty"] == 2
        assert result.json_blocks[0]["position_id"] == str(UNSAFE_ID)

    @pytest.mark.asyncio
    async def test_account_summary_covers_nested_positions(
        self, call_tool, mock_trade_service
    ):
        mock_trade_service.get_assets.return_value = {
            "acc_id": ACCOUNT_ID,
            "cash": 500.0,
        }
        mock_trade_service.get_positions.return_value = [
            {"position_id": STRATEGY_ID, "combo_id": LEG_ID, "qty": 1},
            {"position_id": None, "qty": 3},
        ]

        result = await call_tool("get_account_summary", {"trd_env": "SIMULATE"})

        payload = result.structured
        assert payload["assets"]["acc_id"] == str(ACCOUNT_ID)
        assert payload["assets"]["cash"] == 500.0
        assert payload["positions"][0]["position_id"] == str(STRATEGY_ID)
        assert payload["positions"][0]["combo_id"] == str(LEG_ID)
        assert payload["positions"][1]["position_id"] is None
        assert payload["positions"][1]["qty"] == 3

    @pytest.mark.asyncio
    async def test_service_records_are_not_mutated_by_the_tool(
        self, call_tool, mock_trade_service
    ):
        rows = [{"position_id": STRATEGY_ID}]
        mock_trade_service.get_positions.return_value = rows

        await call_tool("get_positions")

        assert rows[0]["position_id"] == STRATEGY_ID

    @pytest.mark.asyncio
    async def test_lossy_identifier_fails_the_tool_call(
        self, call_tool, mock_trade_service
    ):
        mock_trade_service.get_positions.return_value = [
            {"position_id": 9007199254740992.0}
        ]

        with pytest.raises(Exception, match="position_id"):
            await call_tool("get_positions")


class TestRetrievalToRequestRoundtrip:
    """End to end: what the tools emit must be usable unchanged."""

    @pytest.mark.asyncio
    async def test_account_id_survives_to_a_follow_up_request(
        self, call_tool, mock_trade_service
    ):
        mock_trade_service.get_accounts.return_value = [
            {"acc_id": UNSAFE_ID, "trd_env": "SIMULATE"}
        ]

        listed = await call_tool("get_accounts")
        wire = _roundtrip_through_ieee754_client(listed.structured["result"])

        mock_trade_service.get_assets.return_value = {"acc_id": UNSAFE_ID, "cash": 1.0}
        await call_tool(
            "get_assets", {"trd_env": "SIMULATE", "acc_id": wire[0]["acc_id"]}
        )

        forwarded = mock_trade_service.get_assets.call_args.kwargs["acc_id"]
        assert forwarded == str(UNSAFE_ID)
        assert int(forwarded) == UNSAFE_ID

    def test_retrieved_ids_submit_without_precision_loss(self):
        ctx = MagicMock()
        service = TradeService()
        service.trade_ctx = ctx

        # 1. Retrieval, as the strategy view returns it.
        ctx.position_list_query.return_value = (
            0,
            pd.DataFrame(
                [
                    {
                        "code": "US.XYZ260101C100000",
                        "position_id": UNSAFE_ID,
                        "combo_id": STRATEGY_ID,
                        "position_type": "LEG",
                    },
                    {
                        "code": "US.XYZ260101C105000",
                        "position_id": LEG_ID,
                        "combo_id": STRATEGY_ID,
                        "position_type": "LEG",
                    },
                ]
            ),
        )
        rows = service.get_positions(
            trd_env="SIMULATE", acc_id=123, show_option_strategy_view=True
        )

        # 2. Across the MCP boundary, into a double-parsing client and back.
        wire = _roundtrip_through_ieee754_client(serialize_identifiers(rows))

        # 3. Straight back into a closing combo order.
        ctx.place_combo_order.return_value = (
            0,
            pd.DataFrame([{"order_id": "1", "order_status": "SUBMITTED"}]),
        )
        service.place_combo_order(
            combo_legs=[
                {
                    "code": wire[0]["code"],
                    "trd_side": "SELL",
                    "qty_ratio": 1,
                    "position_id": wire[0]["position_id"],
                },
                {
                    "code": wire[1]["code"],
                    "trd_side": "BUY",
                    "qty_ratio": 1,
                    "position_id": wire[1]["position_id"],
                },
            ],
            price=2.5,
            qty=1,
            trd_env="SIMULATE",
            acc_id=123,
        )

        submitted = ctx.place_combo_order.call_args.kwargs["combo_leg_list"]
        assert [leg.position_id for leg in submitted] == [UNSAFE_ID, LEG_ID]

    def test_roundtrip_would_fail_without_stringification(self):
        """Guard the guard: skipping the fix corrupts the submitted id."""
        wire = _roundtrip_through_ieee754_client([{"position_id": UNSAFE_ID}])

        ctx = MagicMock()
        service = TradeService()
        service.trade_ctx = ctx

        # A double-parsed id arrives as a float, which is now refused outright
        # rather than silently truncated to the wrong position.
        with pytest.raises(ValueError, match="non-integer 'position_id'"):
            service.place_combo_order(
                combo_legs=[
                    {
                        "code": "US.A",
                        "trd_side": "SELL",
                        "qty_ratio": 1,
                        "position_id": wire[0]["position_id"],
                    },
                    {"code": "US.B", "trd_side": "BUY", "qty_ratio": 1},
                ],
                price=2.5,
                qty=1,
                acc_id=123,
            )
