# trading-policy Specification

## Purpose

Defines and enforces the global trading execution policy (READ_ONLY, SIMULATE, or REAL) to guard against unauthorized order submission, mutation, cancellation, or gateway unlocking.

## Requirements

### Requirement: Enforce Configured Trading Mode

The system SHALL accept `MOOMOO_TRADING_MODE` values `READ_ONLY`, `SIMULATE`, and
`REAL`, default to `READ_ONLY`, and reject unknown values on startup. The service
layer SHALL enforce policy for single-leg and combo placement, modification,
cancellation, and unlock before any gateway request. Direct service construction
SHALL also default to read-only. Health SHALL expose the configured mode.

Ordinary `READ_ONLY` operation SHALL remain independent of the execution journal.
In `READ_ONLY` mode the system SHALL NOT open, create or require any journal
database, and SHALL NOT fail for want of one.

A `SIMULATE` mutation SHALL be admitted under an explicit `SIMULATE` or `REAL`
policy, using the same dedicated persistent paper database across mode changes.
`REAL` permits real execution in addition to paper execution; it does not change
the environment or storage identity of a paper operation. REAL-order journaling
is deferred to a later stage and SHALL NOT write into the paper database.
When journal storage is required but unavailable or blocked, the mutation SHALL be
refused. The system SHALL NOT fall back to dispatching it unjournaled.

Paper journal blocking SHALL be independent of Stage 1's REAL relock halt
(`ARMED`/`HALTED`). Calling `lock_trade` SHALL NOT clear paper journal failures.

The trading mode SHALL be determined by configuration alone. No tool argument,
including the environment and account arguments a caller must supply, SHALL elevate
the configured mode or enable `REAL` execution.

#### Scenario: Read-only deployment

- **WHEN** any trading mutation or unlock is requested in READ_ONLY mode
- **THEN** the service rejects it before contacting the gateway while allowing
  reads and previews subject to gateway permissions.

#### Scenario: Read-only deployment needs no journal

- **GIVEN** `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the server starts and serves read operations
- **THEN** it SHALL NOT open, create or require a journal database
- **AND** the absence of journal storage SHALL NOT cause a failure

#### Scenario: Simulation deployment

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE`
- **WHEN** SIMULATE mode receives a SIMULATE write for an allowlisted simulated
  account, with an explicit environment and a caller-supplied operation identifier
  and valid admission epoch
- **THEN** it may submit that write through the execution journal
- **AND** REAL writes and unlock remain denied, without silent rewrite to SIMULATE.

#### Scenario: Paper journal blocking is independent of REAL relock halt

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE` and paper execution is in state
  `JOURNAL_BLOCKED`
- **WHEN** `lock_trade` is called
- **THEN** the call SHALL NOT clear `JOURNAL_BLOCKED`
- **AND** paper mutations SHALL continue to be refused

#### Scenario: Explicit real deployment

- **WHEN** REAL mode receives a valid SIMULATE or REAL write
- **THEN** policy permits the requested environment, subject to gateway checks,
  without silently changing the environment.

#### Scenario: Password does not enable trading mode

- **WHEN** a password is configured in READ_ONLY or SIMULATE mode
- **THEN** startup does not unlock trading or elevate the configured mode.

#### Scenario: Reject invalid configuration

- **WHEN** the mode is not one of the documented values
- **THEN** startup fails with a configuration error rather than selecting REAL.

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

It SHALL be expressed in the instrument's currency. The system SHALL establish that
currency only for markets it has verified quote in a single known currency, and SHALL
NOT infer an instrument's currency from its market prefix outside that verified set.

- **Instrument facts.** The system SHALL obtain, for each code the order names:
  the security classification, the monetary multiplier, and the instrument's quote
  prices. The classification SHALL come from the broker's instrument reference data,
  requested with an explicit list of the codes being valued. Quote prices SHALL come
  from the instrument snapshot. Both reads SHALL succeed for the instrument to be
  assessable.
- **Quote normalization.** Before assessment, each quote field SHALL be reduced to a
  number or to nothing. A value that is absent, non-numeric, non-finite, or not
  greater than zero SHALL be treated as absent. The assessment SHALL NOT perform
  arithmetic or a finiteness test on a non-numeric value.
- **Monetary multiplier.** The multiplier SHALL convert a quoted price into money for
  one unit of quantity. It SHALL be `1` for stocks and ETFs. For options it SHALL be
  the broker-reported `option_contract_multiplier`, whose monetary semantics have
  been verified. Missing or unusable values SHALL be refused without substituting
  `option_contract_size` or a hardcoded value. Any other classification SHALL be
  refused.
- **Market reference (`M`).** The largest of the normalized quote prices. When no
  normalized price remains, `M` is unavailable. This version applies no staleness
  threshold.
- **Order classes.**
  - *Fixed-limit* order types are those listed under Validate Order Values.
  - *No-fixed-limit* order types are `MARKET`, `AUCTION`, `STOP`,
    `MARKET_IF_TOUCHED`, `TRAILING_STOP` and `TRAILING_STOP_LIMIT`.
  - Together these cover every order type this system submits. Algorithmic variants
    the SDK defines for query purposes only are not submission types and are not
    classified.
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
  `|net price| × package quantity × the legs' common monetary multiplier`, using the
  same verified monetary multiplier as a single-leg order. Deliverable contract size
  and monetary multiplier are distinct broker fields and SHALL NOT be used
  interchangeably.
