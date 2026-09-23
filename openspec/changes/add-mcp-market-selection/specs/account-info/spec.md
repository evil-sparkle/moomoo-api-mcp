## MODIFIED Requirements

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

## ADDED Requirements

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
