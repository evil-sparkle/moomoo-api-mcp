# Automatic paper execution recovery

Paper recovery is owned by the MCP process. It runs after startup and after an
uncertain response; it never sends placement, modification or cancellation requests.

The journal uses schema 3. Startup atomically upgrades schemas 1 and 2, preserving
requests, broker IDs, review reasons and historical recovery audits. New placements
receive a unique ASCII UUID `order_tag` (36 UTF-8 bytes) before dispatch. The broker
receives this tag as its `remark`; caller remarks remain in the original request and
participate in retry identity. The caller's `operation_id` identifies one action;
modifications and cancellations have their own IDs and reference the broker order.

Keep the same persistent journal volume. Old images cannot open schema 3. A rollback
requires the matching stopped-service backup and accounting for any broker actions
since that backup; replacing a journal does not recover a missing operation.

Recovery queries fresh current orders and history orders for the recorded SIMULATE
account. It matches the exact tag or recorded broker ID, deduplicates consistent
current/history copies, checks request fields and records fills and the current
symbol position. A confirmed open placement can release recovery immediately.
Modification recovery accounts for observed requested quantity/price or a terminal
target; cancellation recovery requires a terminal target. Accounting for a target
does not establish that the uncertain mutation caused its current state.

For an uncertain tagged placement with no recorded broker ID or previously observed
correlation, one initial check
plus four successful negative rounds records `ASSUMED_NOT_PLACED_AFTER_RETRIES`.
The original state remains `UNKNOWN_OUTCOME` / `POSSIBLY_SENT`. The decision, audit
and removal of its review reasons commit together. The original operation never
replays. A separate new trading decision can use a new operation ID once all other
blocks are clear.

Minimum gaps after the first four clean negatives are 5, 5, 10 and 20 seconds,
giving nominal offsets 0/5/10/20/40. Gaps start at actual query completion; queued
queries extend this schedule. Failed, malformed or conflicting queries do not
count as negative evidence and retry later. There is no 40-second wall-clock cutoff.
Recovery uses shared TradeService read methods, where provider quota governance can
queue reads independently.

Assumed absence is a paper continuity policy, not proof that the broker never
accepted the request. Broker correlation is not an idempotency key. A separate new
order can therefore duplicate exposure if the first order appears late. The worker
keeps checking assumed tags every 60 seconds. Late discovery durably restores the
block before position accounting, records `LATE_BROKER_ORDER_FOUND`, and reports
the current exposure. Failed accounting or conflicting evidence keeps that block.
The worker never cancels or replaces a broker order.

Untagged legacy placements can recover by recorded broker ID. Without either
identity they remain blocked; quantity/price/symbol similarity cannot establish
ownership. Active uncertain modifications/cancellations and journal storage
failures also remain pending until consistent accounting or healthy restart.

The worker starts with the paper-enabled process, before the HTTP listener starts,
and continues across stateless client requests. `MCP_AUTH_TOKEN` remains the ordinary
credential; `MCP_OPERATOR_TOKEN` and `acknowledge_recovery` have been removed.

The agent receives context on its next tool call:

- `get_execution(operation_id)` and identical operation retries return `order_tag`,
  `broker_order_id`, `broker_status`, `recovery`, actual `recovery_checks`, and
  `accounted_facts` when broker accounting succeeds.
- Paper mutation replies include `recovery_updates` for pending and recent decisions.
- `check_health` includes `execution_journal.pending_operation_ids` and
  `execution_journal.recovery_updates`; it does not query the broker for recovery.
- `reconcile_execution` advances a due check using the same server policy. Calling
  it repeatedly does not accelerate the durable schedule or submit an order.

`recovery.disposition` describes gate accounting separately from the original
mutation state. `successful_negative_checks`, `last_check`, `next_check_at`,
`absence_proven: false`, and `original_operation_replay_allowed: false` make the
assumption and progress explicit. Check times are Unix seconds in UTC. Background
completion does not push an unsolicited message into an agent's conversation.

Validation uses fake broker responses, real SQLite and crash/restart faults. It does
not establish live provider visibility bounds, retention, or broker idempotency.
