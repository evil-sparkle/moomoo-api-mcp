## Context

The MCP tool layer wraps synchronous SDK services. The installed SDK already has
combo tradability, option discovery, calendar, and subscription APIs. The present
server does not expose them. Health reporting checks only whether a quote context
exists; K-line queries discard the returned cursor; account summaries bypass the
recent position-ID serializer.

## Goals and Non-Goals

Deliver R1-R8 with small SDK wrappers and explicit response contracts. Keep Python
service IDs exact and serialize at MCP boundaries. Make blocked operations fail
before gateway submission. Keep unrelated formatting cleanup and autonomous
trading outside this work.

## Interfaces and Decisions

### R1: Health

Keep `check_health` and its top-level `status` and `host`. Add per-service quote and
trade results, UTC observation time, configured trading mode, and gateway version
when available. Use a lightweight quote global-state request and an appropriate
read-only trade probe, checking return codes. Avoid returning account contents.
Overall status is `connected` only when both probes succeed, `degraded` when one
succeeds, and `disconnected` when neither succeeds. Successful connectivity does
not imply trading is unlocked or that every market is authorized.

Bound the operation to a proposed 5-second total deadline. Verify SDK timeout
controls during implementation; a timed-out worker must not permit unbounded
background probes on repeated calls. Reuse a single in-flight probe if necessary.
Allow startup with a failed downstream connection so MCP health remains callable,
and close partially initialized contexts. Automatic reconnect is a separate feature.

### R2: Identifier Serialization

Move the field-aware conversion into a reusable tool-layer utility. Cover `acc_id`,
`position_id`, and `combo_id` in `get_accounts`, `get_assets`, `get_positions`, and
nested `get_account_summary` results. Preserve missing fields, nulls, and existing
strings; do not mutate source records or stringify quantities and monetary values.
Handle Python and NumPy integers. Invalid float/bool identifier values must yield
an explicit serialization error rather than a plausible but corrupted ID.

Tests must invoke the actual MCP tools and inspect both text and structured output.
Calling the helper directly is insufficient to prove every tool uses it.

### R3: Trading Policy

Parse `MOOMOO_TRADING_MODE` once on startup: `READ_ONLY` (default), `SIMULATE`, or
`REAL`; unknown values fail configuration. Construct a policy in `TradeService`,
with a read-only default for direct Python construction too.

| Mode | Account/quote reads and previews | SIMULATE writes | REAL writes | Unlock |
| --- | --- | --- | --- | --- |
| READ_ONLY | Allowed subject to gateway permissions | Denied | Denied | Denied |
| SIMULATE | Allowed subject to gateway permissions | Allowed | Denied | Denied |
| REAL | Allowed subject to gateway permissions | Allowed | Allowed | Allowed |

Guard placement, combo placement, modification, cancellation, and unlock inside
the service before any gateway request. A configured password does not promote a
mode. Only `REAL` mode may attempt auto-unlock. Failed unlock does not change the
policy or trigger automatic order retries. Quote subscriptions remain allowed in
all modes because they do not mutate trading positions or orders.

Keep existing tool `trd_env` defaults; a policy mismatch returns an explicit error,
never silently reroutes an order. Update tool descriptions and README accordingly.
The policy controls the server's capabilities, not user authorization for a trade.

### R4: Combo Preview

Add `preview_combo_order(combo_legs, price, qty, order_type, trd_env, acc_id)` using
`comboorder_tradinginfo_query`. Share leg validation and account selection with
placement. Return the SDK's `nlv_change`, `initial_margin_change`,
`maintenance_margin_change`, `option_bp`, `max_withdraw_change`, and `bp_decrease`
when present, plus an observation timestamp. Preserve unavailable fields as null.

The preview does not submit, reserve funds, unlock trading, verify a fill price,
or guarantee acceptance. Price-sign semantics remain unresolved: do not normalize
price signs or assert a debit/credit convention. The proposed initial interface
previews new orders; previewing modifications using `order_id` is deferred.

### R5: Option Discovery

Expose `get_option_expiration_date(code)` and `get_option_chain(code, start, end,
option_type)`. Validate date ordering and supported option-type values before SDK
calls. Preserve exact contract symbols and expiration metadata. Start with bounded
underlying/date queries; additional SDK filters and strategy generation are deferred.
Return ordinary lists, with empty lists representing no matches.

### R6: Market Sessions

Expose `get_market_state(codes)` and `get_trading_days(market, start, end)` using
the SDK's `get_market_state` and `request_trading_days`. Preserve raw session states
and trading-day metadata, including session type when supplied. Label calendar
dates as market-local and observations in UTC. Calendar membership alone must not
be described as permission to trade a particular instrument.

### R7: Historical Pagination

Add `get_historical_klines_page` with the existing candle filters plus an opaque
cursor. Return `{data, next_cursor, has_more}`. One tool call fetches one SDK page;
clients stop when `next_cursor` is null. Encode the SDK cursor losslessly, binding
it to code, dates, interval, adjustment, and page size; reject mismatched filters.
The old `get_historical_klines` keeps its list response and is documented as one
page. Do not accumulate an unbounded history silently. Empty intermediate pages
with a continuation token still report `has_more=true`.

### R8: Subscriptions

Expose `get_subscriptions` scoped to this connection and
`unsubscribe_market_data(codes, sub_types)` for explicit releases. Report provider
usage/quota fields when available; never invent limits. Existing automatic
subscriptions remain. Surface minimum subscription-duration and permission errors
without retry loops. Do not add global unsubscribe or alter another client's
subscriptions. Background eviction and push-based monitoring are deferred.

## Dependencies and Delivery Order

R1, R2, and R3 are separate foundation slices. Deliver R4 after R2/R3 so previews
share exact IDs and a defined policy. R5 can follow R4 using the existing quote
service. R6, R7, and R8 are independent slices within the final phase. These are
logical dependencies, not a request for parallel agents.

## Migration and Rollback

Announce string account IDs, the new default policy, and additive health fields.
Existing real-trading deployments must explicitly configure `REAL`; paper trading
deployments configure `SIMULATE` and retain explicit environment arguments.
Keep the prior candle tool stable. Update examples before release and use a release
version appropriate for the breaking behavior. Do not silently disable policy
enforcement as a rollback; rollback means restoring a previous release explicitly.

## Risks and Evidence to Resolve

- SDK timeout behavior and broker permissions vary: use bounded mocked failures
  and an optional read-only gateway smoke test before release.
- Preview is a point-in-time account calculation; it is not an execution promise.
- Cursor types and quota response fields must be verified against the installed
  SDK before finalizing schemas.
- Proposed default `READ_ONLY` is intentionally a migration decision requiring
  proposal review; it is not inferred from historical tool defaults.
- Keep current nonconforming/stale OpenSpec deltas separate, and resolve overlap
  with the older account-ID proposal before archival.

## Sources

- [Combo tradable information](https://openapi.moomoo.com/moomoo-api-doc/en/trade/comboorder-tradinginfo-query.html)
- [Option chain](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html)
- Installed SDK: `moomoo/trade/open_trade_context.py`,
  `moomoo/quote/open_quote_context.py`, and associated request serializers.
- Local evidence: `services/base_service.py`, `services/market_data_service.py`,
  `tools/account.py`, and `server.py` under `src/moomoo_mcp`.
