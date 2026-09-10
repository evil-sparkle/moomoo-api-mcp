# R7: Explicit Historical-Candle Pagination

Status: Implemented. Priority: Medium. Dependency: Existing K-line implementation.
Requirement: [Historical continuation](../specs/market-kline/spec.md).

## Problem and Outcome

`get_historical_klines` discards the continuation value returned by
`request_history_kline`. Clients cannot tell whether a requested date range has
additional pages or retrieve them through the current tool.

Success means clients can traverse all provider pages with bounded calls and
explicit completion, without breaking existing list-returning consumers.

## Scope and Requirements

- R7.1: Add a page-oriented tool returning `data`, `next_cursor`, and `has_more`.
- R7.2: Fetch one SDK page per call and preserve returned candle ordering.
- R7.3: Encode continuation losslessly and bind it to the original query filters.
- R7.4: Reject malformed or mismatched cursors before the SDK query.
- R7.5: Preserve the legacy tool's list response and document its one-page behavior.
- R7.6: Distinguish terminal pages, empty intermediate pages, and provider failures.

Automatic retrieval of unlimited history, caching, backtesting, and changing candle
adjustment semantics are excluded.

## Proposed Interface and Cursor Design

`get_historical_klines_page(code, ktype="K_DAY", start=None, end=None,
max_count=100, autype="QFQ", cursor=None)` ->
`{data: [...], next_cursor: string | null, has_more: boolean}`.

Use a versioned opaque cursor envelope containing the losslessly encoded SDK token
and normalized filters. Validate envelope version, fields, size, and query equality.
The cursor is not an authorization token and must contain no account credentials.
Changing a filter starts a new query with no cursor.

Confirm the SDK token type and serialization before choosing an encoding. Resolve
omitted date defaults once and retain the resolved query in continuation state so
a midnight transition cannot change the effective history range between pages.
Keep compatibility code out of the service's financial-data mapping logic.

## Implementation Checklist

- [x] Verify SDK token type, date defaults, page-size limits, and ordering guarantees.
- [x] Define the new result schema and versioned cursor codec.
- [x] Add service/tool pagination with explicit filter validation.
- [x] Keep the old method's response stable and document the new tool.
- [x] Add multi-page and malformed-cursor regression tests through MCP serialization.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| First page has token | More=true and usable cursor |
| Successive pages | Correct SDK tokens and filters; ordering preserved |
| Final page | Null cursor and more=false |
| Empty page with token | More=true; token retained |
| Corrupt/unknown-version cursor | Validation error before SDK query |
| Cursor reused with changed symbol or adjustment | Rejected |
| Query crosses midnight with omitted dates | Effective range unchanged |
| Later SDK page fails | Error rather than false completion |
| Legacy call | Original list response |

## Rollout, Risks, and Completion

The new tool is additive. Update history examples to show a bounded client loop
that stops at null cursor and handles errors without assuming completeness.
If the provider cursor is connection-bound, document that limitation explicitly;
do not promise persistence across restarts without evidence.

Done when acceptance tests and [shared gates](README.md) pass.
