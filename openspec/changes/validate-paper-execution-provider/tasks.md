# Tasks

The after-close retention observation is complete; journal acceptance remains
pending. This is the operator-authorized follow-up
split from Stage 2 development on 2026-09-23.

## 1. Prerequisites and retention

- [x] 1.1 Record exact separately authorized paper-test bounds and confirm the
  Stage 2 commit, SDK/OpenD versions, isolated account, original journal, operator
  capability, MCP agent client and caller-chain access. Verify the authorization
  and redacted setup are recorded before any provider mutation.
  On 2026-10-09 the operator approved a direct-agent Mac US SIMULATE first phase:
  21:45–22:00 SGT, only in confirmed regular trading; one US.AAPL BUY share,
  NORMAL/DAY at USD 1.00, at most one price-only modification to USD 1.01 and one
  cancellation of that exact test order. Maximum one new order, three mutation
  intents and one additional share/USD 1.01 gross exposure. Stop on rejection,
  fill, uncertainty or journal blocking; no sale, fault injection, restart or
  recovery acknowledgement. Exact account identity and redacted authorization
  are retained in the local operator record, outside Git. Selected source
  07b119aad4d1666b6aebbfa21f3c112955fdd283 and its pinned CI digest, SDK 10.10.7008,
  OpenD build pin 10.10.7008/API server_ver 1010, distinct authenticated operator
  capability, original empty READY journal and direct-agent MCP access were
  verified. The operator confirmed this stack is the sole paper writer; the
  other instance is READ_ONLY. Any MCP-compatible caller can perform the tests;
  task 2.3 remains unperformed. This completes preparation, not live acceptance.
  The operator subsequently authorized submitting the same bounded order ahead
  of the market session, with RTH-only fills. Verification, modification and
  cancellation retain the approved 21:45–22:00 SGT window. Off-market submission
  and regular-hours execution are distinct; no other bounds were expanded.
- [x] 1.2 Measure how long a terminal DAY order remains queryable using current
  and historical order queries after close (former Stage 2 task 1.4). Verify by
  recording timestamps and both observed windows; distinguish lower bounds from
  measured expiry and never infer absence from an empty query.
  At 2026-09-24 17:01:45 SGT both queries returned the original cancelled DAY
  order with zero fills and its caller remark, about 20 hours 50 minutes after
  cancellation. Both windows are lower bounds, not measured expiry. The probe ran
  later than its planned 08:15 slot and issued no mutations or unlocks. See
  [Stage 1 evidence](../archive/2026-09-24-harden-trading-safeguards/verification.md). This closes
  retention only, not journaled provider acceptance.

## 2. Live acceptance

- [ ] 2.1 Recheck group 1's provider capabilities, then run one bounded journaled
  paper lifecycle: place, modify price and cancel (`M01`, `M02`; former 11.1).
  Verify operation IDs, preserved epochs, journal states, broker identities,
  factual final statuses, fills and resulting exposure in redacted evidence.
  Partial observation on 2026-10-09: the amended advance placement was
  acknowledged at 09:44 SGT. Current/history reads at 09:45 SGT showed the same
  RTH DAY order SUBMITTED, one share at USD 1.00, zero fills and no positions.
  The one-time read-only observer completed at 21:45:01 SGT inside the approved
  window; provider market state was AFTERNOON and the same order remained
  SUBMITTED with zero fills and no positions. A separate operator-requested
  status read at 22:24 SGT returned the same result. The original journal remains
  READY and intact. Modification and cancellation did not run before that
  window ended. Later standing authorization and original-order cancellation
  are recorded in [dated evidence](verification.md); this historical deadline
  is no longer a permission blocker.
  A separate fill amendment was approved for 2026-10-10 01:45–02:15 SGT:
  one price-only change of the same order to USD 336.00, preserving quantity one
  and NORMAL/DAY/RTH, plus a USD 336 Mac cap and one container recreation with
  the original journal and volumes. The operator activated the cap at 01:48 SGT.
  At 01:56:31 SGT, exactly one modification intent was durably recorded as
  REFUSED / NOT_SENT, with dispatch marker zero. The pinned image rejected the
  provider's `fill_outside_rth=N/A` as outside its verified regular-hours scope.
  No modification reached the broker, no retry or cancellation ran, and the
  01:57 SGT current/history reads showed the original order still SUBMITTED at
  USD 1.00 with zero fills and positions. The original journal remains READY,
  intact and unblocked, with two operations and no review/recovery records.
  Its epoch changed through the authorized recreation; the refused intent used
  and retained the new epoch. Stop-on-refusal ended this approved attempt.
  The separately authorized local implementation is tracked in
  [fix-paper-order-session-evidence](../archive/2026-10-11-fix-paper-order-session-evidence/tasks.md).
  Its Session.RTH local image was activated on 2026-10-10; the original order
  was cancelled with zero fills. Standing bounded paper-order/fault authority
  now applies. A successful modification and complete new lifecycle remain
  unobserved. Fresh source/image identity and execution preflight are required;
  historical checks do not verify a later main revision.
