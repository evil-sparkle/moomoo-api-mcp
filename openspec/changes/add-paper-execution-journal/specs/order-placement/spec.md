# Spec Delta

## MODIFIED Requirements

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

- carry a non-empty, caller-supplied `operation_id`, as defined by
  `execution-journal` › Operation Admission and Execution Identity;
- express its price as a decimal string;
- be admitted, and have its intent and dispatch marker committed, before the SDK
  mutation invocation;
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

#### Scenario: Journaled paper placement commits before dispatch

- **GIVEN** journaled paper execution is active for an allowlisted simulated account
- **WHEN** a caller places a US stock `NORMAL` `DAY` order with
  `operation_id='op-p1'`, an explicit `trd_env='SIMULATE'`, and a decimal-string
  price
- **THEN** the operation SHALL be committed to the journal before the SDK mutation
  invocation
- **AND** the result SHALL report the broker receipt and the operation's local state
  distinguishably

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
