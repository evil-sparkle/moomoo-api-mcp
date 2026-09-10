# Tasks: Containerize OpenD and MCP Server with JIT Unlocking

## 1. Core Service Hardening (JIT & Explicit Lock)

- [ ] 1.1 Add `lock_trade()` method to `TradeService` supporting `is_unlock=False`
- [ ] 1.2 Implement `_jit_trade_unlock()` context manager in `TradeService`
- [ ] 1.3 Wrap `place_order`, `place_combo_order`, `modify_order`, and `cancel_order` with `_jit_trade_unlock()`
- [ ] 1.4 Add `lock_trade` tool in `src/moomoo_mcp/tools/account.py`
- [ ] 1.5 Add proactive startup lock enforcement in `server.py` when `MOOMOO_TRADING_MODE=READ_ONLY`

## 2. Containerization Assets

- [ ] 2.1 Create `Dockerfile` for `moomoo-api-mcp` using Python and `uv`
- [ ] 2.2 Create `docker-compose.yml` with `opend` and `moomoo-mcp` services
- [ ] 2.3 Configure isolated internal Docker bridge network without exposing port 11111 to host
- [ ] 2.4 Create `.env.example` documenting `MOOMOO_TRADE_PASSWORD_MD5` and container environment variables
- [ ] 2.5 Configure persistent volume for OpenD session data (`AppData.dat`)

## 3. Testing & Verification

- [ ] 3.1 Unit test `lock_trade` and `_jit_trade_unlock` context manager
- [ ] 3.2 Verify `_jit_trade_unlock` safely handles exceptions and always re-locks
- [ ] 3.3 Verify JIT unlock with `MOOMOO_TRADING_MODE=READ_ONLY` blocks writes without unlocking
- [ ] 3.4 Validate OpenSpec change with `openspec validate containerize-opend-mcp --strict --no-interactive`
