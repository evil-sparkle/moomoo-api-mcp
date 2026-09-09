# Feature Development Plans

Status: Draft for review. These plans describe future work; none of their
implementation checkboxes indicate completed code.

Each plan is a separately reviewable delivery slice with requirements, interface
decisions, implementation tasks, acceptance tests, and rollout considerations.
The linked OpenSpec deltas are the normative acceptance contracts. Revise both
the plan and its delta if review changes a requirement.

| Order | Feature plan | Priority | Dependencies | Main approval decision |
| --- | --- | --- | --- | --- |
| 1 | [R1: Active health checks](01-active-health-checks.md) | High | None | Additive health schema and startup behavior |
| 2 | [R2: Account ID precision](02-account-id-precision.md) | High | Existing combo-ID support | Numeric IDs become strings |
| 3 | [R3: Trading mode enforcement](03-trading-mode-enforcement.md) | High | None; integrates with R1 | Default READ_ONLY and migration |
| 4 | [R4: Combo order preview](04-combo-order-preview.md) | High | R2, R3, existing combo support | Read-only preview response |
| 5 | [R5: Option discovery](05-option-discovery.md) | High | Existing market-data service | Initial filter surface |
| 6 | [R6: Market sessions and calendar](06-market-sessions-calendar.md) | Medium | Existing market-data service | Market-local dates and state metadata |
| 7 | [R7: Historical-candle pagination](07-historical-candle-pagination.md) | Medium | Existing K-line implementation | New paginated tool, legacy tool retained |
| 8 | [R8: Subscription management](08-subscription-management.md) | Medium | Existing automatic subscriptions | Current-connection scope |

The order is a recommendation, not a claim that all work is serially dependent.
R5-R8 can be delivered independently of combo preview. R3 should precede releases
of new trading workflows. No deadline or effort commitment is implied before SDK
contract checks and proposal review.

## Shared Definition of Done

- Requirements have explicit test coverage, including failure paths and actual
  MCP serialization or argument forwarding where relevant.
- The full pytest suite passes; changed code introduces no new lint violations.
- Supported Python >=3.10 and the minimum SDK version remain supported.
- README, tool descriptions, examples, and migration notes match final behavior.
- Strict OpenSpec validation passes for the affected change.
- SDK fixtures use synthetic identifiers and contain no live account information.
- Live writes are never used as validation for these plans. Optional gateway
  checks are read-only, scoped, and require an appropriately available environment.
- Implementation is reviewed before release. Archival follows deployment and
  resolves overlap with existing unarchived proposals.

## Review and Release Boundaries

Use one feature-focused implementation PR per plan. A task checkbox is checked
only when its stated result is verified. Failed SDK contract checks should update
the proposal rather than produce guessed interfaces. Split the umbrella proposal
into independently archived changes if releases diverge; do not archive unfinished
features when only one slice ships.

Related documents: [proposal](../proposal.md), [design](../design.md), and
[aggregate task checklist](../tasks.md).
