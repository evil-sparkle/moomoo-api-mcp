# R6: Expose Market State and Trading Calendars

Status: Draft. Priority: Medium. Dependency: Existing market-data service.
Requirement: [Market sessions](../specs/market-sessions/spec.md).

## Problem and Outcome

Agents have quotes and order tools but no MCP interface for instrument session
state or provider trading calendars. Calendar assumptions based on the server's
timezone or weekdays alone can be wrong around holidays and partial sessions.

Success means callers can query observed instrument states and market-local
trading dates without guessing a schedule.

## Scope and Requirements

- R6.1: Expose provider market-state queries for instrument codes.
- R6.2: Expose provider trading-day queries for a market and date range.
- R6.3: Preserve raw state values and available session metadata; timestamp state
  observations in UTC and identify calendar dates as market-local.
- R6.4: Validate market/date inputs and report provider errors explicitly.
- R6.5: Do not treat calendar inclusion as proof that a particular instrument or
  account can trade, or infer missing session hours.

Scheduling orders, generating expiry-close jobs, calculating exchange calendars
locally, and enforcing instrument-specific trading hours are excluded.

## Proposed Interfaces

- `get_market_state(codes)` -> `{checked_at, data}` using SDK `get_market_state`.
- `get_trading_days(market, start, end)` -> `{market, date_basis, data}` using SDK
  `request_trading_days`, with `date_basis="market_local"`.

Add wrappers to market-data services/tools. Keep provider states intact rather
than reducing every state to a boolean open/closed. Preserve session-type metadata
when supplied, but do not invent opening/closing times from an ordinary date list.

Determine and document SDK batch/date-range limits before finalizing validation.
Local validation should reject invalid values, not normalize them to another market.

## Implementation Checklist

- [x] Verify SDK market enums, date fields, states, and available session metadata.
- [x] Implement both service queries and explicit error mapping.
- [x] Add MCP wrappers, timestamps, and date-basis metadata.
- [x] Test holidays, partial sessions, transitions, and invalid requests.
- [x] Document how a caller combines state and calendar without inferring permission.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| State query at a session boundary | Provider state with UTC observation timestamp |
| Server in a different timezone | Calendar dates remain market-local |
| Holiday in requested range | Provider omission preserved |
| Shortened-session metadata present | Preserved without assuming a full session |
| Unknown/unsupported market | Explicit validation or SDK error |
| Missing session metadata | No invented opening or closing times |
| Multiple instruments with different states | Per-instrument distinctions preserved |

Use fixed synthetic provider responses and injected time in tests. A read-only
gateway check can confirm response fields but should not depend on markets being open.

## Rollout, Risks, and Completion

These additive tools can ship independently. Documentation should distinguish
observed state from a future schedule and from account authorization. If the SDK
does not provide shortened-session hours, the output must say only what is known.
Rollback removes the added tools in a versioned release without changing order behavior.

Done when acceptance tests and [shared gates](README.md) pass.
