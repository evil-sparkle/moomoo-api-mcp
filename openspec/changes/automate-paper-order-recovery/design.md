# Design

## Context

See proposal.md for motivation. ExecutionStore schema 2 already persists dispatch
markers and independent review requirements. PaperExecution serializes writes and
reconciliation but a separate operator must clear recovered markers. Broker remarks
are correlation metadata; they provide neither deduplication nor bounded query
visibility. HTTP sessions are stateless and TradeService owns process connections.

## Goals / Non-Goals

**Goals:** Durably associate placements, recover without client participation, retain
uncertainty separately from gate disposition, and make recovery visible in ordinary
tool results. Preserve short SQLite transactions and existing write-policy checks.

**Non-Goals:** Broker-side exactly-once execution, automatic replacement orders,
REAL recovery, live provider acceptance, or implementing the separate rate limiter.

## Decisions

1. **Separate identities.** Generate an ASCII UUID order tag at PLACE admission,
   persist it before dispatch and override the outbound remark. Keep caller remark
   in canonical request identity. Reusing operation_id as the tag would couple all
   action identities to a placement-only provider field and caller byte constraints.
   MODIFY/CANCEL retain the existing broker order identity.

2. **Schema 3 with additive migration.** Add order_tag, recovery_disposition and next
   check time to operations and a recovery_checks table for actual start/completion
   timestamps, outcome and evidence. Preserve the old recovery_audit table and its
   historical operator_id column; new entries use actor `system`. Decision, audit and
   review removal commit atomically. Legacy tags remain NULL, never backfilled.

3. **One process worker, shared reads.** Start a daemon worker with TradeService and
   stop it before closing the journal. Serialize recovery with paper mutation using
   PaperExecution's lock. SDK I/O occurs outside SQL transactions. Use TradeService
   get_orders(refresh_cache=True), get_history_orders and get_positions with explicit
   SIMULATE account. These are integration points for independent quota governance.
   Background and explicit reconciliation use the same evidence classifier.

4. **Completed rounds determine absence.** Initial query plus four clean negatives
   (five total) applies only to tagged PLACE. Suggested minimum gaps after successive
   negatives are 5, 5, 10, 20 seconds, corresponding to nominal offsets 0/5/10/20/40.
   Calculate the next due time from actual completion; queued reads extend it. A
   recorded broker ID or previously observed correlation excludes assumed absence. Errors,
   malformed records and conflicts retry after 5 seconds without advancing the count.
   Persist progress before returning. At five negatives, keep UNKNOWN_OUTCOME and
   POSSIBLY_SENT, set ASSUMED_NOT_PLACED_AFTER_RETRIES and audit gate release. Never
   replay the original token or automatically place a replacement.

5. **Evidence and accounting.** A recorded broker ID or exact tag must identify one
   order. Deduplicate consistent current/history copies and validate request fields,
   fill values and broker status. An identified placement can recover while open.
   For MODIFY, observed requested quantity/price allows accounting of current state,
   not causal success; terminal targets can be terminal-accounted. For CANCEL only a
   terminal target releases uncertainty. Store filled quantity, average fill price,
   remaining executable quantity and current symbol position (not attributable P&L).
   Query failures and ambiguity preserve the block. Identityless legacy rows remain
   pending rather than receiving the tagged absence disposition.

6. **Monitor assumptions.** Continue checking assumed tags every 60 seconds. On any
   correlated late observation, persist a new blocking reason before fetching position
   evidence; release only after consistent accounting. Audit LATE_BROKER_ORDER_FOUND
   and expose current exposure. Conflict or failed accounting retains the block.
   This detects later visibility; it cannot undo a separate replacement already placed.

7. **Context without pushes or operator.** get_execution and identical retries return
   detailed checks and recovery disposition; paper mutation replies and check_health
   return pending IDs and recent decisions. Background results reach the next request.
   Remove acknowledge_recovery and operator token configuration/middleware. Normal
   bearer auth stays enforced. No client accepts a supplied order state or edits SQL.

## Risks / Trade-offs

- Broker visibility has no documented upper bound → assumed absence is explicitly
  labeled, audited and monitored; duplicate exposure remains possible after a new order.
- Restoring storage predating admission loses tag knowledge → startup reviews known
  rows but cannot discover missing journal operations; replacement is not recovery.
- Missing legacy identity or an active uncertain cancel/modify remains blocked → expose
  reason and evidence; do not weaken other safety checks or invent mutation success.
- Shared reads can be queued or unavailable → actual timestamps and successful-round
  counts control policy, with no elapsed-time shortcut or private quota bypass.
- Unknown newer schema prevents rollback → preserve volume and back up using the
  documented stopped-service procedure; do not run an old image against schema 3.

## Migration Plan

Upgrade v1/v2 atomically to v3 under the existing process lock. Retain all requests,
review reasons, broker identities and historical audit records. Remove operator
configuration from examples and Compose and document that obsolete environment
values have no capability. Roll back application and a matching stopped-service
backup together only after accounting for intervening broker operations. This PR
does not deploy, create live orders or complete the separate provider-validation
change. Archive this change only after the user approves PR merge.
