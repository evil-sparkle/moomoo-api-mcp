## ADDED Requirements

### Requirement: Configure Trade Account Market Discovery

The system SHALL support `MOOMOO_TRADING_MARKET` with values `NONE`, `HK`, `US`,
`CN`, `HKCC`, `SG`, `AU`, `JP`, `MY`, and `CA`. Missing or blank configuration
SHALL resolve to `NONE`. Values SHALL be trimmed and normalized to uppercase.
`NONE` SHALL request all securities accounts exposed by the provider for the
configured login and securities firm; a named value SHALL select the provider's
market-filtered discovery behavior. Recognition of a market SHALL NOT imply that
the account or provider supports trading in it.

Invalid values SHALL fail startup before serving a transport, with an error naming
the variable and accepted values. The selected filter SHALL remain fixed for the
process lifetime and survive reconnection without falling back to HK. The standard
container deployment SHALL pass this configuration to the MCP server.

#### Scenario: All-market default

- **WHEN** the server starts with the variable absent, empty, or whitespace-only
- **THEN** its trade account discovery filter SHALL be `NONE`
- **AND** it SHALL NOT implicitly use the provider's HK default

#### Scenario: Explicit legacy scope

- **WHEN** the server starts with `MOOMOO_TRADING_MARKET=" hk "`
- **THEN** its filter SHALL be `HK`, retaining the previous market discovery scope

#### Scenario: Reject a typo or unsupported category

- **WHEN** the variable is `USA`, `FUTURES`, or another unaccepted value
- **THEN** startup SHALL fail with the accepted-value diagnostic
- **AND** no transport SHALL start listening

#### Scenario: Reconnect preserves scope

- **GIVEN** the configured filter is `NONE` or `US`
- **WHEN** the gateway disconnects and reconnects
- **THEN** subsequent account discovery SHALL use the same configured filter

#### Scenario: Market selection does not authorize trading

- **WHEN** an operator selects `US` or `NONE`
- **THEN** environment policy, REAL write allowlists, limits, and lock behavior
  SHALL continue to apply independently of that selection
- **AND** market-data queries SHALL NOT be restricted to that discovery filter
