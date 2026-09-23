## 1. Configuration and shared connection

- [x] 1.1 Add the explicit securities-market mapping and `Settings.trading_market`
  parsing for `MOOMOO_TRADING_MARKET`. Verify defaults, all accepted values,
  normalization and rejection of unsupported values in `tests/test_settings.py`,
  including startup refusal before transport or gateway initialization.
- [x] 1.2 Pass the setting through `server.py` and `TradeService` into
  `filter_trdmarket`, defaulting direct service construction to NONE. Verify
  NONE/US/HK constructor arguments, bounded failed connections, one process-owned
  context across MCP sessions, and preservation through reconnection in server,
  trade-service and health tests.

## 2. Account discovery and targeting

- [x] 2.1 Add optional `market` and `trd_env` filters to the account-list service
  and MCP tool. Verify omitted/null/NONE behavior, normalization, invalid and blank
  rejection before SDK queries, intersection, multi-market membership, missing
  metadata, empty results and provider errors using synthetic accounts.
- [x] 2.2 Verify MCP schema and responses in `tests/test_tools/test_account.py`:
  existing no-argument calls keep their shape, IDs above 2^53 remain exact strings,
  concurrent HK/US filters do not leak, and later unfiltered discovery and mutation
  account resolution are unaffected. Assert no extra context construction.
- [x] 2.3 Add a read-account resolver distinct from REAL write authorization.
  Verify zero/one/multiple candidates, masked errors, exact explicit IDs, malformed
  IDs, unknown IDs, wrong environments and discovery failures. Assert that REAL
  read candidates are not filtered by the REAL write allowlist.
- [x] 2.4 Apply read resolution to assets, positions, maximum tradable quantity,
  cash flow, current orders/deals and historical orders/deals. Parameterize service
  tests across every endpoint: no account-specific query on ambiguity or failure,
  and explicit concrete IDs and environments reach the correct SDK method.
- [x] 2.5 Resolve account summary once before constituent reads. Verify both
  queries use the same concrete ID, account disappearance fails rather than
  switching accounts, and existing identifier and response shapes are preserved.
- [x] 2.6 Run and extend mutation and combo-preview regression tests with HK and
  US paper accounts visible together. Verify placement narrows by code market,
  modify/cancel refuse unresolved ambiguity, preview retains its existing resolver,
  and REAL allowlists, mode gates, limits, halt ordering and no-replay behavior hold.

## 3. Health and operator configuration

- [x] 3.1 Add health's top-level `trade_market` from validated configuration.
  Verify NONE/US values with healthy and unavailable gateways, no extra provider
  queries, no account IDs, and unchanged five-second deadline and existing fields
  in service and MCP health tests.
- [x] 3.2 Pass the setting through `docker-compose.yml`; verify base/production
  inheritance and NONE/HK override behavior with isolated nonsecret Compose
  fixtures in topology tests. Do not load private environment files or recreate
  the user's live container during automated tests.
- [x] 3.3 Update README, deployment instructions, configuration example template
  and relevant account/trading/health tool docstrings. Verify examples show
  `get_accounts(market="US", trd_env="SIMULATE")`, exact explicit IDs on subsequent
  reads, NONE default migration, HK compatibility scope, and broker-region versus
  market versus environment distinctions. Document that context filters do not
  grant or prohibit trading permission and require a process restart to change.

## 4. Integrated verification

- [x] 4.1 Run `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run basedpyright`. Verify all pass and
  record the results; fake-broker tests must not be described as provider proof.
- [x] 4.2 Run `./scripts/smoke-test.sh` against its isolated stub deployment.
  Verify existing supervision, reconnect, loopback binding and volume contracts
  still pass; preserve the separate logged-in local container and its volume.
- [x] 4.3 After implementation, use the actual MCP entry point in SIMULATE mode
  with the local logged-in OpenD. Verify NONE discovers both observed paper market
  types, US/SIMULATE filtering selects the US paper account, an explicit-ID paper
  order read succeeds, and zero-ID paper reads refuse ambiguity. Record sanitized
  results without account numbers, credentials, REAL detail queries, lock/unlock,
  or order mutations. Keep pending if provider access is unavailable; SDK-only
  planning evidence does not complete this MCP acceptance task.
- Sanitized live result (2026-09-23): NONE discovery included HK and US paper
  authorizations; the US/SIMULATE filter returned an account; an explicit-ID
  SIMULATE order read succeeded; and a zero-ID SIMULATE order read refused
  ambiguity. No identifiers or order details were logged. No REAL detail reads,
  locks, unlocks, or order mutations were made; the existing container and
  volume were left intact.
- [x] 4.4 Run `npx -y @fission-ai/openspec@1.13.1 validate --all --strict
  --no-interactive` and `git diff --check`. Verify deltas preserve the current
  Stage 1 contracts and the separate Stage 2 artifacts remain untouched; prepare
  the implementation PR with both breaking behaviors and actual validation results.

## 5. Review follow-ups

- [x] 5.1 Preserve Compose executable discovery under private-config isolation;
  cover user plugins, system plugins, and an unset market setting.
- [x] 5.2 Remove duplicate summary account resolution while retaining explicit-ID
  SDK validation and failure on account disappearance; simplify repeated guidance
  and test setup without dropping coverage.
- [x] 5.3 Re-run the full local gates, review the final diff, sync this change's
  three delta specs, and archive stage 1.1 in the implementation PR.

Review verification (2026-09-23): `uv run pytest -q` passed with 1,076 passed,
1 skipped, and 72 subtests passed. Ruff lint/format and basedpyright passed.
The isolated container smoke test passed. A real Compose executable installed
only as a synthetic user plugin rendered successfully with isolated configuration;
an inherited US market value did not override the unset NONE default.

The rebuilt image also passed in-process FastMCP dispatch against the existing
local OpenD in SIMULATE mode: HK/US discovery, US filtering, explicit paper order
and summary reads, and ambiguous zero-ID refusal for both orders and summaries.
Only sanitized outcomes were recorded; there were no order mutations, REAL detail
reads, or lock/unlock calls. SDK contract tests additionally prove that summary
account disappearance before either detail query fails without changing IDs.
