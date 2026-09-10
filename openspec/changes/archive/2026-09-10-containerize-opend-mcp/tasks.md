# Tasks: Containerize OpenD and MCP Server with JIT Unlocking

## 1. Core Service Hardening (JIT & Explicit Lock)

- [x] 1.1 Add `lock_trade()` method to `TradeService` supporting `is_unlock=False`
- [x] 1.2 Implement `_jit_trade_unlock()` context manager in `TradeService`
- [x] 1.3 Wrap `place_order`, `place_combo_order`, `modify_order`, and `cancel_order` with `_jit_trade_unlock()`
- [x] 1.4 Add `lock_trade` tool in `src/moomoo_mcp/tools/account.py`
- [x] 1.5 Add proactive startup lock enforcement in `server.py` when `MOOMOO_TRADING_MODE=READ_ONLY`

## 2. Containerization Assets

- [x] 2.1 Create `Dockerfile` for `moomoo-api-mcp` using Python and `uv`
- [x] 2.2 Create `docker-compose.yml` with `opend` and `moomoo-mcp` services
- [x] 2.3 Configure isolated internal Docker bridge network without exposing port 11111 to host
- [x] 2.4 Create `.env.example` documenting `MOOMOO_TRADE_PASSWORD_MD5` and container environment variables
- [x] 2.5 Configure persistent volume for OpenD session data (`AppData.dat`)

## 3. Testing & Verification

- [x] 3.1 Unit test `lock_trade` and `_jit_trade_unlock` context manager
- [x] 3.2 Verify `_jit_trade_unlock` safely handles exceptions and always re-locks
- [x] 3.3 Verify JIT unlock with `MOOMOO_TRADING_MODE=READ_ONLY` blocks writes without unlocking
- [x] 3.4 Validate OpenSpec change with `openspec validate containerize-opend-mcp --strict --no-interactive`

