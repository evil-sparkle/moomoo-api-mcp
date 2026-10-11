# trade-events Specification

## Purpose

Expose bounded broker order and deal callback observations without treating ephemeral events as durable execution or recovery evidence.

## Requirements

### Requirement: Scoped Bounded Event Observations
The system SHALL collect order events for REAL and SIMULATE and deal events only for REAL in a bounded thread-safe process buffer. Reads SHALL be scoped to exact account/environment, expose cursor epoch and retention gaps, and make ephemeral completeness limits explicit.

#### Scenario: Callback precedes placement response
- **WHEN** an order callback arrives before placement returns
- **THEN** its broker identifier SHALL be retained without inventing operation correlation

#### Scenario: Paper event support
- **WHEN** a paper client reads events
- **THEN** it SHALL receive order observations rather than unsupported deal callbacks

#### Scenario: Retention or restart gap
- **WHEN** events have expired or the process has restarted
- **THEN** the cursor response SHALL indicate the gap rather than imply a complete audit history
