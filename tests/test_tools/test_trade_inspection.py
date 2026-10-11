"""Inspect additive read tools through the actual MCP argument/output boundary."""

import pytest

import moomoo_mcp.tools.trade_inspection  # noqa: F401

ACCOUNT = "987654321098765431"


@pytest.mark.asyncio
async def test_capabilities_tool_preserves_account_id(call_tool, mock_trade_service):
    mock_trade_service.get_trading_capabilities.return_value = {
        "acc_id": int(ACCOUNT),
        "supported": {"placement": None},
        "ready": {"state": "UNKNOWN"},
    }
    result = await call_tool(
        "get_trading_capabilities",
        {"code": "US.AAPL", "trd_env": "REAL", "acc_id": ACCOUNT},
    )
    assert result.structured["acc_id"] == ACCOUNT
    assert result.structured["supported"]["placement"] is None
    assert (
        mock_trade_service.get_trading_capabilities.call_args.kwargs["acc_id"]
        == ACCOUNT
    )


@pytest.mark.asyncio
async def test_event_tool_preserves_nested_exact_identifiers(
    call_tool, mock_trade_service
):
    mock_trade_service.get_trade_events.return_value = {
        "acc_id": int(ACCOUNT),
        "events": [
            {
                "acc_id": int(ACCOUNT),
                "data": {"deal_id": int(ACCOUNT) + 1, "order_id": ACCOUNT},
            }
        ],
        "event_epoch": "event-process",
        "next_sequence": 3,
    }
    result = await call_tool(
        "get_trade_events",
        {
            "trd_env": "REAL",
            "acc_id": ACCOUNT,
            "after_sequence": 1,
            "event_epoch": "event-process",
            "limit": 2,
        },
    )
    assert result.structured["acc_id"] == ACCOUNT
    assert result.structured["events"][0]["data"]["deal_id"] == str(int(ACCOUNT) + 1)
    assert mock_trade_service.get_trade_events.call_args.kwargs == {
        "trd_env": "REAL",
        "acc_id": ACCOUNT,
        "after_sequence": 1,
        "event_epoch": "event-process",
        "limit": 2,
    }


@pytest.mark.asyncio
async def test_event_schema_rejects_boolean_integer_cursor(call_tool):
    with pytest.raises(Exception) as error:
        await call_tool("get_trade_events", {"trd_env": "REAL", "after_sequence": True})
    assert "after_sequence" in str(error.value)
