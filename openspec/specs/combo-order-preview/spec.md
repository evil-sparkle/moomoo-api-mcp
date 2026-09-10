# combo-order-preview Specification

## Purpose

Provides read-only estimation and preview of account margin and buying-power impact for multi-leg option combinations without submitting orders or altering account funds.

## Requirements

### Requirement: Preview Combo Account Impact Without Submission

The system SHALL expose `preview_combo_order` using the SDK combo tradability
query. It SHALL reuse placement leg validation and account selection and accept
exact closing position IDs. It SHALL return available net-liquidation, initial
and maintenance margin, option buying-power, withdrawal, and buying-power-decrease
fields with an observation timestamp. Missing values SHALL remain unavailable.
Preview SHALL NOT place, modify, cancel, unlock, reserve funds, promise acceptance,
or invent a price-sign convention.

#### Scenario: Preview a valid package

- **WHEN** a caller provides valid legs, price, quantity, and account environment
- **THEN** the tool returns the SDK account-impact calculation without a trading write.

#### Scenario: Preview closing legs

- **WHEN** valid closing position IDs arrive as decimal strings
- **THEN** exact integer IDs reach the SDK query unchanged in value.

#### Scenario: Invalid package

- **WHEN** required leg fields are missing or invalid
- **THEN** preview fails validation before contacting the gateway.

#### Scenario: Unavailable preview

- **WHEN** the gateway rejects preview or omits an impact field
- **THEN** the tool surfaces the rejection or unavailable field without fabricating
  a value or falling back to placing a test order.
