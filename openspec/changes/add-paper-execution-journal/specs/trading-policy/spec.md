## MODIFIED Requirements

### Requirement: Enforce Configured Trading Mode

The system SHALL accept `MOOMOO_TRADING_MODE` values `READ_ONLY`, `SIMULATE`, and
`REAL`, default to `READ_ONLY`, and reject unknown values on startup. The service
layer SHALL enforce policy for single-leg placement, modification,
cancellation, and unlock before any gateway request. Direct service construction
SHALL also default to read-only. In `READ_ONLY` mode, the server SHALL NOT initialize,
open, or require any execution database or journal file. Health SHALL expose the
configured mode.

#### Scenario: Read-only deployment
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `READ_ONLY` (or omitted)
- **WHEN** any trading mutation or unlock is requested
- **THEN** the service SHALL reject it before contacting the gateway while allowing reads and previews
- **AND** the execution journal database SHALL NOT be initialized, opened, or created.

#### Scenario: Simulation deployment
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `SIMULATE`
- **WHEN** SIMULATE mode receives a SIMULATE write for an allowlisted account
- **THEN** it may submit that write through the execution journal
- **AND** REAL writes and unlock remain denied without silent rewrite to SIMULATE.

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

### Requirement: Paper Trading Account Isolation and Scope Restriction

When running in `SIMULATE` trading mode, the system SHALL enforce an explicit account allowlist and restrict mutation operations to supported version 1 paper workflows. The agent SHALL NOT be capable of elevating to `REAL` mode through any tool parameter.

#### Scenario: Refuse SIMULATE write to un-allowlisted account
- **GIVEN** `MOOMOO_SIMULATE_ACC_IDS` is configured with allowed accounts `["1001", "1002"]`
- **WHEN** an order mutation is requested targeting account `"1003"`
- **THEN** the system SHALL refuse the request before making any gateway call
- **AND** the refusal SHALL state that the account is not on the simulation allowlist.

#### Scenario: Default or missing account cannot select unauthorized account
- **GIVEN** `MOOMOO_SIMULATE_ACC_IDS` is configured with allowed accounts
- **WHEN** an order mutation request omits `acc_id` or passes `"0"`
- **THEN** the system SHALL resolve the account only if exactly one allowlisted simulation account matches the target market
- **AND** if multiple or zero allowlisted accounts match, the request SHALL be refused.

#### Scenario: Unsupported paper mutations fail closed
- **GIVEN** the server is running in `SIMULATE` paper trading mode
- **WHEN** a client calls an unsupported mutation path (such as `place_combo_order`, market order, stop order, or trailing stop order)
- **THEN** the system SHALL reject the mutation explicitly as unsupported in paper execution
- **AND** the system SHALL NOT dispatch the mutation unjournaled to the gateway.

#### Scenario: Paper records cannot be promoted to live execution
- **GIVEN** execution records stored in the paper journal
- **WHEN** a future live-trading deployment is configured
- **THEN** the live trading engine SHALL require separate, distinct execution storage
- **AND** paper execution journal records SHALL NEVER be recognized, resumed, or promoted into live orders.
