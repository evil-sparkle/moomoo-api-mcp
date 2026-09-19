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

#### Scenario: Place Limit Buy Order

- **GIVEN** the user wants to buy 100 shares of HK.00700 at 350.0
- **WHEN** they call `place_order(code='HK.00700', trd_side='BUY', qty=100,
  price=350.0, order_type='NORMAL', trd_env='REAL', acc_id='<allowed account>')`
- **THEN** the order SHALL be submitted to Moomoo
- **AND** the tool SHALL return the order ID, the `acc_id` and the `trd_env`

#### Scenario: Place Market Buy Order

- **GIVEN** the user wants to buy 100 shares of US.AAPL at market price
- **WHEN** they call `place_order(code='US.AAPL', trd_side='BUY', qty=100,
  price=0.0, order_type='MARKET', trd_env='SIMULATE')`
- **THEN** the order SHALL be submitted
- **AND** the tool SHALL return the order ID

#### Scenario: Environment omitted

- **WHEN** an agent calls `place_order` without `trd_env`
- **THEN** the call SHALL fail as a missing required argument
- **AND** no gateway request SHALL be made

## ADDED Requirements

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
