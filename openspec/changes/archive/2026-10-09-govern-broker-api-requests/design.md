# Design

## Context

See proposal.md for motivation. Services expose synchronous SDK wrappers, tools
offload those wrappers through AnyIO, and option discovery already reserves
capacity before waiting for a worker. Direct SDK paths also exist in instrument
assessment and paper execution. Preserve those public interfaces and journal
boundaries while sharing one dispatcher across process-owned services.

## Goals / Non-Goals

**Goals:** Cover all 22 limited runtime operations, preserve process-owned
connections and existing option-chain cancellation guarantees, and avoid quota
waits inside the shared SDK worker pool.

**Non-Goals:** Replay broker writes, add a distributed service, or invent timed
limits for cached real-time reads and subscription-management operations.

## Decisions

1. Use PyrateLimiter 4.5 exact in-memory buckets for dispatched history. Retain a
   thin thread-safe reservation adapter because pending worker capacity must not
   age out and cancellation must release only unused capacity. Leaky-bucket
   algorithms do not match moomoo's rolling windows.
2. Define one operation registry with account/user/gateway scopes and documented
   conditions. Placement and combo placement share a group. Both max-quantity
   query APIs conservatively share a group because the combo documentation
   describes a collective limit. Other operations have independent groups.
3. Use short-period buckets plus exclusive pending reservations for paced
   groups, ensuring delayed workers cannot create a later burst. Direct sync
   callers fail immediately rather than sleeping.
4. Prepare MCP admission needs outside the main SDK invocation, resolve account
   identities, and asynchronously reserve a bounded bundle before worker
   dispatch. Pass reservations through a request context copied into the worker.
   Unused reservations are released. Every actual SDK call also checks the
   registry, so internal and direct synchronous callers cannot bypass limits.
   Cancellation also stops later calls from a validation worker that has already
   started. A mutation protects its permits immediately before unlocking or
   marking paper dispatch, retaining them until the write and any relock finish.
5. Reserve write capacity before the order dispatch boundary. Borrow an existing
   MCP reservation when present. REAL writes reserve two unlock-interface calls;
   paper writes reserve before their marker. Never retry a partially dispatched
   service operation. Provider failures keep their original semantics.
6. Validate known input and policy errors before admission. Existing journal
   retries return stored outcomes without new write capacity or broker replay.
   Serialize the journal recheck, quota reservation, and local admission to
   preserve concurrent retries of one token. If quota waiting times out after
   another caller admits that token, validate identity and read its stored status
   instead of describing the existing operation as unsent.

## Risks / Trade-offs

- Process-local state cannot coordinate other OpenD clients; document this and
  allow explicitly shared dispatcher injection between local wrappers.
- Millisecond bucket timestamps require a conservative boundary margin; verify
  actual SDK starts with fake clocks and delayed workers.
- A newly introduced internal call can exhaust unprepared capacity; its final
  synchronous gate still refuses safely rather than bypassing the quota.
- Conservative max-quantity grouping can reduce throughput; document the broker
  ambiguity and keep the grouping in configuration rather than scattered code.

## Migration Plan

Update the uv lock and container dependency installation, run mocked tests and
the repository checks, then deploy normally. No journal or tool schema migration
is needed. Rollback restores the prior code and lockfile. Archive the completed
OpenSpec change after the user approves merging the PR.

Automatic paper recovery uses the same gated service reads as public queries.
It defers quota failures as recovery errors without counting them as successful
negative checks. Manual reconciliation prepares current-order, history-order,
and position capacity before entering its worker; stored or not-yet-due checks
need no capacity. Preserve the persisted correlation tag and dispatch marker.

README command references use the same OpenSpec 1.14.1 CLI as CI in the latest
main branch, replacing removed helper scripts; do not regenerate integrations.
