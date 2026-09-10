## ADDED Requirements

### Requirement: Preserve Identifiers Across Account Tool Responses

The system SHALL serialize exact `acc_id`, `position_id`, and `combo_id` values as
decimal strings in all `get_accounts`, `get_assets`, `get_positions`, and
`get_account_summary` MCP responses, including nested records. Conversion SHALL
preserve missing fields, nulls, existing strings, and source records, and SHALL NOT
change monetary or quantity fields. Invalid floating-point or boolean identifiers
SHALL fail explicitly rather than be represented as valid IDs.

#### Scenario: Account discovery roundtrip

- **WHEN** an account ID larger than 2^53 is returned through MCP
- **THEN** both text and structured output contain a string that survives an
  IEEE-754 client and can be supplied unchanged to an account tool.

#### Scenario: Nested summary positions

- **WHEN** an account summary embeds positions with large position and combo IDs
- **THEN** all those IDs are strings and the original service records remain exact.

#### Scenario: Preserve non-identifier values

- **WHEN** a response includes null or absent IDs alongside balances and quantities
- **THEN** their presence and non-identifier values remain unchanged.

#### Scenario: Refuse already lossy identifiers

- **WHEN** an identifier arrives from a service as a float or boolean
- **THEN** serialization reports the field error without emitting a replacement ID.
