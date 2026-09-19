## MODIFIED Requirements

### Requirement: Support Placing Orders

The system MUST allow placing orders with `code`, `side`, `qty`, `price`, `order_type`, and `trd_env`.
Supported order types include: `NORMAL` (Limit), `MARKET`, `STOP`, `STOP_LIMIT`, `TRAILING_STOP`, `TRAILING_STOP_LIMIT`, `AUCTION`, `AUCTION_LIMIT`, etc.
In paper trading (`SIMULATE` mode), every order placement SHALL additionally require a non-empty `operation_id`, SHALL be durably recorded in the execution journal before gateway dispatch, and version 1 SHALL support only stock and ETF limit orders (`order_type='NORMAL'`). Unsupported order types in paper mode SHALL be refused.

#### Scenario: Place Limit Buy Order
- **GIVEN** the user wants to buy 100 shares of HK.00700 at 350.0 in `SIMULATE` mode
- **WHEN** they call `place_order(operation_id='op-buy-001', code='HK.00700', trd_side='BUY', qty=100, price=350.0, order_type='NORMAL', trd_env='SIMULATE')`
- **THEN** the operation SHALL be committed to the execution journal in state `PENDING_SUBMIT`
- **AND** the order SHALL be submitted to the OpenD gateway
- **AND** the tool SHALL return the broker order ID along with the execution journal operation status.

#### Scenario: Place Market Buy Order
- **GIVEN** the user wants to buy 100 shares of US.AAPL at market price in `SIMULATE` mode
- **WHEN** they call `place_order(operation_id='op-buy-002', code='US.AAPL', trd_side='BUY', qty=100, price=0.0, order_type='MARKET', trd_env='SIMULATE')`
- **THEN** the request SHALL be rejected before contacting the gateway
- **AND** the error message SHALL explain that version 1 paper execution supports limit orders (`NORMAL`) only.

#### Scenario: Duplicate placement returns stored receipt without resubmission
- **GIVEN** `place_order` was previously called with `operation_id='op-buy-001'` and returned broker order ID `moo-1001`
- **WHEN** the client re-sends `place_order(operation_id='op-buy-001', code='HK.00700', trd_side='BUY', qty=100, price=350.0, order_type='NORMAL', trd_env='SIMULATE')`
- **THEN** the system SHALL NOT submit a second order to the OpenD gateway
- **AND** the tool SHALL return the stored receipt for `moo-1001`.
