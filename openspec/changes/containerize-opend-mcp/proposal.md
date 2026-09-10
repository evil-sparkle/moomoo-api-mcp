# Change: Containerize OpenD and MCP Server with Docker Compose & JIT Unlocking

## Why

In the current deployment, OpenD and the AI agent run concurrently in host terminal/tmux sessions. Once unlocked for trading via `unlock.py`, OpenD remains permanently unlocked in memory on loopback port `11111`, creating an ambient authority vulnerability where any local process or autonomous agent tool can submit real orders indefinitely without human intervention. Furthermore, storing credentials on the host exposes them to local inspection.

Containerizing OpenD and `moomoo-api-mcp` with Docker Compose isolates the gateway network, conceals port `11111` from the host OS, and enables an ephemeral Just-In-Time (JIT) unlock-and-relock pattern that keeps OpenD locked by default.

## What Changes

- **Docker Compose Topology**: Package OpenD and `moomoo-api-mcp` into an isolated Docker network where port `11111` is internal-only and inaccessible from the host.
- **Credential Isolation (Tier 1 & Tier 2)**: Inject `MOOMOO_TRADE_PASSWORD_MD5` only into the MCP container; the host agent retains zero credentials and connects strictly via MCP (SSE/HTTP or stdio).
- **Ephemeral JIT Trade Unlock**: Modify order mutation tools in `TradeService` (`place_order`, `place_combo_order`, `modify_order`, `cancel_order`) to unlock OpenD momentarily for packet transmission (~50ms) and immediately re-lock in a `finally` block.
- **Explicit Lock Tool & Proactive Startup Lock**: Add a `lock_trade` tool and ensure that whenever the MCP server initializes in `READ_ONLY` mode, it asserts an explicit lock (`is_unlock=False`) on OpenD.
- **Persistent OpenD Session Volume**: Mount `AppData.dat` and device authorization state to a Docker volume so interactive SMS 2FA is performed once and survives container restarts.

## Impact

- Affected specs: `trade-unlock`, `container-deployment` (new capability)
- Affected code:
  - `docker-compose.yml` (new)
  - `Dockerfile` (new)
  - `src/moomoo_mcp/services/trade_service.py` (JIT unlock context manager, lock implementation)
  - `src/moomoo_mcp/tools/account.py` (add `lock_trade` tool)
  - `src/moomoo_mcp/server.py` (lock-on-connect hook for read-only mode)
