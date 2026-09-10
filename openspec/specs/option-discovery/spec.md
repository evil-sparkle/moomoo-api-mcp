# option-discovery Specification

## Purpose

Provides read-only discovery of available option expiration schedules and option chain contract specifications by underlying instrument.

## Requirements

### Requirement: Discover Option Expirations and Contracts

The system SHALL expose option expiration dates and chains by underlying code,
date range, and call/put/all type through read-only MCP tools. It SHALL preserve
SDK contract symbols and expiration metadata, validate date ordering and enum
inputs, and distinguish no matches from a gateway failure.

#### Scenario: Discover available expirations

- **WHEN** a supported underlying has available option expirations
- **THEN** the expiration tool returns the provider's dates without inventing dates.

#### Scenario: Select contracts from a chain

- **WHEN** a caller requests calls within an ordered date range
- **THEN** the chain tool forwards those filters and returns exact contract symbols
  suitable for subsequent quote and combo-preview requests.

#### Scenario: Empty or rejected query

- **WHEN** the provider returns no matching contracts
- **THEN** the tool returns an empty list; provider errors remain explicit errors.

#### Scenario: Invalid filters

- **WHEN** the start date follows the end date or option type is unsupported
- **THEN** validation fails before an SDK query.
