## MODIFIED Requirements

### Requirement: Proactive Lock on Read-Only Startup

When the MCP server runs with `MOOMOO_TRADING_MODE=READ_ONLY`, it SHALL proactively assert an explicit lock on the gateway on every connection it establishes — the first one at startup and every SDK reconnection thereafter.

A reconnection is the case that matters: the SDK replays cached state after reconnecting, and the gateway on the other side may be a restarted process whose unlock state this server never observed. Asserting the lock again is what keeps the READ_ONLY guarantee true across an OpenD restart rather than only at startup.

Asserting the lock SHALL NOT be able to fail the connection. A gateway that refuses the lock SHALL be logged and left alone: this lock is defence in depth behind the policy check that already refuses every mutating call in READ_ONLY mode, and the re-assertion runs on an SDK callback thread where an exception would propagate somewhere unhelpful.

#### Scenario: Server starts in read-only mode
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `READ_ONLY`
- **WHEN** the MCP server initializes its trade connection to OpenD
- **THEN** the server SHALL issue `unlock_trade(is_unlock=False)` to guarantee the gateway is locked
- **AND** refuse any unlock attempts for the lifetime of the process

#### Scenario: Lock re-asserted after an SDK reconnect
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `READ_ONLY`
- **AND** the trade connection to OpenD has been established
- **WHEN** the SDK reconnects that socket, for any reason including an OpenD restart
- **THEN** the server SHALL issue `unlock_trade(is_unlock=False)` again on the reconnected connection
- **AND** SHALL do so without any caller above it observing that a reconnect happened

#### Scenario: A gateway that refuses the lock does not break the connection
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `READ_ONLY`
- **WHEN** the lock assertion fails or raises, at startup or after a reconnect
- **THEN** the server SHALL log a warning naming when the attempt was made
- **AND** SHALL NOT raise out of the connection or reconnect path
- **AND** SHALL continue refusing mutating calls by policy regardless

#### Scenario: Lock is not asserted outside read-only mode
- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` or `SIMULATE`
- **WHEN** the server connects or reconnects to OpenD
- **THEN** the server SHALL NOT assert this lock
- **AND** the just-in-time unlock mechanism SHALL remain the only thing that changes gateway lock state

## ADDED Requirements

### Requirement: Serialized Unlock Windows

The server SHALL hold at most one just-in-time unlock window open at a time. A second order operation that needs an unlock SHALL wait for the window in progress to close rather than open a second one.

This guarantee is about the server's own behaviour and holds whether OpenD's unlock state turns out to be gateway-wide or per-connection. It exists because the alternative is a race with a bad shape: one operation's re-lock landing inside another operation's window, leaving the gateway either locked under a caller that expected it open, or open after every caller believed it closed.

#### Scenario: Concurrent order operations do not interleave unlock windows
- **GIVEN** `MOOMOO_TRADING_MODE` is set to `REAL` and unlock credentials are configured
- **WHEN** two order operations that each require an unlock are dispatched concurrently
- **THEN** the second SHALL wait until the first has unlocked, executed, and re-locked
- **AND** neither operation SHALL observe the other's re-lock inside its own window

#### Scenario: Window closes even when the operation fails
- **GIVEN** an unlock window is open for an order operation
- **WHEN** that operation raises
- **THEN** the server SHALL re-lock the gateway before releasing the window
- **AND** a waiting operation SHALL then proceed with its own unlock

#### Scenario: One shared trade connection serves every client
- **GIVEN** two MCP clients are connected to the same server process
- **WHEN** each places an order requiring an unlock
- **THEN** both SHALL use the same trade connection
- **AND** their unlock windows SHALL be serialized against each other rather than run independently
