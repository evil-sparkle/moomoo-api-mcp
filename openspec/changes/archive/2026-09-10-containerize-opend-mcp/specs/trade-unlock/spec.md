## ADDED Requirements

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
