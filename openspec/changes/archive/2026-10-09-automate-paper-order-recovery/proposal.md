# Proposal

## Why

Paper trading currently requires a separate operator to release recovered dispatch
markers, and a lost placement response can leave no broker identity to reconcile.
The MCP service should recover independently of client instructions and report both
verified outcomes and explicitly assumed absence in ordinary tool results.

## What Changes

- Persist a server-generated order tag separately from the caller's operation ID,
  and use that tag as the broker remark for new paper placements.
- Start a process-owned recovery worker that queries unresolved operations after
  restart and after an uncertain response. It never sends or replays orders.
- Recover unique, consistent broker evidence automatically and audit gate release.
- After one initial check and four successful negative retries for a tagged
  placement, release the paper gate under `ASSUMED_NOT_PLACED_AFTER_RETRIES` while
  preserving `UNKNOWN_OUTCOME`. Errors and ambiguous matches do not count.
- Persist actual check times and progress across restarts; keep monitoring assumed
  placements for late broker appearance.
- Return recovery decisions in execution results, paper mutation replies, and
  journal health, including pending operation IDs.
- **BREAKING** Remove the operator-only recovery tool and operator bearer token.
  The ordinary authenticated MCP interface exposes evidence-backed reconciliation.
- Rate-limit governance remains a separate change; recovery uses shared trade read
  methods rather than introducing its own limiter.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `execution-journal`: durable order tags, server-owned recovery, retry-count-based
  assumed absence, migration, late discovery, and recovery audit/result semantics.
- `order-placement`: reserve paper broker remarks for order correlation and include
  recovery context in mutation results.
- `system-health`: expose pending recovery identities and durable recovery updates.
- `configuration`: remove the separate operator credential.

## Impact

Execution store schema migration, paper execution/recovery, server lifecycle and
authentication, settings, paper Compose configuration, tool descriptions, tests,
and public recovery documentation. REAL execution remains outside this policy;
READ_ONLY remains journal-independent. Existing untagged placements cannot acquire
a tag retrospectively: known broker IDs can recover, but identity-less legacy rows
remain unresolved. Mocked fault tests verify application behavior; no live orders,
deployment, or live-provider acceptance are part of this implementation.
