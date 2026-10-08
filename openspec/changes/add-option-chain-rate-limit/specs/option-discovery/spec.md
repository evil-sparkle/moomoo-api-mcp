# Spec Delta

## ADDED Requirements

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
