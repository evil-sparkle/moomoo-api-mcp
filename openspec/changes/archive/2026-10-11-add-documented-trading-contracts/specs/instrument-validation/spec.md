## Purpose

Validate order quantities and prices using documented product rules while keeping static metadata caching independent of fresh quotes.

## ADDED Requirements

### Requirement: Product-Aware Numeric Validation
The system SHALL validate whole quantities and documented price representation with Decimal arithmetic before mutation. Verified tick rules SHALL be enforced; unknown product ticks SHALL remain explicit. Option lot size SHALL NOT substitute for a monetary multiplier.

#### Scenario: US equity price threshold
- **WHEN** a US equity price is below one dollar or at least one dollar
- **THEN** its documented grid SHALL be 0.0001 or 0.01 respectively
- **AND** an off-grid price SHALL be refused without rounding silently

#### Scenario: Option precision does not establish a tick
- **WHEN** a US option price has two decimal places but authoritative product tick data is absent
- **THEN** representation validation SHALL be distinguished from unknown tick validation

### Requirement: Bounded Static Metadata Cache
The system SHALL cache only static classification and lot metadata with bounded size and expiry, invalidate it when the quote connection changes, and continue obtaining current quote and multiplier facts when needed.

#### Scenario: Reconnect and fresh prices
- **WHEN** a connection is replaced or metadata expires
- **THEN** subsequent lookups SHALL refresh static facts
- **AND** cached metadata SHALL NOT make price snapshots stale
