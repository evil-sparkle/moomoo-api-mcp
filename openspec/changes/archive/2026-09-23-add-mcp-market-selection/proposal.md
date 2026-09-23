## Why

The server currently inherits the SDK's HK trade-context filter, hiding US paper
accounts from MCP even when the same OpenD login can access them. One MCP/OpenD
container should discover accounts across markets and target operations explicitly,
without requiring a container or connection pool for each market.

## What Changes

- Add `MOOMOO_TRADING_MARKET`, passed explicitly to the shared securities trade
  context as `filter_trdmarket`. Support `NONE` for all-market discovery and named
  securities-market filters.
- **BREAKING:** default to `NONE` instead of the SDK's implicit `HK`. Operators can
  set `HK` to retain the former discovery scope. The filter does not grant trading
  permission or restrict what a multi-market account is authorized to trade.
- Add optional `market` and `trd_env` filters to `get_accounts`; filter each response
  without changing the shared context or remembering a caller's last selection.
- **BREAKING:** account-bound reads that currently delegate `acc_id="0"` to the SDK must resolve exactly one
  account in the requested environment, or fail before the account-specific query.
  They must not rely on the SDK choosing its first account after discovery expands.
  Explicit account IDs remain the recommended way to target reads and trades.
- Combo preview retains its existing placement-style, market-aware account
  resolution; it already refuses ambiguous default-account selection.
- Preserve Stage 1 mutation account resolution, REAL allowlists, limits, explicit
  mutation environments, lock behavior, dispatch classifications and no replay.
- Report the configured filter in health and document all-market discovery,
  migration, and the difference between broker region, market and environment.

## Capabilities

### New Capabilities

None. Extend the existing configuration, account and health capabilities.

### Modified Capabilities

- `configuration`: validated trade-context market selection and all-market default.
- `account-info`: stateless discovery filters and unambiguous account-bound reads.
- `system-health`: report the configured trade-context filter independently of
  gateway availability and trading readiness.

## Impact

- `settings.py`, `server.py`, `services/trade_service.py`,
  `services/base_service.py`, account and trading tool descriptions, and health output.
- `docker-compose.yml`, README, deployment documentation, and the configuration
  example template during implementation; no private configuration is read or edited.
- Settings, service, MCP dispatch, account selection, health, connection lifecycle,
  and Compose tests. No dependency or OpenD version change.
- Builds on Stage 1 code at `e8b2c52`; its active delta contracts remain authoritative
  until synced. This change unblocks US paper discovery for Stage 2 but does not
  implement the journal, prove paper mutations, or close its provider prerequisites.
