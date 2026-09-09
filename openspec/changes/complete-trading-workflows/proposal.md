# Change: Complete Trading and Market Data Workflows

## Why

The server exposes 23 tools, including combo placement, but callers cannot yet
discover option contracts or inspect a combo's account impact through MCP. Existing
health reporting, identifier serialization, pagination, and subscription handling
also leave gaps in workflows built on those tools.

This proposal converts the repository audit of 2026-09-10 into requirements and a
phased development plan. It is a draft for review; implementation has not started.

## What Changes

| ID | Deliverable | Priority | Requirement delta |
| --- | --- | --- | --- |
| R1 | Active gateway health probes | High | [system-health](specs/system-health/spec.md) |
| R2 | Exact identifiers across account responses | High | [account-info](specs/account-info/spec.md) |
| R3 | Enforced read-only, simulation, and real trading modes | High | [trading-policy](specs/trading-policy/spec.md) |
| R4 | Read-only combo account-impact preview | High | [combo-order-preview](specs/combo-order-preview/spec.md) |
| R5 | Option expiration and contract discovery | High | [option-discovery](specs/option-discovery/spec.md) |
| R6 | Market state and trading-day queries | Medium | [market-sessions](specs/market-sessions/spec.md) |
| R7 | Explicit historical-candle pagination | Medium | [market-kline](specs/market-kline/spec.md) |
| R8 | Connection-scoped subscription inspection and release | Medium | [market-subscriptions](specs/market-subscriptions/spec.md) |

- **BREAKING**: account identifiers currently emitted as JSON numbers become
  decimal strings at every covered MCP response boundary.
- **BREAKING**: introduce `MOOMOO_TRADING_MODE`, defaulting to `READ_ONLY`.
  Deployments that submit orders must explicitly select `SIMULATE` or `REAL`.
  This is a proposed migration decision, not existing behavior.
- Preserve existing tool argument defaults and the existing historical-candle
  list response; use a new paginated tool for continuation metadata.

## Impact

- Runtime areas: `server.py`, services for gateway/trading/market data, and account,
  system, trading, and market-data tools.
- Tests: service validation and SDK forwarding, actual MCP tool serialization,
  policy enforcement, failure paths, and compatibility regressions.
- Documentation: tool catalogue, configuration, migration notes, examples, and
  removal of the unsupported claim that absent credentials enforce simulation.
- Dependencies: use capabilities in the installed `moomoo-api>=10.10.7008`; no SDK
  upgrade or new runtime dependency is planned.
- Compatibility target: Python >=3.10 from `pyproject.toml`. The Python 3.14 notes
  in `openspec/project.md` do not change the package's supported version floor.

## Existing Work and Overlap

- `add-combo-order-support` already supplies leg validation, position identifiers,
  strategy-view retrieval, and string IDs in `get_positions`. Reuse that work.
- `fix-account-id-precision` remains unchecked, although string inputs already
  exist. R2 completes the output boundary; reconcile that proposal's checklist
  and deltas before either change is archived.
- `add-market-data-tools` is implemented but unarchived. R7 adds a separate
  pagination contract without replacing its existing K-line requirement.
- R2 adds a distinct account-wide serialization requirement rather than replacing
  `Get Account Positions`, avoiding loss of the pending combo strategy scenarios.
- Archive completed prerequisite changes in dependency order after deployment;
  do not mark them deployed solely because their tasks are checked.

## Delivery and Scope

Start with the [individual feature plans](plans/README.md): each feature has its
own requirements, implementation checklist, acceptance tests, and rollout plan.
See [tasks.md](tasks.md) for aggregate implementation order and release gates, and
[design.md](design.md) for shared interfaces, migration, dependencies, and decisions.
Each R-number is a separate reviewable implementation slice. R1-R3 establish the
foundation; R4-R5 complete the options workflow; R6-R8 complete market-data access.

Automated expiry liquidation, stop-loss execution, scheduled orders, strategy
selection, and external notifications are outside this proposal. No live order is
needed to validate the proposed read-only features or mocked write-policy tests.
