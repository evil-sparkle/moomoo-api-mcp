## MODIFIED Requirements

### Requirement: Enforce Configured Trading Mode

The system SHALL accept `MOOMOO_TRADING_MODE` values `READ_ONLY`, `SIMULATE`, and
`REAL`, default to `READ_ONLY`, and reject unknown values on startup. The service
layer SHALL enforce policy for single-leg and combo placement, modification,
cancellation, and unlock before any gateway request. Direct service construction
SHALL also default to read-only. Health SHALL expose the configured mode.

#### Scenario: Trading mode enforcement contract

- **WHEN** the service evaluates an operation under its configured trading mode
- **THEN** the following detailed obligations SHALL hold:

Ordinary `READ_ONLY` operation SHALL remain independent of the execution journal.
Without explicit observation, `READ_ONLY` SHALL NOT open, create or require journals or fail for want of one. Explicit observation SHALL open only configured existing journals for inspection and query-only recovery, without enabling writes or initialization.

A `SIMULATE` mutation SHALL be admitted under an explicit `SIMULATE` or `REAL`
policy, using the same dedicated persistent paper database across mode changes.
`REAL` permits real execution in addition to paper execution; it does not change
the environment or storage identity of a paper operation. REAL mutations SHALL
use their separate environment-bound journal and SHALL NOT write into the paper
database.
When journal storage is required but unavailable or blocked, the mutation SHALL be
refused. The system SHALL NOT fall back to dispatching it unjournaled.

Paper and REAL journal blocking SHALL be independent of the REAL relock halt
(`ARMED`/`HALTED`). Calling `lock_trade` SHALL NOT clear either journal's failures.

The trading mode SHALL be determined by configuration alone. No tool argument,
including the environment and account arguments a caller must supply, SHALL elevate
the configured mode or enable `REAL` execution.

#### Scenario: Read-only deployment

- **WHEN** any trading mutation or unlock is requested in READ_ONLY mode
- **THEN** the service rejects it before contacting the gateway while allowing
  reads and previews subject to gateway permissions.

#### Scenario: Read-only deployment needs no journal

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the server starts and serves read operations without explicit observation
- **THEN** it SHALL NOT open, create or require a journal database
- **AND** the absence of journal storage SHALL NOT cause a failure

#### Scenario: Simulation deployment

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE`
- **WHEN** SIMULATE mode receives a SIMULATE write for an allowlisted simulated
  account, with an explicit environment and a caller-supplied operation identifier
  and valid admission epoch
- **THEN** it may submit that write through the execution journal
- **AND** REAL writes and unlock remain denied, without silent rewrite to SIMULATE.

#### Scenario: Paper journal blocking is independent of REAL relock halt

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE` and paper execution is in state
  `JOURNAL_BLOCKED`
- **WHEN** `lock_trade` is called
- **THEN** the call SHALL NOT clear `JOURNAL_BLOCKED`
- **AND** paper mutations SHALL continue to be refused

#### Scenario: Explicit real deployment

- **WHEN** REAL mode receives a valid SIMULATE or REAL write
- **THEN** policy permits the requested environment, subject to its own journal
  admission and gateway checks, without silently changing the environment.

#### Scenario: Password does not enable trading mode

- **WHEN** a password is configured in READ_ONLY or SIMULATE mode
- **THEN** startup does not unlock trading or elevate the configured mode.

#### Scenario: Reject invalid configuration

- **WHEN** the mode is not one of the documented values
- **THEN** startup fails with a configuration error rather than selecting REAL.
