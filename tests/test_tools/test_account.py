"""Tests for account tools through MCP dispatch and LLM guidance verification."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.tools.account import (
    get_account_summary,
    get_accounts,
    get_assets,
    get_cash_flow,
    get_max_tradable,
    get_positions,
    unlock_trade,
)


@pytest.fixture
def mock_trade_service():
    """Create a mock TradeService."""
    return MagicMock(spec=TradeService)


@pytest.mark.asyncio
async def test_get_margin_ratio(call_tool, mock_trade_service):
    """Test get_margin_ratio tool through MCP dispatch."""
    mock_trade_service.get_margin_ratio.return_value = [
        {"code": "US.AAPL", "im_factor": 0.25}
    ]

    result = await call_tool("get_margin_ratio", {"code_list": ["US.AAPL"]})

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["im_factor"] == 0.25


@pytest.mark.asyncio
async def test_get_cash_flow(call_tool, mock_trade_service):
    """Test get_cash_flow tool through MCP dispatch."""
    mock_trade_service.get_cash_flow.return_value = [
        {"trade_date": "2025-01-01", "amount": 1000.0}
    ]

    result = await call_tool("get_cash_flow", {"clearing_date": "2025-01-01"})

    assert len(result.structured["result"]) == 1
    assert result.structured["result"][0]["amount"] == 1000.0


@pytest.mark.asyncio
async def test_unlock_trade_explicit_password(call_tool, mock_trade_service):
    """Test unlock_trade tool forwards explicit password through MCP dispatch."""
    result = await call_tool("unlock_trade", {"password": "testpass"})

    payload = result.structured or result.json
    assert payload["status"] == "unlocked"
    mock_trade_service.unlock_trade.assert_called_once_with(
        password="testpass", password_md5=None
    )


@pytest.mark.asyncio
async def test_unlock_trade_has_no_environment_fallback(call_tool, mock_trade_service):
    """The environment fallback is gone.

    A stored credential now makes manual unlocking refused outright, so a
    fallback to that same credential has nothing left to fall back to. The tool
    forwards exactly what the caller supplied, and the service decides.
    """
    with patch.dict(os.environ, {"MOOMOO_TRADE_PASSWORD": "env_password"}, clear=True):
        await call_tool("unlock_trade")

    mock_trade_service.unlock_trade.assert_called_once_with(
        password=None, password_md5=None
    )


@pytest.mark.asyncio
async def test_unlock_trade_forwards_the_supplied_password(
    call_tool, mock_trade_service
):
    await call_tool("unlock_trade", {"password": "supplied"})

    mock_trade_service.unlock_trade.assert_called_once_with(
        password="supplied", password_md5=None
    )


@pytest.mark.asyncio
async def test_unlock_trade_result_states_that_the_unlock_persists(call_tool):
    """Nothing re-locks on this path, so the caller has to know that."""
    result = await call_tool("unlock_trade", {"password": "supplied"})

    payload = result.structured or result.json
    assert payload["status"] == "unlocked"
    assert "PERSISTS" in payload["message"]


@pytest.mark.asyncio
async def test_lock_trade_reports_the_halt_state(call_tool, mock_trade_service):
    mock_trade_service.lock_trade.return_value = {
        "status": "locked",
        "execution_halted": False,
        "halt_cleared": True,
    }

    result = await call_tool("lock_trade")

    payload = result.structured or result.json
    assert payload["status"] == "locked"
    assert payload["execution_halted"] is False
    assert payload["halt_cleared"] is True


class TestGuidanceMatchesPolicy:
    """Tool descriptions must not tell an agent to do something policy denies.

    READ_ONLY is the default trading mode and it denies unlock while permitting
    reads. Instructing an agent to unlock before reading therefore manufactures
    a policy failure for a read that would have worked.
    """

    READ_TOOLS = [
        get_accounts,
        get_account_summary,
        get_assets,
        get_positions,
        get_max_tradable,
        get_cash_flow,
    ]

    @pytest.mark.parametrize("tool", READ_TOOLS, ids=lambda t: t.__name__)
    def test_no_read_tool_mandates_unlocking_first(self, tool):
        doc = " ".join((tool.__doc__ or "").split()).lower()

        assert "you must call unlock_trade first" not in doc
        assert "must first call unlock_trade" not in doc
        assert "requires unlock_trade first" not in doc

    @pytest.mark.parametrize("tool", READ_TOOLS, ids=lambda t: t.__name__)
    def test_read_tools_that_mention_unlocking_qualify_it(self, tool):
        doc = " ".join((tool.__doc__ or "").split())

        if "unlock_trade" not in doc:
            return
        assert "may require unlock_trade" in doc or "unlock_trade for why" in doc

    def test_unlock_tool_warns_against_calling_it_pre_emptively(self):
        doc = " ".join((unlock_trade.__doc__ or "").split())

        assert "DO NOT call this pre-emptively" in doc
        assert "READ_ONLY" in doc

    @pytest.mark.asyncio
    async def test_a_real_read_needs_no_unlock_in_read_only_mode(
        self, call_tool, mock_trade_service
    ):
        """The behaviour the guidance now describes: read first, no unlock."""
        mock_trade_service.get_positions.return_value = [{"code": "US.AAPL", "qty": 1}]

        result = await call_tool("get_positions", {"trd_env": "REAL"})

        assert result.structured["result"][0]["qty"] == 1
        mock_trade_service.unlock_trade.assert_not_called()
