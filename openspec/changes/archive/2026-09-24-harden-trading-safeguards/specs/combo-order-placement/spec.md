# Spec Delta

## MODIFIED Requirements

### Requirement: Select An Account For Combo Orders

The system SHALL apply the same account resolution to combo placement and combo
preview as to single-leg placement.

- When `acc_id` is `"0"`, the service SHALL select an account only when exactly one
  eligible account is authorized for the legs' market. In REAL, an account is
  eligible only if it is allowlisted.
- If no account is eligible, or more than one is, the request SHALL be refused.
- A successful placement result SHALL name the `acc_id` and `trd_env` used.
- `trd_env` SHALL be supplied explicitly on `place_combo_order`; it has no default.

#### Scenario: Resolve account from leg market

- **GIVEN** `acc_id` is not supplied, the legs are `US.` codes, and exactly one
  eligible account is authorized for `US`
- **WHEN** they call `place_combo_order`
- **THEN** that account SHALL be selected
- **AND** the order SHALL be submitted against that account

#### Scenario: Ambiguous combo account

- **GIVEN** `acc_id` is not supplied and two eligible accounts are authorized for the
  legs' market
- **WHEN** they call `place_combo_order`
- **THEN** the service SHALL refuse it before any order-mutating gateway request

## ADDED Requirements

### Requirement: Combo Limits Measure Leg Quantity and Premium

Combo placement SHALL be subject to the trading policy's order guardrails, measured
as follows:

- The quantity limit SHALL apply to the largest leg quantity, `qty × qty_ratio`.
- The notional limit SHALL apply to the package premium:
  `|net price| × qty × common monetary multiplier`, using the same verified monetary
  multiplier as a single-leg order. Deliverable contract size and monetary multiplier
  are distinct broker fields and SHALL NOT be used interchangeably.

The tool description SHALL state that package premium is not maximum loss. It SHALL
also state that, while a notional cap is configured, these combos are refused:

- combos whose premium cannot be computed;
- combos with legs of differing monetary multipliers, or differing contract sizes;
- combos that include a stock leg;
- combos that use an order type outside the fixed-limit class.

#### Scenario: Ratio raises the checked quantity

- **GIVEN** `MOOMOO_MAX_ORDER_QTY` is 5
- **WHEN** a 1:2:1 butterfly is requested with `qty=3`
- **THEN** the largest leg quantity SHALL be 6
- **AND** the service SHALL refuse the order before any order-mutating gateway
  request

#### Scenario: Market-type combo under a notional cap

- **GIVEN** a notional cap is configured
- **WHEN** a combo is requested with `order_type='MARKET'`
- **THEN** the service SHALL refuse it as not sent, explaining that a combo without a
  fixed limit price has no computable premium
