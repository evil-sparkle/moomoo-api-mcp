# Design

## Context

See proposal.md for motivation. Baseline origin/main is 81a5dbf and already contains separate REAL and SIMULATE journals. The pinned SDK is moomoo-api 10.10.7008; its public source archive hash matches uv.lock. Existing admission and recovery paths must remain authoritative.

## Goals / Non-Goals

Goals: provide inspectable trading contracts, enforce optional approval and cumulative limits at the mutation boundary, preserve durable retry identity, and keep every MCP client on the same server contract.

Non-goals: broaden journaled paper execution, add a CCXT provider, simulate universal margin/fees, treat callback observations as durable recovery evidence, deploy infrastructure, or execute broker mutations during verification.

## Decisions

- Capabilities combine documented conservative product rules with discovered account facts, deployment policy and journal/halt health. Unknown permission remains unknown. Tool registration and account market authority do not imply order acceptance.
- InstrumentAdapter caches bounded static classification/lot facts by connection identity and expiry. Snapshots remain fresh. Decimal validation enforces documented US equity and A-share grids, options' two-decimal representation, and explicitly unknown product-specific option/HK grids. Missing required classification fails closed. No universal tick is inferred from snapshot price_spread.
- Preserve public list query methods as compatibility delegates into a query service. New typed order views preserve raw broker status and exact IDs. Pagination uses bounded expiring immutable snapshots bound to filters/account/environment; continuation is local and consumes no broker quota.
- A single-order preview composes policy valuation and acctradinginfo_query. It reports maximum quantities and only documented derivative margin fields. The existing native combo preview remains separate.
- Optional controls use their own explicitly initialized SQLite store. Prepared intent includes full default-expanded parameters, explicit resolved account, environment, operation ID, epoch, expiry and review facts. Decision records bind the stored request. A separate HTTP operator channel uses a distinct token and configured operator identity; ordinary MCP credentials never approve. No approve MCP tool is registered.
- Guard checks run before unlock and before durable dispatch. Approval binds one operation and survives only as stored decision evidence; journal retries return stored state. Budget charges are atomic and keyed by UTC day/account/environment/currency/operation. Charge the full conservative submission value for new placements and exposure-adding modifications, not net portfolio exposure. Reservations persist conservatively if any crash/uncertainty follows; cancellation never refunds turnover.
- Symbol allowlists match exact codes for every leg, including exposure-adding modifications, while cancellations remain possible.
- Explicit REAL sessions enter canonical identity. SDK NONE/N/A is the compatibility default and is omitted from old canonical identities. Paper RTH/default aliases preserve old identity; other paper sessions fail. A session change conflicts on the same operation ID.
- A process-owned bounded callback ring stores exact broker identifiers as soon as callbacks arrive, even before a place response. Orders are observed for both environments, deals only for REAL. Epoch/sequence and retention-gap reporting make its ephemeral scope explicit.

## Official API Evidence

- [Account facts](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html), [product and paper restrictions, callback environments and ticks](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).
- [Placement parameters and deprecated fill_outside_rth](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html), [static information](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-static-info.html).
- [Maximum tradable quantities](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-max-trd-qtys.html), [native combo preview](https://openapi.moomoo.com/moomoo-api-doc/en/trade/comboorder-tradinginfo-query.html).
- [Current orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-list.html), [historical orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html). Historical SDK calls have no order_id argument.
- [Actual fees](https://openapi.moomoo.com/moomoo-api-doc/en/trade/order-fee-query.html): REAL only, not Moomoo CA (SDK SecurityFirm.FUTUCA), at most 400 IDs and ten requests per thirty seconds per account.
- [Order callbacks](https://openapi.moomoo.com/moomoo-api-doc/en/trade/update-order.html), [REAL-only deal callbacks](https://openapi.moomoo.com/moomoo-api-doc/en/trade/update-order-fill.html), [REAL-only deal queries](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-fill-list.html).

## Risks / Trade-offs

- Broker account entitlements and deployed versions can differ from documentation → report advisory support/readiness and retain broker enforcement.
- Session/default additions can invalidate old journal retries → canonical compatibility regression tests cover pre-change records.
- Static caching can hide changed metadata → bounded expiry, connection identity invalidation, no quote-price caching.
- A controls reservation can survive without a broker submission after a crash → conservative daily accounting is explicit; it never permits exceeding the configured ceiling.
- Process callbacks can be dropped or missed during reconnect → gap-aware ephemeral output, no change to durable recovery.
- Controls and pagination enlarge the public interface → additive tools and defaults, bounded resources, focused direct-service/MCP/authentication tests and full required CI.

## Migration Plan

Existing deployments retain their defaults. To enable budgets or approval, initialize dedicated controls storage explicitly, configure limits/credentials through operator-managed environment injection, and preserve its volume across restarts. Never delete execution journals to roll back. Document new settings only in public examples and runbooks. Archive this completed change before the authorized merge; leave live provider-validation work active.
