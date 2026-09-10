# trade-unlock Specification

## Purpose

Defines trading authorization policies and mechanisms for unlocking and locking the OpenD gateway, including startup auto-unlocking, explicit locking tools, and ephemeral Just-In-Time (JIT) order execution locks.

## Requirements

### Requirement: Auto-unlock Trade at Startup

The MCP server SHALL automatically unlock trade during startup if a trade password is provided via environment variable.

#### Scenario: Auto-unlock with plain text password

- **GIVEN** the environment variable `MOOMOO_TRADE_PASSWORD` is set
- **WHEN** the MCP server starts
- **THEN** the server SHALL call `unlock_trade` with the password
- **AND** log a success message indicating REAL account access is enabled

#### Scenario: Auto-unlock with MD5 password

- **GIVEN** the environment variable `MOOMOO_TRADE_PASSWORD_MD5` is set
- **AND** `MOOMOO_TRADE_PASSWORD` is NOT set
- **WHEN** the MCP server starts
- **THEN** the server SHALL call `unlock_trade` with the MD5 password
- **AND** log a success message indicating REAL account access is enabled

#### Scenario: Skip unlock when no password provided

- **GIVEN** neither `MOOMOO_TRADE_PASSWORD` nor `MOOMOO_TRADE_PASSWORD_MD5` is set
- **WHEN** the MCP server starts
- **THEN** the server SHALL skip the unlock step
- **AND** log an info message indicating SIMULATE-only mode

#### Scenario: Handle unlock failure gracefully

- **GIVEN** an environment variable is set with an invalid password
- **WHEN** the MCP server starts
- **THEN** the server SHALL log a warning about the unlock failure
- **AND** continue startup (do not crash)
- **AND** REAL account access will not be available

### Requirement: Manual Unlock Tool

The `unlock_trade` tool SHALL remain available for manual unlocking when auto-unlock is not configured or fails.

#### Scenario: Manual unlock after startup

- **GIVEN** the server started without auto-unlock (no env var set)
- **WHEN** the agent calls `unlock_trade` with a valid password
- **THEN** REAL account access is enabled for the session

### Requirement: Environment Variable Priority

When both `MOOMOO_TRADE_PASSWORD` and `MOOMOO_TRADE_PASSWORD_MD5` are set, the plain text password SHALL take precedence.

#### Scenario: Both env vars set

- **GIVEN** both `MOOMOO_TRADE_PASSWORD` and `MOOMOO_TRADE_PASSWORD_MD5` are set
- **WHEN** the MCP server starts
- **THEN** the server SHALL use `MOOMOO_TRADE_PASSWORD` (plain text)
- **AND** ignore `MOOMOO_TRADE_PASSWORD_MD5`

### Requirement: unlock_trade Tool Environment Variable Fallback

The `unlock_trade` tool SHALL automatically use environment variables for credentials if no arguments are provided.

#### Scenario: unlock_trade with no arguments and env var set

- **GIVEN** the environment variable `MOOMOO_TRADE_PASSWORD` is set
- **AND** `unlock_trade` is called with no arguments
- **WHEN** the tool executes
- **THEN** the tool SHALL use the value from `MOOMOO_TRADE_PASSWORD`
- **AND** return a success status

#### Scenario: unlock_trade with no arguments and MD5 env var set

- **GIVEN** the environment variable `MOOMOO_TRADE_PASSWORD_MD5` is set
- **AND** `MOOMOO_TRADE_PASSWORD` is NOT set
- **AND** `unlock_trade` is called with no arguments
- **WHEN** the tool executes
- **THEN** the tool SHALL use the value from `MOOMOO_TRADE_PASSWORD_MD5`
- **AND** return a success status

#### Scenario: unlock_trade with explicit arguments overrides env vars

- **GIVEN** both `MOOMOO_TRADE_PASSWORD` env var and explicit `password` argument are provided
- **WHEN** `unlock_trade` is called with the explicit argument
- **THEN** the tool SHALL use the explicit argument value
- **AND** ignore the environment variable

### Requirement: Ephemeral Just-In-Time Trade Unlock

The system SHALL automatically unlock the trading gateway immediately prior to dispatching an order mutation and SHALL automatically re-lock the gateway immediately after completion.

#### Scenario: Order execution triggers ephemeral JIT unlock and relock
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `REAL`
- **AND** `MOOMOO_TRADE_PASSWORD_MD5` is configured in the server environment
- **AND** OpenD is currently locked
- **WHEN** an order creation, modification, or cancellation operation is requested
- **THEN** the system SHALL unlock OpenD with the configured credential
- **AND** execute the order operation
- **AND** re-lock OpenD before returning the result to the caller

#### Scenario: Relock guaranteed on order execution failure
- **GIVEN** OpenD is unlocked via the JIT unlock mechanism
- **WHEN** an order operation fails or raises an unhandled exception
- **THEN** the system SHALL guarantee execution of `unlock_trade(is_unlock=False)` in a finally block
- **AND** leave OpenD in a locked state

### Requirement: Explicit Trade Lock Tool

The MCP server SHALL provide a `lock_trade` tool allowing callers to explicitly lock the trading gateway.

#### Scenario: Caller invokes lock_trade
- **GIVEN** OpenD is currently unlocked
- **WHEN** the `lock_trade` tool is invoked
- **THEN** the system SHALL issue `unlock_trade(is_unlock=False)` to OpenD
- **AND** return a success confirmation indicating trading is locked

### Requirement: Proactive Lock on Read-Only Startup

When the MCP server starts with `MOOMOO_TRADING_MODE=READ_ONLY`, it SHALL proactively assert an explicit lock on the gateway.

#### Scenario: Server starts in read-only mode
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `READ_ONLY`
- **WHEN** the MCP server initializes its trade connection to OpenD
- **THEN** the server SHALL issue `unlock_trade(is_unlock=False)` to guarantee the gateway is locked
- **AND** refuse any unlock attempts during the session