- The system SHALL describe this value as package premium, not as maximum loss.
- Only fixed-limit order types are assessable.

While a notional cap is configured, the order SHALL be refused if any of these
holds:

- any required valuation fact cannot be established: the quote snapshot, the
  security classification, or the monetary multiplier;
- the classification is not a stock, ETF or option, or the instrument's currency
  cannot be established from the verified market set;
- the rule above needs `M` and `M` is unavailable;
- the instrument's currency has no configured cap;
- a combo uses a no-fixed-limit order type, mixes monetary multipliers or contract
  sizes, or contains a
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
  whose broker-reported monetary multiplier is 100
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
  - the instrument's quote snapshot cannot be retrieved;
  - its security classification cannot be retrieved;
  - its classification is not supported;
  - its monetary multiplier cannot be established;
  - its currency cannot be established from the verified market set;
  - its currency has no configured cap;
  - the order needs `M` and `M` is unavailable;
  - the order type is in neither class
- **THEN** the service SHALL reject the order with an error stating why no notional
  could be computed
- **AND** no order-mutating gateway request SHALL be made
- **AND** the refusal SHALL be reported as not sent

#### Scenario: Non-numeric quote fields do not by themselves prevent assessment

- **GIVEN** a notional cap is configured
- **AND** the instrument's snapshot returns a non-numeric placeholder for `bid_price`
  and `ask_price`, and a usable `last_price`
- **WHEN** an order requiring `M` is assessed
- **THEN** those placeholders SHALL be treated as absent rather than compared or
  computed with
- **AND** the assessment SHALL NOT raise a type error
- **AND** `M` SHALL be the remaining `last_price`, and the assessment SHALL continue

#### Scenario: No usable quote price refuses the order

- **GIVEN** a notional cap is configured
- **AND** no finite, positive numeric value remains among `last_price`, `bid_price`
  and `ask_price` after normalization
- **WHEN** an order whose rule requires `M` is assessed
- **THEN** the assessment SHALL NOT raise a type error
- **AND** the order SHALL be refused as not sent, naming the missing market reference

#### Scenario: Classification is requested for the codes being valued

- **GIVEN** a notional cap is configured
- **WHEN** an order's instrument is assessed
- **THEN** the security classification SHALL be requested with an explicit list of the
  codes being valued
- **AND** an instrument whose classification is not returned SHALL be refused as not
  sent

#### Scenario: An instrument outside the verified currency set is refused

- **GIVEN** a notional cap is configured
- **WHEN** an order names an instrument on a market that has not been verified to
  quote in a single known currency
