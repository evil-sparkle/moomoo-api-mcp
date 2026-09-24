"""Protocol-visible safety hints for every exported MCP tool."""

import pytest

from moomoo_mcp.server import mcp

READ_ONLY = {
    "check_health",
    "get_account_summary",
    "get_accounts",
    "get_assets",
    "get_cash_flow",
    "get_deals",
    "get_execution",
    "get_historical_klines",
    "get_historical_klines_page",
    "get_history_deals",
    "get_history_orders",
    "get_margin_ratio",
    "get_market_snapshot",
    "get_market_state",
    "get_max_tradable",
    "get_option_chain",
    "get_option_expiration_date",
    "get_orders",
    "get_positions",
    "get_subscriptions",
    "get_trading_days",
    "get_user_security",
    "get_user_security_group",
    "preview_combo_order",
}
MUTATING = {
    "acknowledge_recovery",
    "get_order_book",  # auto-subscribes before reading
    "get_stock_quote",  # auto-subscribes before reading
    "lock_trade",
    "reconcile_execution",
    "unsubscribe_market_data",
}
CONSEQUENTIAL = {
    "cancel_order",
    "modify_order",
    "place_combo_order",
    "place_order",
    "unlock_trade",
}


@pytest.mark.asyncio
async def test_every_tool_has_the_expected_annotations() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools) == READ_ONLY | MUTATING | CONSEQUENTIAL
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        annotations = tool.annotations
        assert annotations.openWorldHint is False, name
        if name in READ_ONLY:
            assert annotations.readOnlyHint is True, name
            assert annotations.destructiveHint is False, name
            assert annotations.idempotentHint is True, name
        elif name in MUTATING:
            assert annotations.readOnlyHint is False, name
            assert annotations.destructiveHint is False, name
            assert annotations.idempotentHint is False, name
        else:
            assert annotations.readOnlyHint is False, name
            assert annotations.destructiveHint is True, name
            assert annotations.idempotentHint is False, name
