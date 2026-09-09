"""Precision tests for 64-bit position identifiers crossing the JSON boundary."""

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.tools.account import _stringify_large_ids

# Above 2**53, so a double-precision client cannot represent it exactly.
UNSAFE_ID = 9007199254740993
STRATEGY_ID = 3333333333333333333
LEG_ID = 4444444444444444444


def _roundtrip_through_ieee754_client(payload: list[dict]) -> list[dict]:
    """Simulate a client that parses every JSON number as a double.

    This is what a JavaScript-based MCP client does, and it is where the
    corruption happens — before any server-side input validation can see it.
    """
    return json.loads(json.dumps(payload), parse_int=float)


class TestLargeIdSerialization:
    """Identifiers must leave the tool boundary as strings."""

    def test_ids_serialized_as_strings(self):
        rows = [{"code": "US.X", "position_id": STRATEGY_ID, "combo_id": STRATEGY_ID}]

        out = _stringify_large_ids(rows)

        assert out[0]["position_id"] == str(STRATEGY_ID)
        assert out[0]["combo_id"] == str(STRATEGY_ID)

    def test_numpy_ints_from_pandas_are_handled(self):
        df = pd.DataFrame([{"position_id": STRATEGY_ID, "combo_id": LEG_ID}])
        rows = df.to_dict("records")

        out = _stringify_large_ids(rows)

        assert out[0]["position_id"] == str(STRATEGY_ID)
        assert out[0]["combo_id"] == str(LEG_ID)

    def test_missing_ids_left_alone(self):
        rows = [{"code": "US.AAPL", "qty": 10}]

        assert _stringify_large_ids(rows) == rows

    def test_none_ids_left_alone(self):
        rows = [{"code": "US.AAPL", "position_id": None}]

        assert _stringify_large_ids(rows)[0]["position_id"] is None

    def test_input_rows_not_mutated(self):
        rows = [{"position_id": STRATEGY_ID}]

        _stringify_large_ids(rows)

        assert rows[0]["position_id"] == STRATEGY_ID


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
        payload = _stringify_large_ids([{"position_id": UNSAFE_ID}])

        received = _roundtrip_through_ieee754_client(payload)

        assert received[0]["position_id"] == str(UNSAFE_ID)


class TestRetrievalToSubmissionRoundtrip:
    """End to end: what get_positions emits must submit unchanged."""

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
        wire = _roundtrip_through_ieee754_client(_stringify_large_ids(rows))

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
