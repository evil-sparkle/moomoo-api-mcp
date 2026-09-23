## Context

See `proposal.md` for motivation. At main `e8b2c52`, `Settings` has host, port,
security firm and trading policy but no market setting. `TradeService` creates
`OpenSecTradeContext` without `filter_trdmarket`; installed SDK 10.10.7008 defaults
that argument to HK. `server.py` shares one trade service for the process, and
`get_accounts` currently exposes its SDK list without filters. Account-bound reads
largely pass zero IDs directly to the SDK; mutation paths and combo preview already
perform their own account resolution.

### Evidence and limits

On 2026-09-23, the local image built from that commit, using FUTUSG and the user's
existing interactive OpenD login, produced these observations (account identifiers
are deliberately omitted):

| Check | Result |
| --- | --- |
| Existing MCP account discovery with implicit HK | HK paper account visible; US paper account absent |
| Explicit US SDK context | US STOCK_AND_OPTION paper account visible |
| Explicit NONE SDK context | HK STOCK and US STOCK_AND_OPTION paper accounts visible together |
| Current-order query through NONE, explicit US paper ID and SIMULATE | RET_OK, zero rows |

The NONE probe used a temporary SDK context and closed it afterward; it did not
change the running MCP configuration. No order was placed, no REAL account detail
was queried, and no lock/unlock was invoked. Empty order results establish query
acceptance only, not order retention or proof of absence.

