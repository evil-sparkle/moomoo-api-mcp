# Spec Delta

## RENAMED Requirements

- FROM: `### Requirement: Read-Only Gateway Lock on Connect and Reconnect`
- TO: `### Requirement: Gateway Lock at Rest on Connect and Reconnect`

## MODIFIED Requirements

### Requirement: Gateway Lock at Rest on Connect and Reconnect

The trade service SHALL issue a lock request to the gateway when its trade connection
is first established, and again each time the SDK re-establishes that connection,
when either of these holds:

- `MOOMOO_TRADING_MODE=READ_ONLY`; or
- `MOOMOO_TRADING_MODE=REAL` and a stored trade credential (`MOOMOO_TRADE_PASSWORD`
  or `MOOMOO_TRADE_PASSWORD_MD5`) is configured.

A reconnect that occurs while a just-in-time write is in progress SHALL NOT lock the
gateway underneath that write. The write's own relock covers it.

If a lock request fails, the service SHALL:

- log the failure as a warning;
- keep the connection;
- keep enforcing the trading policy in the service layer.

The gateway lock is defence in depth. The policy check, which runs before any
request reaches the gateway, is what refuses writes and unlocks. This requirement
does not guarantee that the gateway is locked after every reconnect. It guarantees
that a lock is requested and that a refusal is reported.

#### Scenario: Server starts in read-only mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the trade service establishes its connection to OpenD
- **THEN** the service SHALL issue `unlock_trade(is_unlock=False)` on that
  connection

#### Scenario: REAL mode with a stored credential starts locked

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and `MOOMOO_TRADE_PASSWORD_MD5` is set
- **WHEN** the trade service establishes or re-establishes its connection to OpenD
- **THEN** the service SHALL issue `unlock_trade(is_unlock=False)` on that
  connection
- **AND** SHALL NOT issue any unlock request as part of connecting

#### Scenario: Lock requested again after an SDK reconnect

- **GIVEN** the gateway is locked at rest under this requirement, and the trade
  connection has dropped
- **WHEN** the SDK re-establishes the connection and runs its post-reconnect work
- **THEN** the SDK's own post-reconnect work SHALL still run and its result SHALL
  be returned to the SDK unchanged
- **AND** the service SHALL issue `unlock_trade(is_unlock=False)` again

#### Scenario: Reconnect during a just-in-time write

- **GIVEN** a just-in-time REAL write holds the gateway unlocked
- **WHEN** the SDK re-establishes the connection before that write finishes
- **THEN** the reconnect SHALL NOT issue a lock request that interrupts the write
- **AND** the write's relock SHALL still be attempted when it finishes

#### Scenario: A refused lock is reported and does not cost the connection

- **GIVEN** the gateway is locked at rest under this requirement
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

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE`, or it is `REAL` with no stored
  trade credential
- **WHEN** the trade connection is established or re-established
- **THEN** the service SHALL NOT issue a lock request as part of connecting

### Requirement: Manual Unlock Tool

The `unlock_trade` tool SHALL unlock the gateway only when all of these hold:

- `MOOMOO_TRADING_MODE` is `REAL`;
- no stored trade credential is configured;
- the caller supplies a password or password hash.

When a stored credential is configured, the server manages the lock itself and
unlocks only for the duration of a write. A manual unlock would leave the gateway
unlocked indefinitely, so the tool SHALL refuse it. Outside REAL mode the tool SHALL
refuse to unlock, whatever credential is supplied. A refused unlock SHALL NOT reach
the gateway.

#### Scenario: Manual unlock after startup

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and no stored trade credential is set
- **WHEN** the agent calls `unlock_trade` with a valid password
- **THEN** the gateway SHALL be unlocked for REAL account access
- **AND** the result SHALL state that the gateway stays unlocked until `lock_trade`
  is called or the gateway restarts

#### Scenario: Manual unlock refused when a stored credential exists

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and `MOOMOO_TRADE_PASSWORD_MD5` is set
- **WHEN** the agent calls `unlock_trade`, with or without a password
- **THEN** the tool SHALL refuse the request before it reaches the gateway
- **AND** the error SHALL explain that REAL writes unlock just in time

#### Scenario: Manual unlock refused outside REAL mode

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY` or `SIMULATE`
- **WHEN** the agent calls `unlock_trade`, with or without a password
- **THEN** the tool SHALL refuse the request before it reaches the gateway
- **AND** the configured trading mode SHALL be unchanged

