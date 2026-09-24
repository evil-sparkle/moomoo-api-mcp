# Spec Delta

## ADDED Requirements

### Requirement: Report Execution Halt

The health result SHALL report the trade service's execution state, as defined by the
`trade-unlock` Execution Halt requirement.

- It SHALL report `execution_halted`.
- When halted, it SHALL also report `halted_since`, the time the halt began, and
  `halt_error`, the latest lock error.
- Reporting this state SHALL NOT require a gateway request, and SHALL NOT change the
  top-level connectivity status.
- Health reporting SHALL NOT clear the halt.

#### Scenario: Healthy and not halted

- **WHEN** the execution state is `ARMED`
- **THEN** `check_health` SHALL report `execution_halted: false`

#### Scenario: Halted after relock failure

- **GIVEN** the execution state is `HALTED`
- **WHEN** `check_health` is called
- **THEN** it SHALL report `execution_halted: true`, `halted_since` and `halt_error`
- **AND** the connectivity `status` SHALL still reflect the probes alone
- **AND** the state SHALL remain `HALTED`
