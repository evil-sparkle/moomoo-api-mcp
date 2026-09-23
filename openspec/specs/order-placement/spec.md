# order-placement Specification

## Purpose

Provides capabilities for submitting new buy and sell orders with various order types, prices, and quantities across simulated and real environments.

## Requirements

### Requirement: Support Placing Orders

The system MUST allow placing orders with `code`, `side`, `qty`, `price`,
`order_type`, and `trd_env`.

- Supported order types include `NORMAL` (limit), `MARKET`, `STOP`, `STOP_LIMIT`,
  `TRAILING_STOP`, `TRAILING_STOP_LIMIT`, `AUCTION`, `AUCTION_LIMIT`, and others.
- `trd_env` has no default, so every placement states its environment. The service
  never infers or rewrites it.
- A successful result SHALL include the `acc_id` and `trd_env` the order was
  submitted against.

A placement that is journaled paper execution SHALL additionally:

- carry a non-empty, caller-supplied `operation_id` and a valid `admission_epoch`,
  as defined by `execution-journal` › Operation Admission and Execution Identity;
- express its price as a strict decimal string;
- have its admission and intent persisted in state `ADMITTED` before safety checks,
  and commit its dispatch marker (`DISPATCHING`) only after all safety checks pass;
- record pre-dispatch safety and limit refusals as `REFUSED` with local disposition
  `NOT_SENT` without committing a dispatch marker;
- return an immediate bounded response (`IN_FLIGHT`) if an identical request is
  retried while dispatch is currently executing;
- be restricted to the version 1 paper scope in `trading-policy` › Version 1 Paper
  Execution Scope. Order types outside that scope are refused rather than dispatched.

The result of a journaled placement SHALL report the operation's local state
alongside the broker receipt, and SHALL keep the two distinguishable.

#### Scenario: Place Limit Buy Order

- **GIVEN** the user wants to buy 100 shares of HK.00700 at 350.0
- **WHEN** they call `place_order(code='HK.00700', trd_side='BUY', qty=100,
  price=350.0, order_type='NORMAL', trd_env='REAL', acc_id='<allowed account>')`
- **THEN** the order SHALL be submitted to Moomoo
- **AND** the tool SHALL return the order ID, the `acc_id` and the `trd_env`

#### Scenario: Place Market Buy Order

- **GIVEN** the user wants to buy 100 shares of US.AAPL at market price outside
  journaled paper execution
- **WHEN** they call `place_order(code='US.AAPL', trd_side='BUY', qty=100,
  price=0.0, order_type='MARKET', trd_env='REAL', acc_id='<allowed account>')`
- **THEN** the order SHALL be submitted
- **AND** the tool SHALL return the order ID

#### Scenario: Environment omitted

- **WHEN** an agent calls `place_order` without `trd_env`
- **THEN** the call SHALL fail as a missing required argument
- **AND** no gateway request SHALL be made

#### Scenario: Journaled paper placement persists admission then checks before dispatching

- **GIVEN** journaled paper execution is active for an allowlisted simulated account
- **WHEN** a caller places a US stock `NORMAL` `DAY` order with
  `operation_id='op-p1'`, `admission_epoch='epoch-1'`, an explicit
  `trd_env='SIMULATE'`, and a decimal-string price
- **THEN** the operation SHALL be persisted in state `ADMITTED` before pre-dispatch
  limit checks run
- **AND** the dispatch marker (`DISPATCHING`) SHALL be committed only after checks
  pass, prior to the SDK mutation invocation
- **AND** the result SHALL report the broker receipt and the operation's local state
  distinguishably

#### Scenario: Pre-dispatch limit refusal records refused not sent without dispatch marker

- **GIVEN** journaled paper execution is active
- **WHEN** a caller places an order whose notional value exceeds the configured limit
- **THEN** the operation SHALL be recorded in the journal as `REFUSED` with local
  disposition `NOT_SENT`
- **AND** no dispatch marker SHALL be committed
- **AND** no gateway request SHALL be made

#### Scenario: In-flight retry returns immediate bounded response

- **GIVEN** a journaled placement `op-p2` is currently in state `DISPATCHING`
- **WHEN** the caller re-sends the identical placement with `operation_id='op-p2'`
- **THEN** the system SHALL return an immediate bounded in-flight response
- **AND** SHALL NOT block or initiate a second gateway request

#### Scenario: Market order is refused under journaled paper execution

