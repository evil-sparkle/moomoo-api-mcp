# Combo Order Placement

## ADDED Requirements

### Requirement: Place Multi-Leg Combo Orders

The system SHALL allow submitting a multi-leg option strategy as a single atomic order
through `place_combo_order`, accepting a list of legs — each with `code`, `trd_side`,
and `qty_ratio` — together with a net `price`, a package `qty`, an `order_type`, a
`time_in_force`, a `trd_env`, and an optional `acc_id`.

The order SHALL be submitted as one unit so that it fills completely or not at all,
leaving no partially-executed strategy.

#### Scenario: Close a vertical call spread

Given the user holds a call debit spread, long the lower strike and short the higher
When they call `place_combo_order` with legs
`[{code: 'US.XYZ260101C100000', trd_side: 'SELL', qty_ratio: 1},
  {code: 'US.XYZ260101C105000', trd_side: 'BUY', qty_ratio: 1}]`
and `price=2.50, qty=1`
Then both legs should be submitted as a single combo order at a net price of 2.50
And the tool should return the order ID.

#### Scenario: Open a spread as one package

Given the user wants to open a two-leg strategy
When they call `place_combo_order` with a `BUY` leg and a `SELL` leg and `qty=1`
Then the package should be submitted as one order
And neither leg should be executable independently of the other.

### Requirement: Require Explicit Quantity Ratios

`qty_ratio` multiplies the order quantity for its leg, such that the actual quantity
traded on a leg is `qty × qty_ratio`. Because an assumed ratio would silently submit a
different strategy, the system SHALL reject any leg that omits `qty_ratio` rather
than supplying a default.

#### Scenario: Reject a leg with no quantity ratio

Given a leg supplies `code` and `trd_side` but no `qty_ratio`
When they call `place_combo_order`
Then the call should fail with an error stating that `qty_ratio` must be stated
explicitly
And no order should reach the gateway.

#### Scenario: Reject a non-positive quantity ratio

Given a leg specifies `qty_ratio` of 0
When they call `place_combo_order`
Then the call should fail with an error stating that `qty_ratio` must be a positive
integer.

### Requirement: Support Position Identifiers For Closing Orders

The gateway requires a `position_id` on each leg when a combo order closes an existing
position. The system SHALL accept an optional `position_id` per leg and pass it through
unchanged, and SHALL omit it when not supplied so that opening orders are
unaffected.

Callers obtain these identifiers from the option strategy view of the position list,
which returns the strategy as a `COMBINED` row alongside its `LEG` rows.

#### Scenario: Closing order carries position identifiers

Given each leg supplies the `position_id` of the corresponding held leg
When they call `place_combo_order`
Then each submitted leg should carry that identifier.

#### Scenario: Opening order omits position identifiers

Given no leg supplies a `position_id`
When they call `place_combo_order`
Then the submitted legs should leave the identifier unset.

#### Scenario: Reject a malformed position identifier

Given a leg supplies a `position_id` that is not numeric
When they call `place_combo_order`
Then the call should fail with an error naming the invalid identifier
And no order should reach the gateway.

### Requirement: Validate Combo Legs Before Submission

The system SHALL reject malformed leg lists before contacting the gateway, with an
error naming the specific problem.

A valid leg list has at least two legs; every leg has a non-empty `code` and a
`trd_side` of `BUY` or `SELL`; and all legs belong to the same market.

#### Scenario: Reject a single-leg combo

Given a caller supplies only one leg
When they call `place_combo_order`
Then the call should fail with an error stating that a combo order requires at least
two legs and that `place_order` should be used for single-leg orders
And no order should reach the gateway.

#### Scenario: Reject an invalid trade side

Given a leg specifies `trd_side` of `SHORT`
When they call `place_combo_order`
Then the call should fail with an error naming the invalid side and listing `BUY` and
`SELL` as the valid values.

#### Scenario: Reject legs spanning different markets

Given one leg is a `US.` code and another is an `HK.` code
When they call `place_combo_order`
Then the call should fail with an error stating that all legs must belong to the same
market.

### Requirement: Select An Account For Combo Orders

The system SHALL apply the same account-selection behaviour as single-leg placement:
when `acc_id` is the default, an account authorised for the legs' market must be
chosen automatically.

#### Scenario: Resolve account from leg market

Given `acc_id` is not supplied and the legs are `US.` codes
When they call `place_combo_order`
Then an account authorised for the `US` market should be selected
And the order should be submitted against that account.

### Requirement: Require Explicit Confirmation For Real Combo Orders

The tool description SHALL instruct agents to obtain explicit user confirmation before
placing a combo order in the `REAL` environment, presenting every leg, the net price,
and the quantity for verification.

Because moomoo's API reference does not document a sign convention for debit versus
credit packages, the tool description SHALL NOT assert one, and SHALL direct callers
to verify pricing against the platform's own ticket before submitting a real order.

#### Scenario: Agent presents the full package before ordering

Given an agent intends to place a combo order with `trd_env='REAL'`
When it prepares the call
Then it should first display each leg's code, side, and ratio, plus the net price and
quantity, and wait for the user's explicit confirmation.
