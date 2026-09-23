# account-info Specification

## Purpose

Defines MCP tools and services for querying broker account information, asset balances, positions, cash flows, margin ratios, and tradable quantities while preserving exact 64-bit identifier precision.

## Requirements

### Requirement: Get Account List

The system SHALL provide `get_accounts` to retrieve trading accounts available
through the configured trade-context filter and securities firm. It SHALL accept
optional `market` and `trd_env` arguments and return the existing list shape,
including exact string IDs, account types, simulation status and provider metadata.

Omitted or null filters SHALL retain all returned accounts. `market` SHALL accept
the values defined by Configure Trade Account Market Discovery, with `NONE` meaning
no additional market filtering. A named market SHALL match membership in the
account's `trdmarket_auth`. `trd_env` SHALL accept `REAL` or `SIMULATE`. Supplied
strings SHALL be trimmed and uppercased; blank or invalid arguments SHALL fail
before the account-list query. Both filters together SHALL use intersection.

Filters SHALL affect only the current response; they SHALL NOT switch connections,
alter future account resolution, or act as trading authorization. A filter SHALL
NOT expand the accounts available through the configured context. No match SHALL
return an empty list. Provider query failures SHALL remain errors, not empty lists.

#### Scenario: Get Account List

- **GIVEN** the agent needs to know available accounts
- **WHEN** `get_accounts` is called without filters
- **THEN** it SHALL return accounts with IDs as strings, types, and simulation status
- **AND** an all-market context SHALL retain both HK and US paper accounts when
  the provider returns them

#### Scenario: Select US paper accounts

- **WHEN** `get_accounts(market=" us ", trd_env="simulate")` is called
- **THEN** only SIMULATE accounts whose market authorizations include US SHALL remain
- **AND** returned IDs and provider metadata SHALL retain their values

#### Scenario: Multi-market account and empty result

- **GIVEN** one account is authorized for both HK and US
- **WHEN** either named market is requested
- **THEN** the account SHALL appear once in that response
- **AND** a valid filter with no matching account SHALL return an empty list

#### Scenario: Filters are independent across calls

- **WHEN** concurrent callers request HK and US accounts and a later caller omits filters
- **THEN** each SHALL receive only its own requested selection
- **AND** the later unfiltered call SHALL retain all provider-returned accounts

#### Scenario: Invalid filter

- **WHEN** `market="USA"`, `market=""`, or `trd_env="PAPER"` is supplied
- **THEN** the tool SHALL return an argument error without querying accounts

#### Scenario: A tool filter cannot widen deployment scope

- **GIVEN** the configured context returns only HK paper accounts
- **WHEN** US SIMULATE accounts are requested
- **THEN** the response SHALL be empty without creating a US context

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

### Requirement: Resolve Account-Bound Reads Without First-Account Fallback

For assets, positions, account summaries, cash flow, maximum tradable quantity,
current orders/deals and historical orders/deals, `acc_id="0"`
SHALL resolve only when exactly one discovered account matches the requested
environment. Zero or multiple matches SHALL produce a clear account-selection
error before the account-specific query, directing callers to `get_accounts` and
an explicit ID. Candidate IDs in error messages SHALL be masked to their last
four digits. Position, code and discovery-response filters SHALL NOT silently
choose the account for these reads.

A supplied nonzero ID SHALL be preserved exactly and used only if discovery confirms
membership in the requested environment. Unavailable IDs or environment mismatches
SHALL fail before the account-specific query, without fallback. Account summaries
SHALL resolve once and use that concrete ID for every constituent read. Discovery
errors SHALL propagate; an account-specific query SHALL never be issued with zero.
Read eligibility SHALL NOT be restricted by REAL write allowlists. Existing
environment defaults and identifier serialization contracts SHALL remain unchanged.
Combo preview SHALL retain the account-selection contract in Preview Combo Account
Impact Without Submission, which already reuses placement account selection.

#### Scenario: Ambiguous paper read

- **GIVEN** discovery returns one HK and one US paper account
- **WHEN** an account-bound read uses `trd_env="SIMULATE"` and `acc_id="0"`
- **THEN** it SHALL fail with masked candidates and explicit-ID guidance
- **AND** no account-specific query SHALL be made

#### Scenario: Single eligible account

- **GIVEN** exactly one account matches the requested environment
- **WHEN** an account-bound read uses `acc_id="0"`
- **THEN** it SHALL use that account's concrete ID for the provider request

#### Scenario: Explicit US paper account

- **GIVEN** discovery confirms a caller-supplied US paper account ID
- **WHEN** a SIMULATE read names it
- **THEN** it SHALL query exactly that account through the shared context
- **AND** no HK default account SHALL be substituted

#### Scenario: Unknown or wrong-environment account

- **WHEN** an explicit account is absent from discovery or belongs to a different environment
- **THEN** the read SHALL fail before its account-specific provider query

#### Scenario: Account summary binds once

- **WHEN** an account summary resolves a unique account
- **THEN** its assets and positions queries SHALL use the same resolved ID
- **AND** neither query SHALL ask the provider to select a default account

#### Scenario: Read access is not a REAL write permission

- **GIVEN** a discovered REAL account is not on the REAL write allowlist
- **WHEN** an explicit read names that account and environment
- **THEN** read account resolution SHALL NOT reject it because of the write allowlist
- **AND** no trading permission SHALL be granted by the read
