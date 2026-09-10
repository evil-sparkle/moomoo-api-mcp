# Account Info

## MODIFIED Requirements

### Requirement: Get Account Positions

The system SHALL provide a tool to retrieve the current positions held in a trading account.

The system SHALL additionally support an option strategy view, which groups multi-leg
option positions into strategies. This view is the documented source of the
`position_id` values that a closing combo order requires, so without it the closing
workflow cannot be performed through this server at all.

The `position_id` and `combo_id` fields SHALL be serialized as decimal strings when
returned across the MCP boundary. These are 64-bit values, and a client that parses
JSON numbers as IEEE-754 doubles rounds them silently. The rounded result is still a
valid integer, so no downstream validation can detect it; the order would simply be
submitted against a position that does not exist.

#### Scenario: Get Positions

Given the user holds stocks
When the `get_positions` tool is called
Then it should return a list of held securities with stock code, quantity, cost price, and current market value

#### Scenario: Get Positions by Market

Given the user holds stocks in multiple markets (e.g. US, HK)
When the `get_positions` tool is called with a specific `market` (e.g. "US")
Then it should return only the positions for that market

#### Scenario: Get Positions Grouped Into Option Strategies

Given the user holds a multi-leg option strategy such as a vertical spread
When the `get_positions` tool is called with `show_option_strategy_view=True`
Then the strategy should be returned as a row with `position_type` of `COMBINED`
And each of its legs should be returned as a row with `position_type` of `LEG`
And every row should carry a `position_id` and a shared `combo_id`
So that those identifiers can be supplied to `place_combo_order` when closing.

#### Scenario: Position Identifiers Survive A JSON Roundtrip

Given a strategy whose `position_id` exceeds 2^53
When the `get_positions` tool returns it across the MCP boundary
Then the identifier should be serialized as a decimal string, not a JSON number
So that a client parsing numbers as IEEE-754 doubles cannot silently round it
And the value submitted to `place_combo_order` should equal the value held.

#### Scenario: Flat View Remains The Default

Given `show_option_strategy_view` is not supplied
When the `get_positions` tool is called
Then positions should be returned ungrouped, as before this change.
