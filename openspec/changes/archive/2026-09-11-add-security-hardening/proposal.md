# Change: Add Application Lifecycle Security Hardening

## Why

Operating an AI-integrated trading server in a public repository involves high-stakes risks across the application lifecycle:
1. **Supply Chain Risks**: Downloading external gateway binaries (`moomoo_OpenD`) without cryptographic checksum verification risks executing tampered code.
2. **Container Security**: Running containers as root (`UID 0`) risks host compromise if container isolation fails.
3. **Execution Guardrails**: In `REAL` or `SIMULATE` mode, LLM hallucinations could submit excessive order sizes or notional values without guardrails.
4. **Transport Security & Auth**: When exposed via SSE/HTTP, unauthenticated endpoints risk unauthorized tool execution by local processes or DNS rebinding.
5. **Developer & CI Hygiene**: Sensitive credentials must be blocked before reaching git, and dependencies must be scanned in CI.

## What Changes

- **Container Hardening**:
  - Add SHA256 checksum verification (`d38aad772b296f922e3b270119ca1abbadadc61cd3a45826e1b087a5b79069a5`) to `Dockerfile.opend`.
  - Run `moomoo-api-mcp` and `opend` containers as non-root users (`appuser` / `opend`).
  - Replace editable `-e .` install with standard package installation in production `Dockerfile`.
- **Trading Safety Guardrails**:
  - Add configurable `max_order_qty` (`MOOMOO_MAX_ORDER_QTY`) and `max_order_notional` (`MOOMOO_MAX_ORDER_NOTIONAL`) guardrails to `TradingPolicy`.
  - Enforce limits in `TradeService.place_order` and `TradeService.place_combo_order`.
- **API & Transport Security**:
  - Enable DNS rebinding protection via `TransportSecuritySettings`.
  - Add bearer token authentication via `MCP_AUTH_TOKEN` for FastMCP SSE endpoints.
- **CI/CD & Dev Hygiene**:
  - Add least-privilege `permissions: contents: read` to `.github/workflows/ci.yml`.
  - Add `.pre-commit-config.yaml` with Gitleaks secret detection.

## Impact

- Affected specs:
  - `container-deployment`: Checksum verification, non-root user execution.
  - `trading-policy`: Order quantity and notional value limit checks.
  - `configuration`: `MCP_AUTH_TOKEN`, `MOOMOO_MAX_ORDER_QTY`, `MOOMOO_MAX_ORDER_NOTIONAL`.
