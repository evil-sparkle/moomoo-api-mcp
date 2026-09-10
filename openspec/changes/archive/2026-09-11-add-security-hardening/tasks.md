# Tasks: Add Security Hardening

## 1. Container & Supply Chain Hardening

- [x] 1.1 Add SHA256 checksum verification to `Dockerfile.opend`
- [x] 1.2 Configure non-root user (`opend`, UID 10001) in `Dockerfile.opend`
- [x] 1.3 Configure non-root user (`appuser`, UID 10001) in `Dockerfile` and change editable install to standard install
- [x] 1.4 Update `docker-compose.yml` to support non-root volume permissions

## 2. Trading Policy Guardrails

- [x] 2.1 Add `max_order_qty` and `max_order_notional` configurations to `TradingPolicy`
- [x] 2.2 Add `check_order_limits` validation in `TradingPolicy`
- [x] 2.3 Integrate `check_order_limits` into `TradeService.place_order` and `TradeService.place_combo_order`
- [x] 2.4 Add unit tests for trading limit guardrails in `test_trading_policy.py`

## 3. Transport Security & FastMCP Auth

- [x] 3.1 Configure `TransportSecuritySettings` with DNS rebinding protection in `server.py`
- [x] 3.2 Add `MCP_AUTH_TOKEN` bearer authentication via `BearerAuthMiddleware` with constant-time check (`hmac.compare_digest`)
- [x] 3.3 Add unit tests for auth token verification and rejection in `test_server.py`

## 4. CI/CD & Developer Hygiene

- [x] 4.1 Add least-privilege `permissions: contents: read` in `.github/workflows/ci.yml`
- [x] 4.2 Create `.pre-commit-config.yaml` with Gitleaks hook
- [x] 4.3 Update `.env.example` documenting new security configurations
- [x] 4.4 Run test suite and lint checks to confirm all tests pass
