# Tasks

All live validation remains pending. This is the operator-authorized follow-up
split from Stage 2 development on 2026-09-23, not a record of completed tests.

## 1. Prerequisites and retention

- [ ] 1.1 Record exact separately authorized paper-test bounds and confirm the
  Stage 2 commit, SDK/OpenD versions, isolated account, original journal, operator
  capability and caller-chain access. Verify the authorization and redacted setup
  are recorded before any provider mutation.
- [ ] 1.2 Measure how long a terminal DAY order remains queryable using current
  and historical order queries after close (former Stage 2 task 1.4). Verify by
  recording timestamps and both observed windows; distinguish lower bounds from
  measured expiry and never infer absence from an empty query.

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
