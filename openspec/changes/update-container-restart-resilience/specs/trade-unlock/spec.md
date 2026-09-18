## RENAMED Requirements

- FROM: `### Requirement: Proactive Lock on Read-Only Startup`
- TO: `### Requirement: Read-Only Gateway Lock on Connect and Reconnect`

## MODIFIED Requirements

### Requirement: Read-Only Gateway Lock on Connect and Reconnect

When `MOOMOO_TRADING_MODE=READ_ONLY`, the trade service SHALL issue a lock
request to the gateway when its trade connection is first established, and
again each time the SDK re-establishes that connection. If a lock request
fails, the service SHALL log the failure as a warning, SHALL keep the
connection, and SHALL keep enforcing the read-only policy in the service layer.
The gateway lock is defence in depth. The policy check, which runs before any
request reaches the gateway, is what refuses writes and unlocks. This
requirement does not guarantee that the gateway is locked after every
reconnect, only that a lock is requested and that a refusal is reported.

#### Scenario: Server starts in read-only mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the trade service establishes its connection to OpenD
- **THEN** the service SHALL issue `unlock_trade(is_unlock=False)` on that
  connection

#### Scenario: Lock requested again after an SDK reconnect

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY` and the trade connection has
  dropped
- **WHEN** the SDK re-establishes the connection and runs its post-reconnect work
- **THEN** the SDK's own post-reconnect work SHALL still run and its result SHALL
  be returned to the SDK unchanged
- **AND** the service SHALL issue `unlock_trade(is_unlock=False)` again

#### Scenario: A refused lock is reported and does not cost the connection

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the gateway refuses the lock request on connect or after a reconnect
- **THEN** the service SHALL log a warning identifying the failure
- **AND** SHALL NOT raise into the SDK's connect or reconnect thread
- **AND** SHALL keep the connection available for read-only requests

#### Scenario: Writes and unlocks stay refused whatever the gateway lock state

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`, whether or not the most recent
  lock request succeeded
- **WHEN** a caller requests an order placement, modification, cancellation or
  an `unlock_trade`
- **THEN** the service SHALL refuse the request before it reaches the gateway
- **AND** SHALL refuse it for the lifetime of the process

#### Scenario: Other modes do not assert a lock on connect

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE` or `REAL`
- **WHEN** the trade connection is established or re-established
- **THEN** the service SHALL NOT issue a lock request as part of connecting
- **AND** REAL-mode order commands SHALL rely on the just-in-time unlock and
  re-lock instead

### Requirement: Auto-unlock Trade at Startup

When `MOOMOO_TRADING_MODE=REAL` and a trade password is provided through the
environment, the MCP server SHALL unlock trade when it opens its gateway
connections, provided the trade connection is established within the connect
wait. The server opens its connections once per process, on the first request
it serves. A configured password SHALL NOT unlock trading in any other mode,
SHALL NOT change the configured mode, and a failed unlock SHALL NOT stop the
server.

#### Scenario: Auto-unlock with plain text password

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** the environment variable `MOOMOO_TRADE_PASSWORD` is set
- **WHEN** the MCP server opens its gateway connections and the trade connection
  is established
- **THEN** the server SHALL call `unlock_trade` with the password
- **AND** log a success message indicating REAL account access is enabled

#### Scenario: Auto-unlock with MD5 password

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** the environment variable `MOOMOO_TRADE_PASSWORD_MD5` is set
- **AND** `MOOMOO_TRADE_PASSWORD` is NOT set
- **WHEN** the MCP server opens its gateway connections and the trade connection
  is established
- **THEN** the server SHALL call `unlock_trade` with the MD5 password
- **AND** log a success message indicating REAL account access is enabled

#### Scenario: A password does not unlock outside REAL mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY` or `SIMULATE`
- **AND** `MOOMOO_TRADE_PASSWORD` or `MOOMOO_TRADE_PASSWORD_MD5` is set
- **WHEN** the MCP server opens its gateway connections
- **THEN** the server SHALL NOT call `unlock_trade`
- **AND** the configured trading mode SHALL be unchanged

#### Scenario: Skip unlock when no password provided

- **GIVEN** neither `MOOMOO_TRADE_PASSWORD` nor `MOOMOO_TRADE_PASSWORD_MD5` is set
- **WHEN** the MCP server opens its gateway connections
- **THEN** the server SHALL skip the unlock step
- **AND** log an info message naming the configured trading mode, which SHALL
  be unchanged

#### Scenario: Skip unlock when the trade connection is not yet established

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and a trade password is set
- **WHEN** the MCP server opens its gateway connections but the trade connection
  is not established within the connect wait
- **THEN** the server SHALL skip the startup unlock and continue serving
- **AND** REAL order commands SHALL still unlock just in time

#### Scenario: Handle unlock failure gracefully

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and a trade password is set that the
  gateway rejects
- **WHEN** the MCP server opens its gateway connections
- **THEN** the server SHALL log a warning about the unlock failure
- **AND** continue serving (do not crash)
- **AND** the configured trading mode SHALL be unchanged

### Requirement: Manual Unlock Tool

The `unlock_trade` tool SHALL remain available for manual unlocking in REAL
mode when auto-unlock is not configured or fails. Outside REAL mode it SHALL
refuse to unlock, whatever credential is supplied.

#### Scenario: Manual unlock after startup

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** the server did not auto-unlock
- **WHEN** the agent calls `unlock_trade` with a valid password
- **THEN** the gateway SHALL be unlocked for REAL account access

#### Scenario: Manual unlock refused outside REAL mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY` or `SIMULATE`
- **WHEN** the agent calls `unlock_trade`, with or without a password
- **THEN** the tool SHALL refuse the request before it reaches the gateway
- **AND** the configured trading mode SHALL be unchanged

### Requirement: unlock_trade Tool Environment Variable Fallback

In REAL mode, the `unlock_trade` tool SHALL use credentials from environment
variables when no arguments are provided. The fallback only chooses which
credential to use. It SHALL NOT allow an unlock that the configured mode
forbids.

#### Scenario: unlock_trade with no arguments and env var set

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** the environment variable `MOOMOO_TRADE_PASSWORD` is set
- **AND** `unlock_trade` is called with no arguments
- **WHEN** the tool executes
- **THEN** the tool SHALL use the value from `MOOMOO_TRADE_PASSWORD`
- **AND** return a success status

#### Scenario: unlock_trade with no arguments and MD5 env var set

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** the environment variable `MOOMOO_TRADE_PASSWORD_MD5` is set
- **AND** `MOOMOO_TRADE_PASSWORD` is NOT set
- **AND** `unlock_trade` is called with no arguments
- **WHEN** the tool executes
- **THEN** the tool SHALL use the value from `MOOMOO_TRADE_PASSWORD_MD5`
- **AND** return a success status

#### Scenario: unlock_trade with explicit arguments overrides env vars

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **AND** both `MOOMOO_TRADE_PASSWORD` env var and explicit `password` argument
  are provided
- **WHEN** `unlock_trade` is called with the explicit argument
- **THEN** the tool SHALL use the explicit argument value
- **AND** ignore the environment variable

#### Scenario: Environment credentials do not bypass the mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY` or `SIMULATE`
- **AND** a trade password environment variable is set
- **WHEN** `unlock_trade` is called with no arguments
- **THEN** the tool SHALL refuse the request before it reaches the gateway
