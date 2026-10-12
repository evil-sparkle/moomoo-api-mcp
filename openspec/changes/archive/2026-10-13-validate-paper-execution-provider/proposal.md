# Proposal

## Why

Automated journal tests establish application behavior but cannot establish live
paper-provider behavior or whether MCP agent clients preserve retry identity
through their actual caller chains.
The operator authorized separating this validation from Stage 2 development on
2026-09-23, so development can be archived without claiming live acceptance.

## What Changes

- Preserve former Stage 2 task 1.4: measure terminal DAY-order retention in current
  and historical order queries after trading close.
- Preserve former tasks 11.1 and 11.2 (`M01`–`M04`): run a bounded journaled
  paper lifecycle, controlled response-loss recovery, and end-to-end retry-token
  propagation through an authorized MCP agent client's actual retry chain.
- Apply the same execution contract to any compliant MCP agent client, independent
  of agent framework or messaging channel. Record the tested client/version,
  transport and any intermediary; scope live evidence to that tested caller chain.
- Record dated, redacted evidence and limitations. These tasks remain unperformed
  until separately authorized and actually observed.

## Capabilities

### New Capabilities

None. This is verification of the Stage 2 execution-journal contract.

### Modified Capabilities

None. `skip_specs: true` records that verification changes no runtime requirement.
Any discovered behavior change needs a separate implementation change.

## Impact

Requires the completed Stage 2 binary, a separately authorized paper environment,
its original journal, an operator capability, and access to the intended caller
for the selected phase. Any MCP-compatible agent or client may be selected; no
particular product or messaging integration is required. On 2026-10-09 the
operator selected this agent as the direct MCP caller for the Mac first-phase
lifecycle. The `M04` caller retry test was unperformed in that first phase;
it completed on 2026-10-12 for the selected Codex/MCP caller and tested e2bb7cf
image. Subsequent standing
bounded paper-order/fault authorization supersedes the first-phase-only scope. No REAL orders, production deployment, account reset, or
journal replacement is authorized by this plan. Evidence
belongs in this change's verification record.

## Modification visibility acceptance

Include the PR review's acknowledgement-versus-applied-state timing case:
quantity reduction followed by a price-only modification, plus the reverse field
order. Measure actual provider acknowledgement/current/history query timing and
retain interrupted-read limitations. Separately force one controlled stale target
observation after a real acknowledged reduction, restart with the original journal
while its durable visibility guard is pending, and verify pre-dispatch refusal.
Release the fault, confirm fresh real state and verify a new dependent intent
preserves the reduced quantity. Keep real provider samples and injected evidence
separately labeled. Naturally occurring provider lag is not a completion prerequisite.
This criterion refinement was approved by the operator on 2026-10-12; it changes
verification evidence requirements only, with no packaged runtime changes.
