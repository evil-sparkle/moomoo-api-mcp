# Tasks

## 1. Capability and event contracts

- [x] 1.1 Add scoped documented capability discovery and read-only MCP tool; verify unknown support, policy separation, account permissions and independent journal readiness tests.
- [x] 1.2 Add bounded process-owned order/deal callbacks and event tool; verify concurrency, account/environment isolation, exact IDs, early callbacks, cursor gaps and reconnect behavior.
- [x] 1.3 Document capabilities and ephemeral event limits with official source links in the trading guide; verify examples match exported signatures.

## 2. Instrument and session contracts

- [x] 2.1 Add Decimal instrument rules and bounded connection-aware static metadata caching; verify price thresholds, option unknown ticks, missing facts, cache expiry and fresh snapshots.
- [x] 2.2 Integrate placement/modification validation and explicit REAL sessions while preserving paper scope and old identities; verify journal conflict/default retry and unsent rejection regressions.
- [x] 2.3 Document price/tick/lot distinctions and session restrictions in the trading guide; verify against pinned SDK and official API references.

## 3. Query contracts

- [x] 3.1 Extract read queries with compatibility delegates and typed exact current/historical single-order lookup; verify local historical matching, scope absence and ambiguity tests.
- [x] 3.2 Add actual order fees, restrictions and account-scoped quota coverage; verify paper/CA rejection, 400-ID bounds and independent fee quota tests.
- [x] 3.3 Add bounded immutable normalized query pages; verify cursor scope, expiry, exact identifiers, hard resource bounds and no SDK continuation dispatch.
- [x] 3.4 Correct REAL-only deal behavior and document query migration/contracts; verify existing recovery list contracts and tools remain intact.

## 4. Preview and durable trading controls

- [x] 4.1 Add read-only single-order preview and exact prepared mutation contracts; verify no broker write/unlock and documented maximum-quantity/margin boundaries.
- [x] 4.2 Implement optional durable approvals and separate authenticated operator decision route; verify ordinary credential rejection, expiry, parameter binding, decision audit and journal retries.
- [x] 4.3 Add exact-symbol restrictions and atomic durable daily submission budgets at both journal boundaries; verify every combo leg, modifying quantities, concurrent limits, restart persistence, unknown outcomes and no duplicate retry charge.
- [x] 4.4 Wire fail-closed startup/settings/storage lifecycle and public example configuration; verify READ_ONLY storage independence, distinct tokens, required paths and operator guide examples.

## 5. Integration validation

- [x] 5.1 Verify MCP schemas, annotations, bounded offloaded behavior and unchanged journal recovery via cross-feature tests and source review.
- [x] 5.2 Run strict OpenSpec validation, full Ruff lint/format, basedpyright and pytest; inspect the final diff for secrets and unintended changes and resolve review findings.

## Workflow follow-up

- Archive and sync this completed change before the authorized merge; retain the separate live provider-validation change.
- Create and link the PR, verify exact-head remote CI, then merge automatically under the user's authorization and verify remote main.


## Validation evidence

- Full local mocked suite: 2,110 passed, 1 skipped, 106 subtests passed.
- Focused cross-feature suite: 223 passed.
- Rebuilt application image passed isolated C01-C04 paper and R01 REAL persistence probes, including acknowledged and unknown-outcome retries with no gateway calls.
- Ruff lint and formatting pass; basedpyright reports zero errors.
- All 32 main specifications pass strict OpenSpec validation.
- Staged diff secret scan reports no leaks; final diff check passes.
- Validation uses local journals, temporary controls storage and mocked SDK calls. Live OpenD acceptance remains separate; the provider-validation change stays active.
