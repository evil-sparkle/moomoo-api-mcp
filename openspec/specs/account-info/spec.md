# account-info Specification

## Purpose

Defines MCP tools and services for querying broker account information, asset balances, positions, cash flows, margin ratios, and tradable quantities while preserving exact 64-bit identifier precision.

## Requirements

### Requirement: Get Account List

The system SHALL provide a tool to retrieve the list of trading accounts available to the user.

#### Scenario: Get Account List

Given the agent needs to know available accounts
When the `get_accounts` tool is called
Then it should return a list of trading accounts with their IDs (as strings to preserve precision), types, and simulation status

### Requirement: Get Account Assets

The system SHALL provide a tool to retrieve the asset summary (cash, market value) for a specific account.

#### Scenario: Get Assets

Given the user has funds in their account
When the `get_assets` tool is called with an account ID (as string)
Then it should return the total assets, cash, market value, and purchasing power

### Requirement: Get Account Positions

The system SHALL provide a tool to retrieve the current positions held in a trading account.

#### Scenario: Get Positions

Given the user holds stocks
When the `get_positions` tool is called with an account ID (as string)
Then it should return a list of held securities with stock code, quantity, cost price, and current market value

#### Scenario: Get Positions by Market

Given the user holds stocks in multiple markets (e.g. US, HK)
When the `get_positions` tool is called with a specific `market` (e.g. "US") and account ID (as string)
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

### Requirement: Get Max Tradable Quantity

The system SHALL provide a tool to calculate the maximum tradable quantity for a security.

#### Scenario: Get Max Buy

Given the user wants to buy a stock
When the `get_max_tradable` tool is called with code, price, and account ID (as string)
Then it should return the maximum number of shares they can buy based on available funds

### Requirement: Get Margin Ratio

The system SHALL provide a tool to retrieve margin ratio data for the account.

#### Scenario: Get Margin Status

Given the user has a margin account
When the `get_margin_ratio` tool is called
Then it should return the current margin ratios and risk status

### Requirement: Get Cash Flow

The system SHALL provide a tool to retrieve the cash flow history of the account.

#### Scenario: Get Cash History

Given the user wants to see transaction history
When the `get_cash_flow` tool is called with a date range and account ID (as string)
Then it should return a list of cash transactions (deposits, withdrawals, fees)

### Requirement: Unlock Trade

The system SHALL provide a tool to unlock trading permissions using a password or PIN.

#### Scenario: Unlock Trading

Given the user needs to perform a restricted action (e.g. check max tradable or trade)
When the `unlock_trade` tool is called with the password
Then the trading context should be unlocked for subsequent operations

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
