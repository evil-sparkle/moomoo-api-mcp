# trading-policy Specification

## Purpose

Defines and enforces the global trading execution policy (READ_ONLY, SIMULATE, or REAL) to guard against unauthorized order submission, mutation, cancellation, or gateway unlocking.

## Requirements

### Requirement: Enforce Configured Trading Mode

The system SHALL accept `MOOMOO_TRADING_MODE` values `READ_ONLY`, `SIMULATE`, and
`REAL`, default to `READ_ONLY`, and reject unknown values on startup. The service
layer SHALL enforce policy for single-leg and combo placement, modification,
cancellation, and unlock before any gateway request. Direct service construction
SHALL also default to read-only. Health SHALL expose the configured mode.

#### Scenario: Read-only deployment

- **WHEN** any trading mutation or unlock is requested in READ_ONLY mode
- **THEN** the service rejects it before contacting the gateway while allowing
  reads and previews subject to gateway permissions.

#### Scenario: Simulation deployment

- **WHEN** SIMULATE mode receives a SIMULATE write
- **THEN** it may submit that write, but REAL writes and unlock remain denied.

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