#### Scenario: Manual unlock without a password

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` and no stored trade credential is set
- **WHEN** the agent calls `unlock_trade` with no password and no hash
- **THEN** the tool SHALL return an error asking for a password
- **AND** no request SHALL reach the gateway

### Requirement: Environment Variable Priority

When both `MOOMOO_TRADE_PASSWORD` and `MOOMOO_TRADE_PASSWORD_MD5` are set, the plain
text password SHALL take precedence for just-in-time unlocking.

#### Scenario: Both env vars set

- **GIVEN** both `MOOMOO_TRADE_PASSWORD` and `MOOMOO_TRADE_PASSWORD_MD5` are set
- **WHEN** a REAL write unlocks just in time
- **THEN** the server SHALL use `MOOMOO_TRADE_PASSWORD` (plain text)
- **AND** ignore `MOOMOO_TRADE_PASSWORD_MD5`

### Requirement: Ephemeral Just-In-Time Trade Unlock

In REAL mode with a stored trade credential, the system SHALL unlock the gateway
immediately before an order mutation is dispatched and SHALL re-lock it immediately
afterwards. This is the only path by which this server unlocks the gateway with a
stored credential.

Mutations that use the unlock SHALL be serialized. The unlock → dispatch → relock
sequence of one mutation SHALL NOT interleave with another's.

A failed unlock SHALL be reported as not sent, and no order-mutating request SHALL be
made. A failed relock SHALL NOT change the reported outcome of the mutation, whether
that outcome is acknowledged, outcome unknown or not sent. The Execution Halt
requirement covers what happens next.

An unlock the gateway reports as unnecessary SHALL count as a successful unlock and
SHALL allow the dispatch to proceed. The system SHALL NOT assert, as a post-condition
of unlocking, that the gateway is now in an unlocked state.

#### Scenario: An unlock reported as unnecessary is a success

- **GIVEN** a REAL write with a stored credential
- **WHEN** the gateway answers the just-in-time unlock by reporting that no unlock is
  required
- **THEN** the write SHALL proceed to dispatch
- **AND** the system SHALL NOT treat the response as a failed unlock

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
- **THEN** the system SHALL attempt `unlock_trade(is_unlock=False)` before
  returning

#### Scenario: Unlock failure sends nothing

- **GIVEN** a REAL write with a stored credential
- **WHEN** the gateway rejects the just-in-time unlock
- **THEN** the write SHALL fail with an error stating that no order was sent
- **AND** no order-mutating request SHALL reach the gateway

### Requirement: Explicit Trade Lock Tool

The MCP server SHALL provide a `lock_trade` tool that lets callers explicitly lock
the trading gateway. `lock_trade` is a *lock-only request*: it issues a lock and
never an unlock.

It SHALL be serialized with just-in-time writes. It SHALL wait for an in-progress
unlock → dispatch → relock sequence to finish, and SHALL NOT lock the gateway inside
one.

Its result SHALL report whether the execution halt is still in effect afterwards
(`execution_halted`), and whether this call cleared it (`halt_cleared`).

#### Scenario: Caller invokes lock_trade

- **GIVEN** OpenD is currently unlocked
- **WHEN** the `lock_trade` tool is invoked
- **THEN** the system SHALL issue `unlock_trade(is_unlock=False)` to OpenD
- **AND** return a success confirmation indicating trading is locked

#### Scenario: lock_trade waits for an in-progress write

- **GIVEN** a just-in-time REAL cancellation holds the gateway unlocked
- **WHEN** `lock_trade` is invoked
- **THEN** its lock request SHALL be issued only after that cancellation's relock
  attempt has completed

#### Scenario: A lock the gateway refuses does not appear to succeed

- **GIVEN** the gateway refuses a lock request, including when it cannot resolve the
  account its lock path requires
- **WHEN** `lock_trade` is invoked
- **THEN** the call SHALL report the failure and the lock error
- **AND** any execution halt in effect SHALL remain in effect
- **AND** the result SHALL NOT report the halt as cleared

## ADDED Requirements

### Requirement: Execution Halt

In REAL mode with a stored trade credential, the trade service SHALL hold exactly one
of two execution states: `ARMED` or `HALTED`. The process starts `ARMED`.

**Transitions**

| From | Event | To |
| --- | --- | --- |
| `ARMED` | The relock after a just-in-time write fails | `HALTED`. Record the time it began and the lock error. |
| `HALTED` | The relock after a just-in-time write fails (a cancellation permitted while halted) | `HALTED`. Keep the start time; record the latest lock error. |
| `HALTED` | `lock_trade` fails | `HALTED`. Keep the start time; record the latest lock error. |
| `HALTED` | `lock_trade` succeeds | `ARMED` |
| `ARMED` | `lock_trade` succeeds or fails | `ARMED` |

These events SHALL NOT clear the halt:

- a successful relock that ends a just-in-time write;
- a successful lock at rest after a connect or reconnect.

A just-in-time relock follows an unlock the server itself made, so its success does
not show that the condition which caused the halt has been dealt with. Recovery
therefore requires an explicit lock-only request.

**While `HALTED`**, the service SHALL refuse the following before any gateway
request:

- REAL order placements;
- REAL combo placements;
- REAL `NORMAL` and `ENABLE` modifications.

REAL `cancel_order` calls, and `CANCEL`, `DISABLE` and `DELETE` modifications, SHALL
remain permitted, so an operator can still reduce exposure. They still use the
just-in-time unlock and relock.

`SIMULATE` writes SHALL remain permitted while `HALTED`. The halt records that the
REAL gateway may still be unlocked; a `SIMULATE` write does not use the
just-in-time unlock and cannot add live exposure, so it is not what the halt
guards against. A permitted `SIMULATE` write SHALL NOT clear the halt.

**On relock failure**, the write's reported outcome SHALL be unchanged:

- An acknowledged write SHALL return its receipt, plus the lock error and
  `execution_halted: true`.
- A write that was not sent, or whose outcome is unknown, SHALL fail with its own
  error, with the lock failure appended.

**Scope.** The state is held in process memory. A new process starts `ARMED` and
locks the gateway at rest when it connects. This requirement does not define an
operator pause. A persistent pause is a separate, later capability.

#### Scenario: Relock fails after an acknowledged order

- **GIVEN** the service is `ARMED` and a REAL `place_order` is acknowledged by the
  gateway
- **WHEN** the relock that follows it fails
- **THEN** the tool SHALL return the order receipt, including the order identifier
- **AND** the result SHALL include the lock error and `execution_halted: true`
- **AND** the call SHALL NOT be reported as a failed order
- **AND** the state SHALL be `HALTED`

#### Scenario: Halt refuses new exposure

- **GIVEN** the service is `HALTED`
- **WHEN** a REAL `place_order`, `place_combo_order`, or `NORMAL` or `ENABLE`
  modification is requested
- **THEN** the service SHALL refuse it as not sent before any gateway request
- **AND** the error SHALL name the halt and `lock_trade` as the way to clear it

#### Scenario: A SIMULATE write is allowed while halted

- **GIVEN** the service is `HALTED`
- **WHEN** a `SIMULATE` `place_order` or `NORMAL` modification is requested
- **THEN** the service SHALL dispatch it without unlocking the gateway
- **AND** the state SHALL remain `HALTED`

#### Scenario: Cancellation allowed while halted

- **GIVEN** the service is `HALTED`
- **WHEN** a REAL `cancel_order` is requested
- **THEN** the service SHALL dispatch it

#### Scenario: A cancellation's successful relock does not clear the halt

- **GIVEN** the service is `HALTED`
- **WHEN** a REAL `cancel_order` completes and its relock succeeds
- **THEN** the state SHALL remain `HALTED`

#### Scenario: Reconnect lock does not clear the halt

- **GIVEN** the service is `HALTED`
- **WHEN** the SDK reconnects and the lock at rest succeeds
- **THEN** the state SHALL remain `HALTED`

#### Scenario: A successful lock_trade clears the halt

- **GIVEN** the service is `HALTED`
- **WHEN** `lock_trade` succeeds
- **THEN** the state SHALL be `ARMED`
- **AND** the result SHALL report `halt_cleared: true` and `execution_halted: false`

#### Scenario: A failed lock_trade keeps the halt

- **GIVEN** the service is `HALTED`
- **WHEN** `lock_trade` fails
- **THEN** the state SHALL remain `HALTED` with its original start time
- **AND** the latest lock error SHALL be the `lock_trade` failure

## REMOVED Requirements

### Requirement: Auto-unlock Trade at Startup

**Reason**: This was a second, implicit unlock path. It left the gateway unlocked
for the life of the connection, and the SDK replays that unlock on every reconnect.
That undermined the just-in-time lifecycle, which is the only unlock path the server
now uses with a stored credential. The SDK's `unlock_trade` fetches the account list
itself, so the startup account fetch is not needed either.

**Migration**: None required for writes. REAL writes with a stored credential unlock
just in time. Deployments without a stored credential call `unlock_trade` with a
password.

### Requirement: unlock_trade Tool Environment Variable Fallback

**Reason**: A stored credential now makes manual unlock refused, so an
environment-variable fallback for manual unlock has nothing left to fall back to.

**Migration**: With a stored credential, do nothing: writes unlock just in time.
Without one, pass `password` or `password_md5` to `unlock_trade`.
