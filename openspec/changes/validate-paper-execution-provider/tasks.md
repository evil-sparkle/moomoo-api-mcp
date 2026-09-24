# Tasks

The after-close retention observation is complete; journal acceptance remains
pending. This is the operator-authorized follow-up
split from Stage 2 development on 2026-09-23.

## 1. Prerequisites and retention

- [ ] 1.1 Record exact separately authorized paper-test bounds and confirm the
  Stage 2 commit, SDK/OpenD versions, isolated account, original journal, operator
  capability and caller-chain access. Verify the authorization and redacted setup
  are recorded before any provider mutation.
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
- [ ] 2.2 Under explicit fault-injection authorization, induce controlled response
  loss during a paper mutation and reconcile (`M03`; former 11.2). Verify no replay,
  truthful uncertainty, durable blocking and evidence-backed operator recovery;
  retain the unresolved journal if evidence cannot release it.
- [ ] 2.3 Exercise the real ZeroClaw/Telegram retry chain (`M04`; former 11.2).
  Verify unchanged operation ID and admission epoch reach the service across a
  retry and that the application makes no second SDK mutation invocation.
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