- **THEN** the service SHALL refuse the order as not sent
- **AND** SHALL NOT value it by assuming a currency from the market prefix

#### Scenario: Combo premium limit

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:500`
- **WHEN** a two-leg US option `NORMAL` combo, with a common monetary multiplier of 100, is requested
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

### Requirement: Simulated Account Allowlist

When `MOOMOO_TRADING_MODE` is `SIMULATE`, or paper execution is configured in
`REAL` mode, the system SHALL require an explicit allowlist of simulated account
identifiers and a journal path. A REAL deployment without paper configuration
SHALL refuse paper mutations rather than dispatch them without a journal. If it is missing, empty or malformed,
startup SHALL fail with a configuration error naming the variable.

Every journaled mutation SHALL target an account that is on the allowlist and has
been verified to be a simulated account. A mutation that names, or resolves to, any
other account SHALL be refused before any gateway mutation. An explicitly
unallowlisted identifier SHALL be refused before account discovery.

A journaled mutation SHALL state its account and environment explicitly. Where an
account is resolved rather than named, it SHALL resolve only when exactly one
allowlisted simulated account is eligible, following the rule in `order-placement` ›
Resolve Order Account Explicitly.

#### Scenario: SIMULATE mode without an allowlist

- **GIVEN** `MOOMOO_TRADING_MODE` is `SIMULATE`
- **WHEN** the simulated-account allowlist is unset or empty
- **THEN** startup SHALL fail with a configuration error naming the variable

#### Scenario: Mutation to an un-allowlisted account is refused

- **GIVEN** the allowlist names one simulated account
- **WHEN** a journaled mutation names a different account
- **THEN** the system SHALL refuse it before any gateway request
- **AND** the refusal SHALL state that the account is not on the simulated-account
  allowlist

#### Scenario: An account that is not simulated is refused

- **GIVEN** an account identifier appears on the allowlist
- **WHEN** it cannot be verified as a simulated account
- **THEN** the system SHALL refuse mutations against it before any gateway mutation

#### Scenario: Paper records are never promoted to live execution

- **GIVEN** operations recorded in the paper journal
- **WHEN** REAL-order journaling is implemented in a later stage
- **THEN** it SHALL require separate, distinct storage
- **AND** paper journal records SHALL NOT be recognized, resumed or promoted into
  live orders

### Requirement: Version 1 Paper Execution Scope

Version 1 journaled paper execution SHALL support only:

- US stock and ETF instruments;
- whole-share quantities;
- `BUY` and `SELL`;
- `NORMAL` limit orders;
- `DAY` time in force;
- regular trading hours;
- price and total-quantity modifications;
- individual cancellations.

Any other mutation SHALL be refused explicitly as out of scope for paper execution.
It SHALL NOT be dispatched unjournaled, and SHALL NOT be silently adapted into a
supported form.

#### Scenario: An unsupported order type is refused, not bypassed

- **GIVEN** journaled paper execution is active
- **WHEN** a market, stop, trailing-stop or combo mutation is requested
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
- **AND** SHALL NOT dispatch it unjournaled

#### Scenario: An unsupported time in force is refused

- **WHEN** a journaled paper placement requests a time in force other than `DAY`
- **THEN** the system SHALL refuse it before any gateway request
- **AND** SHALL NOT substitute `DAY` in its place

#### Scenario: An unsupported instrument or quantity is refused

- **WHEN** a journaled paper mutation names an instrument outside US stocks and ETFs,
  or a quantity that is not a whole number of shares
- **THEN** the system SHALL refuse it before any gateway mutation

#### Scenario: Paper journal survives deployment mode changes

- **GIVEN** an acknowledged paper operation persisted under SIMULATE mode
- **WHEN** the same database is reopened under REAL mode and its token is retried
- **THEN** the stored paper outcome SHALL be returned without another dispatch
- **AND** new eligible paper operations SHALL use that same database
- **AND** returning to SIMULATE SHALL retain all paper records
- **AND** READ_ONLY SHALL leave the database intact without opening it