Installed SDK `get_acc_list` source explicitly accepts all returned market lists
when the context filter equals `TrdMarket.NONE`. Its `_check_acc_id` selects a
default account when given zero, explaining why read-side resolution matters.
[Moomoo's trade FAQ](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html)
documents NONE as unfiltered discovery. These findings establish discovery and a
paper read; mutation correctness through NONE is not a live-tested claim.

Stage 1's code and active deltas are on main but its changes have not been synced
into all base specs. This change adds independent configuration and health
requirements, modifies only Get Account List, and adds a read-resolution contract.
It does not replace Stage 1's security configuration or order requirements.

## Goals / Non-Goals

**Goals:** preserve one process-owned trade connection, make discovery scope
visible, keep request filters independent, and prevent expanded discovery from
silently directing a read to an arbitrary account.

**Non-Goals:** per-market connection pools, dynamic context switching, new trading
markets or instrument types, quote-market restrictions, altered gateway locking,
new permissions, journal implementation, or live order experiments during planning.
Recognizing a market filter does not certify provider functionality in that market.

## Decisions

### 1. One context with an explicit, immutable filter

Add `Settings.trading_market` and `MOOMOO_TRADING_MARKET`. Accept exactly NONE, HK,
US, CN, HKCC, SG, AU, JP, MY and CA; trim and uppercase. Missing/blank means NONE.
Validate with an explicit mapping to SDK constants, without regex or dynamic
acceptance of unrelated SDK enum members (futures, funds, crypto, prediction).
The list names securities discovery filters, not a promise of paper accounts for
every value. Invalid configuration fails before transport startup.

Pass the resolved value from `_build_services` into `TradeService`, whose direct
construction also defaults to NONE, then explicitly into each context construction.
Keep the existing bounded connection worker, reconnect hook and shutdown lifecycle.
The market belongs to the process, not to a session or the most recent request.

Alternatives: US-only fixes today's symptom but loses convenient HK access; one
context per market multiplies lifecycle, lock and cached-account state. Neither is
needed for the observed provider behavior. Preserve explicit HK as the migration
escape hatch for discovery scope, not as a trading authorization mechanism.

### 2. Stateless response filtering on account discovery

Extend `get_accounts` tool and service signatures with optional `market` and
`trd_env`. Validate arguments before `get_acc_list`. Reuse the market vocabulary
above; null/omitted or market NONE adds no market predicate. Reject blank supplied
arguments. Match normalized market by exact membership in `trdmarket_auth`, and
environment by exact equality. Combine with AND. Missing market metadata does not
match a named filter. Return existing records in provider order with no duplicate
expansion for multi-market accounts and preserve exact identifier serialization.

Filter a new response list, never the SDK cache or shared service state. Internal
resolvers always query unfiltered discovery for this context; a prior filtered MCP
call cannot select an account for a later mutation. Keep `get_positions(market=...)`
as its existing position-result filter, unrelated to context selection.

### 3. Separate read resolution from mutation authorization

Introduce a service-level read resolver for assets, positions, maximum tradable
quantity, cash flow, current orders/deals and historical orders/deals. Validate
the requested environment, then use unfiltered discovery for that environment.
Resolve zero only when exactly one candidate exists. Do not infer a default read
account from an optional symbol or position filter: callers obtain the intended
ID from discovery. Validate explicit IDs against discovered environment membership;
never replace them or fall back on errors. Use exact integer/string conversion,
not floating-point conversion, and reject malformed identifiers.

Read errors are ordinary account-selection errors, not order-dispatch errors.
Reuse masking for candidates. Do not reuse `_resolve_account` indiscriminately:
its REAL allowlist behavior belongs to write eligibility. Reads continue to obey
their existing access behavior without requiring REAL write permission. Combo
preview already has its own specified placement-style resolver; retain it.

`get_account_summary` resolves once in the service, then supplies that ID to both
private query helpers. The SDK revalidates membership before each detail request;
the application does not repeat discovery. SDK contract tests cover disappearance
before either query. A missing account fails the summary without switching IDs.

Stage 1 placement still uses instrument market and environment to narrow candidates;
modify/cancel still refuse ambiguity without an explicit ID. Discovery filters do
not change those rules, REAL allowlists, halt ordering, limits, or dispatch outcomes.
Regression tests must cover both US and HK paper accounts being visible at once.

### 4. Configuration visibility and deployment

Add top-level `trade_market` to health from the already validated service setting,
even when connection establishment fails. Keep gateway probes and their five-second
budget unchanged. Do not expose account IDs through health or imply that an empty
account list means a failed connection.

Pass the variable through the base Compose file (production inherits it). Update
README, deployment instructions, configuration example template and tool docstrings
with NONE/US/HK examples and explicit-account usage. Distinguish `FUTUSG` (broker
region), `US` (market authorization/filter), and `SIMULATE` (environment). No new
volume, login procedure, OpenD port exposure, or dependency is necessary.

### 5. Validation layers

Use synthetic SDK account lists with distinct HK and US SIMULATE accounts and a
multi-market REAL account. Unit and MCP tests cover normalization, intersection,
empty/error distinctions, IDs above 2^53, concurrent filter independence and read
ambiguity. Exercise every previously zero-forwarding read endpoint, not just assets.
Connection tests prove explicit NONE/US/HK propagation and process sharing. Existing
mutation tests must still prove limits, REAL allowlists, halted-write ordering,
modify/cancel ambiguity, preview semantics and no replay.

After implementation, repeat discovery and explicit paper-account reads through the
actual MCP entry point against the locally logged-in provider. That is a read-only
acceptance check in SIMULATE mode, avoiding READ_ONLY's gateway-lock side effect.
SDK-only results above do not substitute for that MCP check. No mutation probe is
needed to accept account discovery; any later bounded paper write test remains
separately identified as a provider test, with no automatic replay after uncertainty.

## Risks / Trade-offs

- [Compatibility] NONE returns more accounts, and zero-ID reads become ambiguous.
  → Explicit breaking-change note, explicit IDs, optional HK deployment filter.
- [Provider limits] All-market discovery is scoped to securities context, firm,
  login and provider support; named filters do not guarantee accounts exist.
  → Retain provider errors and empty results without fallback or fabricated accounts.
- [Authorization confusion] Visibility is broader than write permission.
  → Keep authorization separate; test all three trading modes with synthetic data.
- [Read overhead] Explicit-ID membership checks add account-list queries.
  → Use existing offloading and avoid introducing a new stale application cache;
  record any provider rate-limit failures in the read-only acceptance check.
- [Scope interaction] Stage 2 may add its own account allowlist and identity rules.
  → Land this independently and rebase Stage 2 without claiming journal prerequisites
  complete. Do not change its planning artifacts in this change.

## Migration Plan

1. Implement and validate in an isolated branch based on current main; reconcile
   any newly landed Stage 1 changes before merging.
2. Before upgrade, callers store exact IDs obtained from discovery and use them
   with explicit environments. Operators needing former discovery scope set HK.
3. Deploy the rebuilt image and recreate the container to apply market settings,
   preserving the existing OpenD login volume. Verify health's configured market
   and filtered MCP paper discovery without placing orders.
4. Roll back by restoring the previous image; it ignores the new setting and uses
   its implicit HK scope. Retain the login volume. No schema or data migration.
