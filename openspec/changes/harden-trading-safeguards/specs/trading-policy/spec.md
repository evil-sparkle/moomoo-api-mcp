# Spec Delta

## MODIFIED Requirements

### Requirement: Order Safety Guardrails

The trading policy SHALL support optional upper bounds on a single order's quantity
and on its notional value. These bounds protect against erroneous or runaway
automated orders. Limit enforcement SHALL fail closed: when a configured limit
cannot be evaluated for an order, the order SHALL be refused, not permitted.

**Configuration**

- `MOOMOO_MAX_ORDER_QTY` SHALL be a finite number greater than zero.
- `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` SHALL be a comma-separated list of
  `CURRENCY:AMOUNT` entries, for example `USD:25000,HKD:200000`.
  - Each currency SHALL be a three-letter alphabetic code that appears at most once.
  - Each amount SHALL be a finite number greater than zero.
- Any other value SHALL fail startup with a configuration error. This includes
  `nan`, `inf`, zero, negative numbers, an entry without a currency, and a duplicate
  currency.
- Constructing the policy directly SHALL apply the same validation.

**Legacy variable**

`MOOMOO_MAX_ORDER_NOTIONAL` is the legacy, unit-less cap. This version SHALL NOT use
it as a limit.

- If both variables are set, the legacy variable SHALL be ignored, and startup SHALL
  log that it is ignored.
- If only the legacy variable is set, startup SHALL fail with a configuration error.
  The error SHALL explain that a cap without a currency cannot be applied, and SHALL
  name `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`.

**Quantity limit**

The quantity checked against `MOOMOO_MAX_ORDER_QTY` SHALL be:

- the order quantity, for a single-leg order;
- the largest leg quantity (`qty × qty_ratio`), for a combo order.

**Notional limit**

When a notional cap is configured, the order's notional value SHALL be:

```
reference price × quantity × contract multiplier
```

It SHALL be expressed in the currency of the instrument's market.

- **Contract multiplier.** `1` for stocks and ETFs, and the broker-reported contract
  size for options.
- **Market reference (`M`).** The largest finite, positive value among the
  instrument snapshot's `last_price`, `bid_price` and `ask_price`, used as returned.
  If none is finite and positive, `M` is unavailable. This version applies no
  staleness threshold.
- **Order classes.**
  - *Fixed-limit* order types are those listed under Validate Order Values.
  - *No-fixed-limit* order types are `MARKET`, `AUCTION`, `STOP`,
    `MARKET_IF_TOUCHED`, `TRAILING_STOP` and `TRAILING_STOP_LIMIT`.
  - Any other order type SHALL be refused while a notional cap is configured.

For a single-leg order, the reference price SHALL be determined as follows:

| Side | Order class | Reference price | Needs `M` |
| --- | --- | --- | --- |
| BUY | fixed-limit | the order's limit `price` | no |
| SELL | fixed-limit | max(`price`, `M`) | yes |
| BUY or SELL | no-fixed-limit | max(`M`, `aux_price` if positive, `price` if positive) | yes |

The limit price is used on its own only for a BUY fixed-limit order, because only
there does it bound the fill price from above.

For a combo order:

- The notional SHALL be the package premium:
  `|net price| × package quantity × the legs' common contract size`.
- The system SHALL describe this value as package premium, not as maximum loss.
- Only fixed-limit order types are assessable.

While a notional cap is configured, the order SHALL be refused if any of these
holds:

- the instrument's snapshot, security type or contract size cannot be obtained;
- the security type is not a stock, ETF or option, or the market is not supported;
- the rule above needs `M` and `M` is unavailable;
- the instrument's currency has no configured cap;
- a combo uses a no-fixed-limit order type, mixes contract sizes, or contains a
  stock leg.

A modification SHALL be assessed with the same rule. The side, order type and trigger
price come from the existing order, and a fresh snapshot is taken.

A limit refusal SHALL occur before any order-mutating gateway request and SHALL name
the limit, the computed value and its currency, or the reason no value could be
computed.

#### Scenario: Enforce max order quantity limit

- **GIVEN** `MOOMOO_MAX_ORDER_QTY` is configured to a positive limit
- **WHEN** a single-leg order quantity, or a combo order's largest leg quantity,
  exceeds that limit
- **THEN** the service SHALL reject the order before contacting the OpenD gateway
  for any order mutation

#### Scenario: Enforce max order notional limit

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000`
- **WHEN** a BUY `NORMAL` order is requested for 5 contracts of a US option at 3.00,
  whose broker-reported contract size is 100
- **THEN** the computed notional SHALL be 1,500 USD
- **AND** the service SHALL reject the order before contacting the gateway for any
  order mutation

#### Scenario: BUY limit below the market uses its limit price

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000`
- **WHEN** a BUY `NORMAL` order for 10 shares of a US stock at 90 is requested while
  `M` is 150
- **THEN** the computed notional SHALL be 900 USD
- **AND** the limit SHALL permit the order

