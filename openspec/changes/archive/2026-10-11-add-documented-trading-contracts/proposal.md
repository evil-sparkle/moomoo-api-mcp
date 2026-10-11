# Proposal

## Why

Clients currently infer trading permissions from tool availability and receive unbounded broker-shaped query results. CCXT's explicit capabilities and normalized contracts provide useful patterns, but this server must implement them within documented Moomoo OpenD behavior and preserve the recently merged REAL and SIMULATE execution journals.

## What Changes

- Add account-, instrument-, and environment-scoped capability discovery separating documented support, deployment policy, and observed readiness.
- Add Decimal instrument validation and bounded static metadata caching; preserve fresh quotes and distinguish price representation from product tick rules.
- Add read-only single-order preview and durable prepared requests with optional trusted operator approval enforced before journal dispatch.
- Add optional exact-symbol allowlists and durable per-account/environment/currency UTC daily submission budgets. Uncertain dispatches remain charged; retries never charge twice.
- Extract order/deal read responsibilities, add typed single-order and fee queries, and offer bounded immutable pagination without breaking existing list tools.
- Add explicit REAL session controls while preserving old retry identities and the existing NORMAL/DAY/RTH paper contract.
- Add bounded ephemeral order events for both environments and deal events only for REAL, independent of durable recovery.
- **BREAKING correction:** refuse SIMULATE deal queries, which the official API documents as unsupported.

## Capabilities

### New Capabilities

- `trading-capabilities`: Documented broker support, configured permission, and advisory readiness.
- `instrument-validation`: Product-aware numeric rules and safe static metadata caching.
- `trade-approval`: Reviewable prepared mutations and a separate trusted operator approval channel.
- `order-preview`: Composed single-order preview with explicit buying-capacity and valuation limits.
- `trade-events`: Bounded environment/account-scoped ephemeral broker callbacks.

### Modified Capabilities

- `order-retrieval`: Exact order lookup, actual fees, normalized pages, and REAL-only deals.
- `trading-policy`: Symbol restrictions and durable cumulative submission budgets.
- `order-placement`: Explicit sessions preserving durable identity and paper restrictions.
- `configuration`: Optional controls storage and a distinct operator credential.
- `provider-request-governance`: Account-scoped fee quota and cached continuation exemptions.

## Impact

Changes affect trading services, settings, server routes, MCP tool registration, documentation and focused regression tests. Existing journal outcome/recovery semantics and list query contracts remain intact. No dependency on CCXT, no broker mutations during verification, and no deployment changes are required. Official documentation and pinned SDK compatibility are recorded in the design; local and CI evidence do not claim live account acceptance.
