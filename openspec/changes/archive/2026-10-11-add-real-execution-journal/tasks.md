# Tasks

## 1. Durable identity and storage

- [x] 1.1 Add immutable environment-bound REAL storage and forbid REAL assumed absence; verify wrong-environment, persistence and storage-policy tests without changing existing paper journal data.
- [x] 1.2 Implement REAL canonical identity and serialized executor around existing mutation validation/dispatch, including local outcome persistence failures; verify duplicate, conflict, concurrent, restart, JIT and broad order/combination tests.

## 2. Conservative recovery

- [x] 2.1 Add process-owned read-only REAL order/position recovery with exact correlation and durable evidence; verify response loss, ambiguous identity, negative rounds, modifications/cancellations and restart tests, and document evidence limitations.

## 3. Service and deployment contracts

- [x] 3.1 Wire all REAL mutation/admission paths and environment-selected MCP inspection plus independent bounded health; verify tool schemas, disconnected retry, quota and transport regression tests.
- [x] 3.2 Add required REAL settings, symmetric startup/shutdown cleanup and persistent container overlay; verify settings, startup failure, Compose and deployment tests, and document initialization and client migration in public examples/runbooks.

## 4. Integration verification

- [x] 4.1 Run full tests, Ruff, format, type checks, public-content secret scanning and strict OpenSpec validation; review the final diff and record evidence without claiming live-provider acceptance.

## Workflow follow-up

- Sync delta specs and archive the completed change before merging.
- Create and link the PR, wait for exact-head CI, merge to main under the user's authorization, and verify remote/local main state.

## Validation evidence

- Full local suite: 1,813 passed, 1 skipped, 106 subtests passed.
- Ruff check, Ruff format check, basedpyright and staged Gitleaks: passed.
- Strict OpenSpec validation: all 28 active-change/main-spec items passed before synchronization.
- Disposable container smoke: stateless authenticated HTTP, gateway loopback isolation, gateway restart and MCP/container restart passed.
- Network-disabled C01-C04 and R01 against final and distinct recreated image identities: passed; REAL acknowledgements/dispatch markers survive, retries do not mutate, and six clean negatives stay blocked.
- Independent reviews covered admission/concurrency, immutable identity, SDK dispatch fields, storage failures, recovery evidence, lifecycle and client/deployment migration. Reported issues were fixed and regression tested.
- This evidence is local, mock-broker and container validation. Live REAL provider acceptance, deployment and Stage 3 approval remain separate.