#### Scenario: SELL limit below the market uses the market reference

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000`
- **WHEN** a SELL `NORMAL` order for 10 shares of a US stock at 90 is requested while
  `M` is 150
- **THEN** the computed notional SHALL be 1,500 USD
- **AND** the service SHALL reject the order

#### Scenario: Missing or zero price does not bypass the notional limit

- **GIVEN** a notional cap is configured for the instrument's currency
- **WHEN** a `MARKET` order is requested with a price of 0
- **THEN** the reference price SHALL be `M`
- **AND** the service SHALL reject the order if the resulting notional exceeds the
  cap

#### Scenario: Stop trigger above the market

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000`
- **WHEN** a BUY `STOP` order for 8 shares with `aux_price` 130 is requested while
  `M` is 120
- **THEN** the computed notional SHALL be 1,040 USD
- **AND** the service SHALL reject the order

#### Scenario: Unassessable order is refused

- **GIVEN** a notional cap is configured
- **WHEN** any of these holds:
  - the instrument's snapshot cannot be retrieved;
  - its security type is not supported;
  - its currency has no configured cap;
  - the order needs `M` and `M` is unavailable;
  - the order type is in neither class
- **THEN** the service SHALL reject the order with an error stating why no notional
  could be computed
- **AND** no order-mutating gateway request SHALL be made

#### Scenario: Combo premium limit

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:500`
- **WHEN** a two-leg US option `NORMAL` combo, with contract size 100, is requested
  at a net price of -2.50 for 3 packages
- **THEN** the computed package premium SHALL be 750 USD
- **AND** the service SHALL reject the order
- **AND** the sign of the net price SHALL NOT itself be grounds for rejection

#### Scenario: Non-finite limit configuration is rejected

- **WHEN** `MOOMOO_MAX_ORDER_QTY` is `nan` or `inf`, or
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` contains `USD:nan` or `USD:inf`
- **THEN** startup SHALL fail with a configuration error naming the variable

#### Scenario: Legacy variable alongside the new one

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL` is `25000` and
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:25000`
- **WHEN** the server starts
- **THEN** it SHALL enforce `USD:25000`
- **AND** SHALL log that the legacy variable is ignored

#### Scenario: Legacy variable alone is rejected

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL` is `25000`, and
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is unset
- **WHEN** the server starts
- **THEN** startup SHALL fail with a configuration error naming
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`

#### Scenario: Unbounded orders when limits are not configured

- **GIVEN** neither `MOOMOO_MAX_ORDER_QTY` nor `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`
  is set, and `MOOMOO_MAX_ORDER_NOTIONAL` is unset
- **WHEN** an order is submitted within an authorized trading environment
- **THEN** the service SHALL permit the order without quantity or notional limit
  rejection
- **AND** SHALL NOT fetch instrument data solely for limit assessment

## ADDED Requirements

### Requirement: Validate Order Values

Before any gateway request, the service SHALL validate the numeric values of every
order placement and modification:

- A quantity SHALL be a positive integer; booleans are rejected.
- A single-leg price, trigger price, trail value and trail spread SHALL each be
  finite and not negative.
- A single-leg order of a *fixed-limit* type SHALL have a price greater than zero.
  The fixed-limit types are `NORMAL`, `ABSOLUTE_LIMIT`, `SPECIAL_LIMIT`,
  `SPECIAL_LIMIT_ALL`, `AUCTION_LIMIT`, `STOP_LIMIT` and `LIMIT_IF_TOUCHED`.
  - Other order types may carry a price of zero.
  - `TRAILING_STOP_LIMIT` is not a fixed-limit type. Its limit follows the trail.
- A combo net price SHALL be finite. Its sign is passed through unchanged.

Validation SHALL apply whether or not limits are configured.

#### Scenario: Reject a non-finite price

- **WHEN** an order is requested with a price of NaN or infinity
- **THEN** the service SHALL reject it before contacting the gateway

#### Scenario: Reject a non-positive quantity

- **WHEN** an order is requested with a quantity of 0, a negative quantity or a
  boolean
- **THEN** the service SHALL reject it before contacting the gateway

#### Scenario: Reject a zero limit price

- **WHEN** a `NORMAL` limit order is requested with a price of 0
- **THEN** the service SHALL reject it before contacting the gateway

### Requirement: REAL Account Allowlist

When `MOOMOO_TRADING_MODE` is `REAL`, the system SHALL require
`MOOMOO_REAL_ACC_IDS`. This is a comma-separated list of decimal account
identifiers. If it is missing, empty or malformed, startup SHALL fail with a
configuration error.

Every REAL order mutation SHALL target an account on this list. A mutation that
names, or resolves to, any other account SHALL be refused before any order-mutating
gateway request. The allowlist does not restrict SIMULATE writes or reads.

#### Scenario: REAL mode without an allowlist

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL`
- **WHEN** `MOOMOO_REAL_ACC_IDS` is unset or empty
- **THEN** startup SHALL fail with a configuration error naming the variable

#### Scenario: REAL write to an unlisted account

- **GIVEN** `MOOMOO_REAL_ACC_IDS` lists account A only
- **WHEN** a REAL order placement, modification or cancellation names account B
- **THEN** the service SHALL refuse it before any order-mutating gateway request

#### Scenario: SIMULATE writes are unaffected

- **GIVEN** `MOOMOO_TRADING_MODE` is `REAL` with an allowlist
- **WHEN** a SIMULATE order is placed against a simulated account
- **THEN** the allowlist SHALL NOT cause a refusal
