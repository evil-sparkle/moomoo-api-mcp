# Design: Containerize OpenD & MCP Server with JIT Unlocking

## Context

The system runs an AI agent (Gemini CLI / Claude Code / Antigravity) that interacts with Moomoo OpenD via `moomoo-api-mcp`. Previously, OpenD ran directly on the host in a tmux session listening on `127.0.0.1:11111`. When unlocked via `unlock.py`, OpenD remained permanently unlocked in memory, leaving an ambient execution window for any host process or autonomous tool call.

This design introduces a two-container Docker Compose architecture and a Just-In-Time (JIT) unlock-relock lifecycle.

## Goals / Non-Goals

### Goals
- **Network Confinement**: Conceal OpenD port `11111` entirely inside a Docker internal bridge network.
- **Credential Compartmentalization**: Host agent environment contains no trade credentials or MD5 hashes.
- **Zero-Ambient Unlock (JIT)**: OpenD rests in a locked state 99.9% of the time, unlocking only for ~50ms during active order packet transmission.
- **State Persistence**: Preserve device authorization and login tokens across container restarts.

### Non-Goals
- Running the AI agent itself inside Docker (the AI agent remains on the host or in its native CLI environment).
- Modifying Moomoo OpenD's internal binary or proprietary protocols.

## Decisions

### Decision 1: Two-Tier Container Architecture
We define two services in `docker-compose.yml`:
1. `opend`: Runs the native Linux OpenD daemon on an internal Docker network. Does not publish port `11111` to the host.
2. `moomoo-mcp`: Builds and runs the FastMCP Python server. Exposes FastMCP over HTTP/SSE on `127.0.0.1:8000` (or runs via Docker stdio exec).

*Alternative considered*: Single container running supervisord. Rejected because it couples daemon maintenance with MCP code and obscures per-service logs and health probes.

### Decision 2: Ephemeral Just-In-Time (JIT) Unlock Context Manager
Inside `TradeService`:
```python
@contextmanager
def _jit_trade_unlock(self):
    if self.policy.mode is not TradingMode.REAL:
        yield
        return

    self.unlock_trade(password_md5=self._password_md5, is_unlock=True)
    try:
        yield
    finally:
        try:
            self.unlock_trade(is_unlock=False)
        except Exception as exc:
            logger.error(f"Critical: failed to re-lock OpenD: {exc}")
```
Every order write (`place_order`, `place_combo_order`, `modify_order`, `cancel_order`) executes wrapped in this context manager.

### Decision 3: Initial Login & 2FA Flow
OpenD requires an interactive verification code on initial device setup.
- Initial setup: `docker compose run --rm -it opend` allows the operator to respond to the SMS prompt in the terminal.
- Device state is written to a named Docker volume (`opend-data` mounted at `/root/.moomooOpenD` and `/app/AppData.dat`).
- Subsequent boots run headless with `docker compose up -d`.

## Risks / Trade-offs

| Risk | Mitigation |
| :--- | :--- |
| Gateway rate limits on repeated unlocks | `unlock_trade` is called once per atomic operation; multi-leg orders use `place_combo_order` which executes in a single request. |
| Order cancellation fails if OpenD is locked | Wrap `cancel_order` and `modify_order` with the same JIT context manager. |
| Re-lock failure on socket drop | Defensive `finally:` block with error logging and fail-closed state. |

## Migration Plan

1. Verify OpenD data directory and AppData.dat location.
2. Create `Dockerfile` and `docker-compose.yml` in project root.
3. Add `lock_trade` tool and JIT context manager to `moomoo_mcp`.
4. Run validation and integration tests.
5. Update agent config (`mcp_config.json`) to point to the containerized MCP endpoint.
