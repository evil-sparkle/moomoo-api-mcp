# R5: Discover Option Expirations and Contracts

Status: Implemented. Priority: High. Dependency: Existing market-data service.
Requirement: [Option discovery](../specs/option-discovery/spec.md).

## Problem and Outcome

Combo placement requires exact option symbols, but the server does not expose
the SDK's expiration and option-chain lookup. Callers must obtain contract codes
elsewhere or risk constructing invalid symbols.

Success means an agent can start with an underlying, select an available expiry,
retrieve contract metadata, and use exact provider symbols in quotes or preview.

## Scope and Requirements

- R5.1: Expose expiration lookup by underlying code.
- R5.2: Expose option-chain lookup by underlying, date range, and call/put/all type.
- R5.3: Validate date format/order and supported types before SDK calls.
- R5.4: Preserve exact symbols and provider expiration metadata.
- R5.5: Distinguish an empty successful response from unsupported instruments,
  permission errors, and gateway failures.

Automatic strategy generation, investment ranking, prediction markets, and advanced
SDK screening filters are excluded from this first slice.

## Proposed Interfaces

- `get_option_expiration_date(code)` -> list of provider expiration records.
- `get_option_chain(code, start=None, end=None, option_type="ALL")` -> list of
  contract records. Document SDK defaults when dates are omitted.

Implement wrappers in `services/market_data_service.py` and `tools/market_data.py`.
Use supported SDK enums explicitly rather than silently replacing invalid input.
Return underlying/expiry/strike/type and exact code when the SDK supplies them;
do not manufacture fields absent from provider results.

Initial examples should query one selected expiry to bound response size. Verify
SDK date-span and result restrictions during contract discovery; if a request
exceeds them, return an actionable error rather than silently truncating contracts.

## Implementation Checklist

- [x] Verify SDK signatures, enums, date semantics, and response limits.
- [x] Add service lookup methods and pre-query validation.
- [x] Register both MCP tools with clear empty/error behavior.
- [x] Add mapping and validation tests plus a contract-selection workflow test.
- [x] Document expiration selection before chain queries.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| Expiration lookup succeeds | Provider dates preserved |
| CALL, PUT, and ALL filters | Correct enum forwarded |
| Start equals end | Single-expiry request supported where SDK permits |
| Reversed or malformed dates | Error before SDK call |
| Empty successful chain | Empty list |
| Unsupported underlying or no permission | Provider error, not empty success |
| Selected contract passed into a quote/preview | Symbol unchanged |

An integration-style test should call actual MCP tools with mocked SDK responses.
It must not subscribe or trade against a live account by default.

## Rollout, Risks, and Completion

Both tools are additive and can ship independently of R4. The discovery-to-preview
example depends on R4 and should be added only when preview is available.
Document provider restrictions and avoid claiming every market supports options.
If payload limits require pagination or additional filters, revise the contract
before silently restricting results.

Done when acceptance tests and [shared gates](README.md) pass.
Reference: [Option chain API](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html).
