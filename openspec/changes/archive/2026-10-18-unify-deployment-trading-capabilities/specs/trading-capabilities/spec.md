## MODIFIED Requirements

### Requirement: Scoped Trading Capability Discovery
The system SHALL expose account-, environment-, and instrument-scoped support, policy permission, and advisory readiness separately. Unknown broker support SHALL remain explicit. Discovery SHALL NOT unlock or mutate the broker.

#### Scenario: Paper API is broader than the executor
- **WHEN** a caller inspects a US stock paper account
- **THEN** documented NORMAL/MARKET support SHALL be distinct from the executor's NORMAL/DAY/RTH restriction
- **AND** deal queries and fees SHALL be reported unsupported

#### Scenario: Readiness is conditional
- **WHEN** account discovery or journal health is unavailable
- **THEN** readiness SHALL include the missing evidence without claiming broker acceptance

#### Scenario: Discovery and preparation select the executable account
- **WHEN** multiple accounts exist but only one matches deployment and client account scopes and the instrument market
- **THEN** discovery, approval preparation and execution SHALL resolve the same default account
- **AND** client grants and persistent operator pause SHALL remain explicit readiness constraints
