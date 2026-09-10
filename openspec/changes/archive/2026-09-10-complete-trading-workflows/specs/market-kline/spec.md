## ADDED Requirements

### Requirement: Retrieve Historical Candles With Explicit Continuation

The system SHALL expose `get_historical_klines_page` returning `data`,
`next_cursor`, and `has_more`. One call SHALL retrieve one SDK page and preserve
its ordering. Cursors SHALL encode the provider continuation losslessly and bind
it to the original query filters. The existing `get_historical_klines` SHALL retain
its list return type and be documented as returning a single page.

#### Scenario: Fetch successive pages

- **WHEN** the SDK returns candles and a continuation token
- **THEN** the tool returns `has_more=true` and a cursor usable with the same filters.

#### Scenario: End of data

- **WHEN** the SDK has no continuation token
- **THEN** `next_cursor` is null and `has_more` is false.

#### Scenario: Empty intermediate page

- **WHEN** the SDK returns no rows but provides a continuation token
- **THEN** the tool preserves that token and reports more data may be available.

#### Scenario: Invalid continuation

- **WHEN** a cursor is malformed or belongs to different query filters
- **THEN** the tool rejects it before querying the SDK.

#### Scenario: Later-page error

- **WHEN** a continuation request fails at the gateway
- **THEN** it returns an explicit error rather than claiming the history is complete.
