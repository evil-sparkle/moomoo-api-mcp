## ADDED Requirements

### Requirement: Expose Market State and Trading Days

The system SHALL provide read-only market-state queries for instrument codes and
trading-day queries for a market/date range. Responses SHALL preserve provider
session states and available session metadata, identify calendar dates as
market-local, and include a UTC observation time for market-state results. They
SHALL NOT imply that a trading date guarantees instrument trading permission.

#### Scenario: Market-state observation

- **WHEN** a caller requests instrument states during a session transition
- **THEN** the response contains the reported state and observation time rather
  than inferring a state from the server's local clock.

#### Scenario: Holiday or partial session

- **WHEN** the requested calendar range includes holidays or shortened sessions
- **THEN** the tool preserves the provider's trading-day list and available session
  metadata without treating every weekday as a full trading day.

#### Scenario: Provider unavailable

- **WHEN** the provider rejects a market or query
- **THEN** an explicit error is returned without a guessed calendar.
