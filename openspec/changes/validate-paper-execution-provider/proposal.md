# Proposal

## Why

Automated journal tests establish application behavior but cannot establish live
paper-provider behavior or whether ZeroClaw and Telegram preserve retry identity.
The operator authorized separating this validation from Stage 2 development on
2026-09-23, so development can be archived without claiming live acceptance.

## What Changes

- Preserve former Stage 2 task 1.4: measure terminal DAY-order retention in current
  and historical order queries after trading close.
- Preserve former tasks 11.1 and 11.2 (`M01`–`M04`): run a bounded journaled
  paper lifecycle, controlled response-loss recovery, and end-to-end retry-token
  propagation through ZeroClaw and Telegram.
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
chain. No REAL orders, deployment, account reset, or journal replacement is
authorized by this plan. Evidence belongs in this change's verification record.
