## ADDED Requirements

### Requirement: Report Configured Trade Market Filter

`check_health` SHALL include top-level `trade_market` containing the normalized
configured trade-context market filter, including `NONE` for all-market discovery.
It SHALL report this configuration even when the gateway is unavailable, without
extra broker queries, account identifiers, or changes to the existing health
deadline, status semantics, trading mode or execution-halt fields. The field SHALL
describe discovery configuration, not trading authorization or market availability.

#### Scenario: All-market connection

- **WHEN** health is requested with the default market configuration
- **THEN** `trade_market` SHALL be `NONE`
- **AND** the existing quote and trade probe outcomes SHALL remain independently reported

#### Scenario: Gateway unavailable

- **GIVEN** the configured filter is `US` and OpenD cannot be reached
- **WHEN** health is requested
- **THEN** `trade_market` SHALL still be `US`
- **AND** connectivity SHALL be reported as unavailable or failed rather than inferred
  from the configured market
