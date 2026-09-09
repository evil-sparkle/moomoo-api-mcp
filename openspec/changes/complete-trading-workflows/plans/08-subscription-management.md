# R8: Inspect and Release Market-Data Subscriptions

Status: Implemented. Priority: Medium. Dependency: Existing automatic subscriptions.
Requirement: [Subscription management](../specs/market-subscriptions/spec.md).

## Problem and Outcome

Quote and order-book reads subscribe automatically. The server exposes no way to
inspect those subscriptions or explicitly release symbols that a workflow no longer
needs. Long-running clients lack visibility into provider subscription usage.

Success means callers can inspect and release subscriptions owned by this quote
connection without disrupting another client.

## Scope and Requirements

- R8.1: Expose current-connection subscription inspection, including provider
  usage/quota fields when available.
- R8.2: Support explicit release by security codes and subscription data types.
- R8.3: Preserve existing automatic subscription behavior in quote/depth tools.
- R8.4: Scope every release to this server's connection; never invoke global release.
- R8.5: Surface provider minimum-duration, quota, and permission errors without
  fabricated success, invented limits, or automatic retry loops.

Global unsubscribe, cross-client management, automatic eviction, live push
notifications, and subscription scheduling are excluded.

## Proposed Interfaces

- `get_subscriptions()` -> connection subscriptions and available usage metadata,
  calling SDK `query_subscription(is_all_conn=False)`.
- `unsubscribe_market_data(codes, sub_types)` -> requested selections and provider
  acknowledgement, calling SDK `unsubscribe` without global-unsubscribe options.

Use market-data services/tools and validate explicit subscription enums. Preserve
the distinction between current-connection subscriptions and provider-wide quota
metadata if the SDK reports usage globally. Describe which scope each field covers.

Do not claim asynchronous provider state has already changed merely because a
request was acknowledged. If verification is needed, expose a subsequent inspection
step. Calls may re-subscribe a released symbol through the existing read tools.

## Implementation Checklist

- [x] Verify current-connection query scope, acknowledgement format, and quota fields.
- [x] Add service inspection/release wrappers and input validation.
- [x] Register MCP tools with explicit scope and minimum-duration error guidance.
- [x] Test interaction with automatic subscribe and isolation from other clients.
- [x] Document inspect -> select -> release -> inspect workflow.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| Quotes created current-connection subscriptions | Inspection includes them |
| Provider quota metadata available/absent | Report available fields without invented limits |
| Explicit symbol/type release | Only requested selections sent to SDK |
| Same symbol subscribed by another connection | Other connection untouched |
| Provider rejects early release | Error; no success or retry loop |
| Read after successful release | Existing automatic subscription works |
| Unsupported subtype or empty release selection | Actionable validation error |
| Repeated read of subscribed symbol | No service-created duplicate tracking entries |

Use mocked SDK state to model two connections. A live test must not release
subscriptions belonging to another client or depend on a guessed cooldown interval.

## Rollout, Risks, and Completion

Additive tools work in every trading mode because they change quote subscriptions,
not positions or orders. Provider quotas may be shared across connections, so
current-connection isolation does not imply a private quota. Document this clearly.
Rollback removes the new interfaces while leaving automatic subscriptions intact.

Done when acceptance tests and [shared gates](README.md) pass.
