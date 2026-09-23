# system-health Specification

## Purpose

Provides health diagnostics, liveness probes, and connection status monitoring between the MCP server and the downstream OpenD gateway.

## Requirements

### Requirement: Check Server Health

The system MUST provide a tool to check the health and connectivity of the MCP
server and its downstream OpenD gateway. It SHALL actively probe quote and trade
connectivity, preserve top-level status and host fields, report per-service status
and observation time, and include gateway version information when available.
It SHALL complete within a five-second total deadline and SHALL NOT infer health
from the mere existence of a context object or equate connectivity with unlock.

#### Scenario: Verify connectivity to OpenD

- **WHEN** both quote and trade probes succeed
- **THEN** the tool returns `connected`, the endpoint, observation time, and the
  available gateway version without account contents or credentials.

#### Scenario: Report connection failure

- **WHEN** the gateway is inaccessible, including after successful initialization
- **THEN** the tool returns `disconnected` and a diagnostic error.

#### Scenario: Report partial availability

- **WHEN** exactly one of the quote and trade probes succeeds
- **THEN** the overall status is `degraded` and identifies the failing service.

#### Scenario: Bound unavailable gateway probes

- **WHEN** a probe exceeds the deadline or repeated calls arrive during a stuck probe
- **THEN** health returns a timeout result within the deadline without accumulating
  unbounded background work.

#### Scenario: Serve health after downstream startup failure

- **WHEN** a downstream connection cannot initialize
- **THEN** MCP remains available for health requests and releases partially
  initialized resources on shutdown.

### Requirement: Report Configured Trade Market Filter

`check_health` SHALL include top-level `trade_market` containing the normalized
configured trade-context market filter, including `NONE` for all-market discovery.
It SHALL report this configuration even when the gateway is unavailable, without
extra broker queries, account identifiers, or changes to the existing health
deadline, status semantics, trading mode or execution-halt fields. The field SHALL
describe discovery configuration, not trading authorization or market availability.

#### Scenario: All-market connection

- **WHEN** health is requested with the default market configuration
- **THEN** `trade_market` SHALL be `NONE`
- **AND** the existing quote and trade probe outcomes SHALL remain independently reported

#### Scenario: Gateway unavailable

- **GIVEN** the configured filter is `US` and OpenD cannot be reached
- **WHEN** health is requested
- **THEN** `trade_market` SHALL still be `US`
- **AND** connectivity SHALL be reported as unavailable or failed rather than inferred
  from the configured market

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
