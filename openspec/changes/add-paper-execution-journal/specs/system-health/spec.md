# Spec Delta

## ADDED Requirements

### Requirement: Report Execution Journal State

The health result SHALL report the state of the execution journal (`DISABLED`,
`READY`, `REVIEW_PENDING`, or `JOURNAL_BLOCKED`), alongside the execution halt
reported under Report Execution Halt.

- When journaled paper execution is not configured, it SHALL report the journal as
  `DISABLED`, and this SHALL NOT degrade the reported status.
- When it is configured, it SHALL report the schema version, the active admission
  epoch, the operation counts below, and whether recovery review is outstanding.
- **Exactly one state SHALL be reported.** More than one condition can hold at once, so
  the reported state SHALL be the first match in this order:
  1. `DISABLED` — journaled paper execution is not configured;
  2. `JOURNAL_BLOCKED` — any blocking reason is active;
  3. `REVIEW_PENDING` — the startup recovery gate has not been cleared;
  4. `READY` — the gate is clear and nothing is blocking.

  When `JOURNAL_BLOCKED` is reported, the blocking reason SHALL be reported with it.
- **Operations outside a terminal state SHALL be reported as three separate counts,**
  because a single count cannot distinguish normal operation from a fault:
  - `in_flight` — operations dispatching now. A `READY` journal MAY report a non-zero
    `in_flight`, and this alone SHALL NOT produce `REVIEW_PENDING`.
  - `awaiting_review` — non-terminal operations the startup gate is still holding.
  - `blocking` — operations whose state is a live blocking reason.
- Reporting SHALL NOT require a gateway request, SHALL NOT change the top-level
  connectivity status, and SHALL NOT alter any operation's state or clear the
  recovery review gate.
- When journal storage is unreachable or unwritable, health SHALL report that
  explicitly as `JOURNAL_BLOCKED`, and connectivity probes SHALL continue to be
  reported accurately.

#### Scenario: Journal not enabled

- **GIVEN** journaled paper execution is not configured
- **WHEN** `check_health` is called
- **THEN** it SHALL report the journal as not enabled
- **AND** the top-level status SHALL reflect the connectivity probes alone

#### Scenario: Journal enabled and reviewed

- **GIVEN** journaled paper execution is configured and recovery review has completed
  with nothing outstanding
- **WHEN** `check_health` is called
- **THEN** it SHALL report state `READY`, the schema version, active admission
  epoch, zero `awaiting_review` and zero `blocking` operations, and that recovery
  review is not outstanding

#### Scenario: An in-flight operation does not make a ready journal review-pending

- **GIVEN** the recovery gate is clear, nothing is blocking, and one operation is
  dispatching
- **WHEN** `check_health` is called
- **THEN** it SHALL report state `READY`
- **AND** SHALL report that operation under `in_flight`
- **AND** SHALL NOT report state `REVIEW_PENDING`

#### Scenario: Journal blocked by unresolved outcome reported independently of relock halt

- **GIVEN** paper execution is in state `JOURNAL_BLOCKED` while trade relock state is
  `ARMED`
- **WHEN** `check_health` is called
- **THEN** it SHALL report journal state `JOURNAL_BLOCKED`
- **AND** SHALL report `execution_halted: false`

#### Scenario: Unresolved operations are reported without being changed

- **GIVEN** the startup recovery gate is holding operations outside a terminal state
- **AND** no blocking reason is active
- **WHEN** `check_health` is called
- **THEN** it SHALL report state `REVIEW_PENDING` and their `awaiting_review` count
- **AND** no operation's state SHALL change
- **AND** the recovery review gate SHALL NOT be cleared

#### Scenario: Blocking outranks outstanding review

- **GIVEN** a blocking reason is active and the startup recovery gate is also holding
  operations outside a terminal state
- **WHEN** `check_health` is called
- **THEN** it SHALL report state `JOURNAL_BLOCKED` and the blocking reason
- **AND** SHALL NOT report state `REVIEW_PENDING`
- **AND** SHALL still report the `awaiting_review` count

#### Scenario: Unreachable journal storage is reported without breaking probes

- **GIVEN** journal storage is unreachable or unwritable
- **WHEN** `check_health` is called
- **THEN** it SHALL report the journal storage failure explicitly
- **AND** the quote and trade connectivity probes SHALL still be reported accurately
