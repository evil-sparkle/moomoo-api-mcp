# Tasks

## 1. Provider admission primitive

- [x] 1.1 Add the provider-operation policy, thread-safe rolling budget and reservations; verify fake-clock window, safety-margin, parallel reservation and delayed-dispatch tests.
- [x] 1.2 Add bounded async admission and cancellation cleanup; verify deterministic timeout/retry-after, released-reservation and cancelled-wait tests.

## 2. Option-chain integration

- [x] 2.1 Share the limiter between synchronous and async service paths, validating before admission; verify invalid requests, provider rejection accounting and existing option discovery tests.
- [x] 2.2 Route the MCP option-chain tool through async admission; verify 11 concurrent MCP calls, unrelated tool availability, worker-pool congestion and cancellation before/after dispatch.
- [x] 2.3 Document policy, timeout, retry guidance, single-process scope, restart reset and external-client uncertainty in README and tool descriptions; review against the official provider source and implemented behavior.

## 3. Integration verification

- [x] 3.1 Run repository tests, Ruff lint/format, basedpyright and strict OpenSpec validation; resolve actionable failures and record results in the review handoff.
- [x] 3.2 Run openspec-verify-change against all requirements, scenarios and design decisions; fix actionable findings and repeat affected checks.

## Workflow follow-up

- Keep the focused change available on its review branch; archive after review.
- Inventory other documented SDK endpoint limits in a separate change.
