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

### Requirement: Option Chain Provider Admission

The system SHALL allow at most 10 option-chain SDK requests to begin in any
rolling 30-second period through the shared gateway service. MCP admission
SHALL precede worker dispatch. Existing inputs, contract records, inclusive
30-day date validation, empty results and provider error semantics SHALL be
preserved. Provider failures SHALL NOT trigger automatic retries.

#### Scenario: Eleven simultaneous requests

- **WHEN** 11 valid option-chain requests arrive simultaneously at an idle service
- **THEN** at most 10 SDK option-chain requests begin within any rolling 30 seconds
  and the remaining request waits within its deadline or returns retry-after guidance.

#### Scenario: Rolling window expires

- **WHEN** the oldest dispatched request ages beyond the provider window plus
  the configured non-negative safety margin
- **THEN** that capacity becomes available for another request.

#### Scenario: Existing discovery behavior

- **WHEN** an admitted request succeeds, has no matches, or is rejected by the provider
- **THEN** exact contract records, an empty list, or the original provider error
  are returned respectively, and no automatic retry occurs.

#### Scenario: Inclusive date range is retained

- **WHEN** the caller provides an inclusive 30-day range or a wider range
- **THEN** the first range is accepted and the wider range fails validation
  before quota admission or SDK dispatch.
