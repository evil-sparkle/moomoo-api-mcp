# R2: Preserve Account Identifiers Across MCP

Status: Draft. Priority: High. Dependency: Existing combo position-ID support.
Requirement: [Account response identifiers](../specs/account-info/spec.md).

## Problem and Outcome

`get_positions` serializes position IDs as strings, but `get_accounts` and nested
`get_account_summary` results bypass that conversion. JSON clients using doubles
can silently round large IDs before sending them back as apparently valid integers.

Success means every covered ID survives retrieval, JSON serialization, a
double-parsing client, and a subsequent account/order request without changing value.

## Scope and Requirements

- R2.1: Convert exact `acc_id`, `position_id`, and `combo_id` to decimal strings in
  `get_accounts`, `get_assets`, `get_positions`, and `get_account_summary` results.
- R2.2: Cover nested summary records and both MCP text and structured content.
- R2.3: Preserve missing fields, nulls, existing strings, amounts, quantities, and
  source objects. Keep in-process service identifiers as exact integers.
- R2.4: Handle Python and NumPy integer types. Reject float/bool identifiers with
  an actionable field error instead of inventing a replacement identifier.

This slice does not stringify arbitrary numeric fields or redesign SDK order/fill
responses. Reconcile the older `fix-account-id-precision` proposal rather than
duplicating its already implemented string input work.

## Proposed Design

Extract the existing helper from `tools/account.py` into a shared tool utility.
Use a field allowlist and traversal of response dictionaries/lists. Do not mutate
DataFrames or service results. Apply the utility at every covered tool boundary,
including the nested summary wrapper.

Keep all existing tool parameters and output containers. The intentional public
change is the type of ID values: a numeric `acc_id` becomes a decimal string.
Document passing IDs unchanged and use string IDs in public examples.

## Implementation Checklist

- [ ] Inventory the four tools' actual response paths and nullable SDK fields.
- [ ] Extract field-aware serialization and explicitly reject lossy values.
- [ ] Wire all covered tools to the shared serializer.
- [ ] Add actual tool/MCP serialization tests, including nested summaries.
- [ ] Reconcile existing account-ID tasks and add migration notes.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| Synthetic ID `9007199254740993` | Exact string in text and structured JSON |
| Nested positions in a summary | Account, position, and combo IDs all preserved |
| JSON client parses all numeric tokens as doubles | ID strings unchanged on return |
| Missing/null ID | Missing/null preserved |
| Source contains NumPy integer | Exact decimal string |
| Source contains float/bool ID | Explicit serialization failure |
| Balance and quantity values | Remain numeric and unchanged |
| Original service result reused | No mutation |

The regression test must call the tool function and MCP result conversion, not
only the serializer helper. Include a retrieval-to-request roundtrip with a mock SDK.

## Rollout, Risks, and Completion

Publish the numeric-to-string change so callers remove number coercion. Verify
SDK missing-value sentinels before distinguishing invalid values from nulls.
Existing clients that treat IDs opaquely continue to work; clients performing
numeric operations need migration. Preserve the previous release for rollback.

Done when all covered response paths pass roundtrip tests and the
[shared gates](README.md) pass.
