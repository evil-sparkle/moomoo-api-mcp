## 1. Implementation

- [x] 1.1 Update `src/moomoo_mcp/tools/account.py` tool signatures to accept `acc_id: str | int` (defaulting to strict string handling in logic or converting safely).
- [x] 1.2 Update `src/moomoo_mcp/tools/trading.py` tool signatures to accept `acc_id: str | int`.
- [x] 1.3 Update `src/moomoo_mcp/services/trade_service.py` to handle string `acc_id` inputs if strict typing prevents it.
- [x] 1.4 Update `tests/test_tools/test_account.py` to use string account IDs and add a specific test case for a large 64-bit integer ID.
- [x] 1.5 Update `tests/test_tools/test_trading.py` (if exists) or add tests to verify `trading` tools with string IDs.

## 2. Reconciliation

String `acc_id` **inputs** shipped with this change and are verified by
`tests/test_tools/test_account.py` (large-id `get_assets` case) and
`tests/test_tools/test_trading.py` (string-id forwarding through actual MCP
dispatch for every trading tool).

The **output** boundary — emitting `acc_id`, `position_id`, and `combo_id` as
decimal strings — is delivered by requirement R2 of
`complete-trading-workflows`, which owns the `account-info` delta for it. This
change is not superseded: it covers inputs, R2 covers outputs. Archive this
change before `complete-trading-workflows` so the `account-info` spec picks up
the input requirement first.
