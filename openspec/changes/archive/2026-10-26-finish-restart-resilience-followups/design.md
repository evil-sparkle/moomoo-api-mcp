## Context

The trade service logs connect/reconnect lock failures and preserves the connection.
Health already reports execution halts independently of connectivity. The smoke
test checks absence of session IDs but does not await gateway connections after
MCP replacement. Production is reported to use a selected hotfix in SIMULATE.

## Goals / Non-Goals

Goals: finish repository follow-ups with bounded diagnostics and focused tests.
Non-goals: release production, change deployment modes or locking policy, add SSE.
A separately authorized local unlock/re-lock test is recorded in
`unlock-verification.md`; no order mutations were issued.

## Decisions

Expose `gateway_lock_at_rest` with `status` (`not_applicable`, `not_attempted`,
`succeeded`, `failed`) and nullable `checked_at`. It records only the last
connect/reconnect attempt, never the current gateway lock state. Omit raw broker
errors to avoid exposing sensitive provider messages. A separate short-held mutex
protects snapshots; health never waits for a lock/unlock network operation.
Skipped attempts during a JIT write preserve the prior observation. A later
successful attempt replaces a failure; neither transition changes execution halt.

Preserve JSON notifications being dropped. Reconcile already-completed follow-ups
with source evidence. On 2026-10-26 the user approved deferring archived task
3.12 as non-blocking: accept the provider-documented OpenD-wide scope for this
design, retain per-operation unlock/re-lock for occasional REAL operations, and
do not represent the live scope experiment as passed. Revisit only if provider
behavior conflicts with the documented contract or gateway ownership changes.

## Risks / Trade-offs

Historical success can become stale; field naming and documentation explicitly
avoid claiming the gateway is currently locked. SIMULATE has no lock-at-rest
policy and reports not applicable. Container validation uses a synthetic gateway,
which cannot establish real provider unlock scope. Publication to the selected
hotfix can deploy automatically and is separate from local implementation.
