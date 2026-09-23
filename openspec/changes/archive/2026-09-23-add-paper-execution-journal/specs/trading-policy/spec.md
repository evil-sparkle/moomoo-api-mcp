# Spec Delta

## MODIFIED Requirements

### Requirement: Enforce Configured Trading Mode

The system SHALL accept `MOOMOO_TRADING_MODE` values `READ_ONLY`, `SIMULATE`, and
`REAL`, default to `READ_ONLY`, and reject unknown values on startup. The service
layer SHALL enforce policy for single-leg and combo placement, modification,
cancellation, and unlock before any gateway request. Direct service construction
SHALL also default to read-only. Health SHALL expose the configured mode.

Ordinary `READ_ONLY` operation SHALL remain independent of the execution journal.
In `READ_ONLY` mode the system SHALL NOT open, create or require any journal
database, and SHALL NOT fail for want of one.

A `SIMULATE` mutation SHALL be admitted under an explicit `SIMULATE` or `REAL`
policy, using the same dedicated persistent paper database across mode changes.
`REAL` permits real execution in addition to paper execution; it does not change
the environment or storage identity of a paper operation. REAL-order journaling
is deferred to a later stage and SHALL NOT write into the paper database.
When journal storage is required but unavailable or blocked, the mutation SHALL be
refused. The system SHALL NOT fall back to dispatching it unjournaled.

Paper journal blocking SHALL be independent of Stage 1's REAL relock halt
(`ARMED`/`HALTED`). Calling `lock_trade` SHALL NOT clear paper journal failures.

The trading mode SHALL be determined by configuration alone. No tool argument,
including the environment and account arguments a caller must supply, SHALL elevate
the configured mode or enable `REAL` execution.

#### Scenario: Read-only deployment

- **WHEN** any trading mutation or unlock is requested in READ_ONLY mode
- **THEN** the service rejects it before contacting the gateway while allowing
  reads and previews subject to gateway permissions.

#### Scenario: Read-only deployment needs no journal

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the server starts and serves read operations
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
- **THEN** policy permits the requested environment, subject to gateway checks,
  without silently changing the environment.

#### Scenario: Password does not enable trading mode

- **WHEN** a password is configured in READ_ONLY or SIMULATE mode
- **THEN** startup does not unlock trading or elevate the configured mode.

#### Scenario: Reject invalid configuration

- **WHEN** the mode is not one of the documented values
- **THEN** startup fails with a configuration error rather than selecting REAL.

## ADDED Requirements

### Requirement: Simulated Account Allowlist

When `MOOMOO_TRADING_MODE` is `SIMULATE`, or paper execution is configured in
`REAL` mode, the system SHALL require an explicit allowlist of simulated account
identifiers and a journal path. A REAL deployment without paper configuration
SHALL refuse paper mutations rather than dispatch them without a journal. If it is missing, empty or malformed,
startup SHALL fail with a configuration error naming the variable.

Every journaled mutation SHALL target an account that is on the allowlist and has
been verified to be a simulated account. A mutation that names, or resolves to, any
other account SHALL be refused before any gateway mutation. An explicitly
unallowlisted identifier SHALL be refused before account discovery.

A journaled mutation SHALL state its account and environment explicitly. Where an
account is resolved rather than named, it SHALL resolve only when exactly one
allowlisted simulated account is eligible, following the rule in `order-placement` ›
Resolve Order Account Explicitly.

#### Scenario: SIMULATE mode without an allowlist

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE`
- **WHEN** the simulated-account allowlist is unset or empty
- **THEN** startup SHALL fail with a configuration error naming the variable

#### Scenario: Mutation to an un-allowlisted account is refused

- **GIVEN** the allowlist names one simulated account
- **WHEN** a journaled mutation names a different account
- **THEN** the system SHALL refuse it before any gateway request
- **AND** the refusal SHALL state that the account is not on the simulated-account
  allowlist

#### Scenario: An account that is not simulated is refused

- **GIVEN** an account identifier appears on the allowlist
- **WHEN** it cannot be verified as a simulated account
- **THEN** the system SHALL refuse mutations against it before any gateway mutation

#### Scenario: Paper records are never promoted to live execution

- **GIVEN** operations recorded in the paper journal
- **WHEN** REAL-order journaling is implemented in a later stage
- **THEN** it SHALL require separate, distinct storage
- **AND** paper journal records SHALL NOT be recognized, resumed or promoted into
  live orders

### Requirement: Version 1 Paper Execution Scope

Version 1 journaled paper execution SHALL support only:

- US stock and ETF instruments;
- whole-share quantities;
- `BUY` and `SELL`;
- `NORMAL` limit orders;
- `DAY` time in force;
- regular trading hours;
- price and total-quantity modifications;
- individual cancellations.

Any other mutation SHALL be refused explicitly as out of scope for paper execution.
It SHALL NOT be dispatched unjournaled, and SHALL NOT be silently adapted into a
supported form.

#### Scenario: An unsupported order type is refused, not bypassed

- **GIVEN** journaled paper execution is active
- **WHEN** a market, stop, trailing-stop or combo mutation is requested
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
- **AND** SHALL NOT dispatch it unjournaled

#### Scenario: An unsupported time in force is refused

- **WHEN** a journaled paper placement requests a time in force other than `DAY`
- **THEN** the system SHALL refuse it before any gateway request
- **AND** SHALL NOT substitute `DAY` in its place

#### Scenario: An unsupported instrument or quantity is refused

- **WHEN** a journaled paper mutation names an instrument outside US stocks and ETFs,
  or a quantity that is not a whole number of shares
- **THEN** the system SHALL refuse it before any gateway mutation

#### Scenario: Paper journal survives deployment mode changes

- **GIVEN** an acknowledged paper operation persisted under SIMULATE mode
- **WHEN** the same database is reopened under REAL mode and its token is retried
- **THEN** the stored paper outcome SHALL be returned without another dispatch
- **AND** new eligible paper operations SHALL use that same database
- **AND** returning to SIMULATE SHALL retain all paper records
- **AND** READ_ONLY SHALL leave the database intact without opening it