- [ ] 2.2 Under explicit fault-injection authorization, induce controlled response
  loss during a paper mutation and reconcile (`M03`; former 11.2). Verify no replay,
  truthful uncertainty, durable blocking and evidence-backed operator recovery;
  retain the unresolved journal if evidence cannot release it.
- [ ] 2.3 Exercise an authorized MCP agent client's actual retry chain (`M04`;
  former 11.2). Record the client/version, MCP transport and any intermediary.
  Verify unchanged operation ID and admission epoch reach the service across a
  retry and that the application makes no second SDK mutation invocation. The
  contract applies to any compliant MCP agent client; scope the observed result
  to the tested client and caller chain.
  Standing bounded authorization supersedes the original first-phase-only
  scope. Successful MCP reads alone do not establish retry identity or
  duplicate suppression.
- [ ] 2.4 Publish a dated verification record distinguishing observed results,
  limitations and incomplete checks. Verify all claims against captured evidence
  and retain any unresolved operation instead of resetting its journal.

- [ ] 2.5 Under separately authorized paper-test bounds, submit a quantity reduction
  followed immediately by a price-only modification (`M02` visibility timing).
  Separate the SDK acknowledgement from timestamped current/history order reads;
  record when the new total quantity and price become visible. For the 100 -> 50
  share example (or a separately approved smaller equivalent), prove the second
  mutation never restores the old quantity. If reads lag acknowledgement, verify a
  pre-dispatch refusal with no second SDK call/marker, then verify a newly authorized
  intent after the requested state becomes visible. Preserve retries and refused
  tokens unchanged. Repeat across a process restart while visibility is pending,
  and measure the reverse price-change then quantity-only case. Do not infer this
  timing from the adversarial double or claim provider acceptance without evidence.

## Session timing

- Preparation, authenticated reads, journal inspection, local orchestration
  checks and evidence publication may run outside market hours. Task 1.2's
  retention observation specifically runs after close.
- New live mutations for tasks 2.1, 2.2 and 2.5 use the selected US RTH test
  scope and require freshly confirmed regular trading. An earlier advance
  placement establishes off-market submission only, not the remaining lifecycle.
- Task 2.3 may retry an exactly matching, durably acknowledged intent outside
  market hours when it returns stored state without SDK dispatch. Never replay
  an uncertain or refused intent, substitute its epoch or create a new intent
  to bypass a closed session. A new live prerequisite follows the RTH gate.
- Read-only reconciliation may continue after close. Fault injection during a
  mutation and the pending-visibility restart sequence require bounded live
  orchestration; local mock checks do not complete those provider tasks.

RTH normally runs 09:30–16:00 US Eastern on exchange business days, subject to
holidays and early closes. This is 21:30–04:00 SGT during US daylight saving time
and 22:30–05:00 otherwise; the closing time is on the following Singapore date.
Check the provider calendar and market state before dispatch instead of assuming
that a Singapore weekday or wall-clock slot establishes readiness.
Sources: [Nasdaq hours/calendar](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)
and [Moomoo session definitions](https://openapi.moomoo.com/moomoo-api-doc/en/quote/quote.html#session).