- **GIVEN** journaled paper execution is active
- **WHEN** a caller requests `order_type='MARKET'` with `trd_env='SIMULATE'`
- **THEN** the placement SHALL be refused before any gateway request
- **AND** the error SHALL state that version 1 paper execution supports `NORMAL`
  limit orders only
- **AND** the placement SHALL NOT be dispatched unjournaled

#### Scenario: Repeated placement returns the stored receipt

- **GIVEN** a journaled placement with `operation_id='op-p1'` was admitted and
  acknowledged
- **WHEN** the caller re-sends the identical placement with `operation_id='op-p1'`
- **THEN** the system SHALL return the stored receipt
- **AND** SHALL NOT make a second SDK mutation invocation

### Requirement: Resolve Order Account Explicitly

Order placement SHALL resolve the target account before any order-mutating gateway
request, and SHALL NOT choose between several eligible accounts. When `acc_id` is
`"0"`:

- **REAL.** The service SHALL select an account only when exactly one allowlisted
  REAL account is authorized for the code's market.
- **SIMULATE.** The service SHALL select an account only when exactly one SIMULATE
  account is authorized for that market.

If no account is eligible, or more than one is, the request SHALL be refused. The
error SHALL list the eligible accounts, each identified by its last four digits.

An explicit `acc_id` SHALL be used as given, subject to the REAL account allowlist.

#### Scenario: Single eligible account

- **GIVEN** exactly one allowlisted REAL account is authorized for US
- **WHEN** a REAL US order is placed with `acc_id="0"`
- **THEN** the order SHALL be submitted against that account
- **AND** the result SHALL name that `acc_id`

#### Scenario: Ambiguous account

- **GIVEN** two allowlisted REAL accounts are authorized for US
- **WHEN** a REAL US order is placed with `acc_id="0"`
- **THEN** the service SHALL refuse it before any order-mutating gateway request
- **AND** the error SHALL ask for an explicit `acc_id`

### Requirement: Report the Dispatch Boundary

Order-mutating operations include placements, combo placements, modifications and
cancellations. Each reports exactly one of three outcomes. The server SHALL claim
that a request was sent only when the gateway acknowledged it.

- **Acknowledged.** The gateway returned a success code for the order-mutating
  request. This is an acknowledgement from the gateway. It does not mean the order
  was filled.
  - When the response can be read, the operation SHALL return it as the receipt.
  - When it cannot be read, the operation SHALL fail with an error stating three
    things: the gateway acknowledged the request, the receipt, including any order
    identifier, could not be read, and the request SHALL NOT be resent. The error
    SHALL tell the caller to find the order with `get_orders`.
- **Outcome unknown (possibly sent).** The server started the order-mutating gateway
  call and received no acknowledgement. This covers these cases:
  - the gateway returned an error code;
  - the SDK raised or timed out;
  - the call was otherwise interrupted.

  A gateway error code is treated as outcome unknown, because its text cannot
  reliably distinguish a broker rejection from a timeout or a transport failure. The
  error SHALL state that the request may have been sent and that its outcome is
  unknown, and SHALL include the gateway's message. It SHALL tell the caller to check
  `get_orders` before any retry. It SHALL NOT state that the request reached the
  gateway or the broker.
- **Not sent.** The operation was refused before the order-mutating gateway call
  started. Causes include policy, validation, limits, account resolution, the
  execution halt, a missing connection, and an unlock failure. The error SHALL state
  that no order was sent.

The system SHALL NOT retry an order-mutating request itself.

#### Scenario: Refused before dispatch

- **WHEN** a placement is refused by the notional limit
- **THEN** the error SHALL state that no order was sent

#### Scenario: Gateway error after dispatch

- **WHEN** the gateway returns an error code for a placement request
- **THEN** the error SHALL state that the request may have been sent and its outcome
  is unknown, and that `get_orders` must be checked before retrying
- **AND** the server SHALL NOT resend the request

#### Scenario: SDK exception during dispatch

- **WHEN** the SDK raises while the placement call is in progress
- **THEN** the call SHALL fail as outcome unknown, not as a rejection

#### Scenario: Unreadable acknowledged response

- **WHEN** the gateway returns a success code for a placement, but the response
  cannot be converted
- **THEN** the call SHALL fail with an error stating that the gateway acknowledged
  the request and that its receipt could not be read
- **AND** the error SHALL NOT describe the outcome as unknown or rejected
