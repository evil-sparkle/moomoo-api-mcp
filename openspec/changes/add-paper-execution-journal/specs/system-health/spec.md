# Spec Delta

## ADDED Requirements

### Requirement: Report Execution Journal State

The health result SHALL report the state of the execution journal (`DISABLED`,
`READY`, `REVIEW_PENDING`, or `JOURNAL_BLOCKED`), alongside the execution halt
reported under Report Execution Halt.

- When journaled paper execution is not configured, it SHALL report the journal as
  `DISABLED`, and this SHALL NOT degrade the reported status.
- When it is configured, it SHALL report the schema version, the active admission
  epoch, the count of operations that are not in a terminal state, and whether
  recovery review is outstanding.
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
  epoch, a count of zero operations outside a terminal state, and that recovery
  review is not outstanding

#### Scenario: Journal blocked by unresolved outcome reported independently of relock halt

- **GIVEN** paper execution is in state `JOURNAL_BLOCKED` while trade relock state is
  `ARMED`
- **WHEN** `check_health` is called
- **THEN** it SHALL report journal state `JOURNAL_BLOCKED`
- **AND** SHALL report `execution_halted: false`

#### Scenario: Unresolved operations are reported without being changed

- **GIVEN** operations remain outside a terminal state
- **WHEN** `check_health` is called
- **THEN** it SHALL report their count and state `REVIEW_PENDING`
- **AND** no operation's state SHALL change
- **AND** the recovery review gate SHALL NOT be cleared

#### Scenario: Unreachable journal storage is reported without breaking probes

- **GIVEN** journal storage is unreachable or unwritable
- **WHEN** `check_health` is called
- **THEN** it SHALL report the journal storage failure explicitly
- **AND** the quote and trade connectivity probes SHALL still be reported accurately
