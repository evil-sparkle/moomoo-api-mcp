# Trading inspection, review and execution contracts

The server exposes documented Moomoo OpenD features together with its own
validation, result handling and optional trading controls. All MCP clients use
the same contracts. The implementation uses the SDK pinned in `uv.lock`
(`moomoo-api 10.10.7008`); documentation and mocked SDK tests establish source
support, while deployed OpenD versions, account entitlements and live acceptance
still require separate verification.

## Inspect support and readiness

Call `get_trading_capabilities(code, trd_env, acc_id="0")` before planning a
trade. Supply an exact string account ID from `get_accounts`. The zero default
resolves only when one discovered account matches the environment; ambiguity
is reported rather than selecting an account silently.

The response separates:

- `supported`: conservative documented order types, sessions, query and event
  support for the product and account. Unverified support remains `null`.
- `allowed`: trading mode, account and symbol restrictions, executor scope,
  per-order caps, daily caps and operator-approval requirements.
- `ready`: account evidence, the appropriate environment's journal state,
  execution halt, unlock strategy and controls health. `READY` is advisory;
  broker acceptance remains unverified.

Market authorization does not prove short-selling or option permissions. See
the official [account fields](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html)
and [product and environment restrictions](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).
Discovery neither unlocks trading nor sends an order.

## Instrument validation and sessions

Single-order placement and exposure-adding modification require verified stock,
ETF or option classification, including when no notional cap is configured.
Whole quantities are checked without truncating a fractional broker quantity.
Price validation uses Decimal arithmetic and refuses excess precision rather
than silently rounding the submitted price.

| Instrument | Locally established price rule |
| --- | --- |
| US stocks and ETFs below $1 | Multiples of $0.0001. |
| US stocks and ETFs at or above $1 | Multiples of $0.01. |
| US options | At most two decimal places; product-specific minimum tick remains unknown. |
| A-share equities with SH/SZ codes | Multiples of 0.01. |
| Other supported securities | At most three decimal places; exchange/product tick remains unknown. |

An option's static `lot_size` is deliverable shares per contract; its premium
multiplier is a separate broker field. A stock's lot size describes shares per
lot. Metadata does not automatically establish a permitted lot increment, and
snapshot `price_spread` does not establish a universal tick schedule. The broker
continues to enforce product-specific rules. See [static instrument fields](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-static-info.html),
[placement precision](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html)
and [tick-size guidance](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).

Static classification and lot metadata use a bounded cache: 2,048 instruments,
300-second lifetime, invalidated when the quote-context object changes. Every
lookup obtains fresh price and option-multiplier snapshots.

`place_order` and `preview_order` accept `session="N/A"` by default. For REAL,
`N/A` preserves the broker's default; explicit sessions require verified US
stocks/ETFs. RTH permits the supported order types, while this server limits
ETH, OVERNIGHT and ALL to NORMAL limit orders. DAY/GTC remain the exposed
time-in-force choices. Product and account restrictions still apply. The
deprecated `fill_outside_rth` parameter is replaced by `session`.

Paper execution retains US stocks/ETFs, whole shares, NORMAL/DAY/RTH. Its
default and explicit RTH have the same retry identity; ETH, OVERNIGHT and ALL
are refused. This executor scope is narrower than the broker's paper API.
Explicit REAL session changes are part of durable operation identity, so the
same operation ID cannot be reused to change a session.

## Preview an order

`preview_order` takes the single-order trading arguments, including code,
price, whole quantity, side, environment, order type, time in force, account,
trigger/trailing parameters and session. Its default account is `"0"` and
default session is `"N/A"`.

The response includes instrument-rule facts, conservative submission valuation
when it can be established, and `maximum_quantities` from
[`acctradinginfo_query`](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-max-trd-qtys.html).
Derivative initial-margin fields have the provider's documented restrictions;
this composed preview does not provide universal stock account-impact or fee
estimates. A preview performs no unlock, broker mutation or durable admission.

`preview_combo_order` continues to use the broker's native
[combo margin and buying-power preview](https://openapi.moomoo.com/moomoo-api-doc/en/trade/comboorder-tradinginfo-query.html).
Combo premium valuation bounds submission value rather than maximum strategy
loss. A preview's observations can change before dispatch.

## Exact order reads, fees and pages

| Tool | Contract |
| --- | --- |
| `get_order` | One exact order ID, with `source="CURRENT"` or `"HISTORY"`, account, environment, scope and fetch time. |
| `get_order_fees` | Actual reported fees for 1–400 exact order IDs. REAL only; Moomoo CA is unsupported. |
| `get_orders_page` / `get_deals_page` | Normalized current activity from an immutable server snapshot. Deal reads are REAL only. |
| `get_history_orders_page` / `get_history_deals_page` | Normalized historical activity with the original filters, date scope and fetch time. Historical deal reads are REAL only. |

`get_order(source="CURRENT")` uses the SDK's order-ID filter. Current orders
cover open orders and recent terminal orders, including filled/cancelled orders
within 24 hours; this is not a calendar-day ledger.
`source="HISTORY"` requires explicit `start` and `end` date or timestamp bounds,
with at most 366 days between their dates. The historical SDK method has no
order-ID parameter, so the server queries that bounded scope and matches the
ID locally. Duplicate matches are an explicit ambiguity error. `found=false`
means absent from the returned scope, rather than proof that an order never
existed. See [current orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-list.html)
and [historical orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html).

Typed order views retain requested `qty`/`price`, executed
`dealt_qty`/`dealt_avg_price`, raw status and additional fields under `raw`.
Deal views describe actual fill quantity/price. Missing numeric facts stay
`null`; identifiers remain exact strings. A cancelled partially filled order
retains its executions. Scope and broker values do not establish independently
reconciled or complete lifetime accounting.

Pages return `items`, `next_cursor`, `total_rows`, `returned_rows`, `scope` and
`fetched_at`. Repeat every filter, account, environment and `limit` unchanged
with `next_cursor`; stop when it is `null`. Continuations use local immutable
data and perform no SDK query. All page tools default to `limit=100`, allow
1–200 rows and enforce these process-wide bounds:

| Resource | Bound |
| --- | --- |
| Snapshot lifetime | 120 seconds; eviction or process restart can invalidate it earlier. |
| Rows per snapshot | 10,000. |
| Serialized bytes per snapshot | 4 MiB. |
| Retained snapshots | 16. |
| Total serialized snapshot cache | 16 MiB. |

Oversized responses fail explicitly; narrow the query rather than assuming a
truncated result is complete. Expired or evicted cursors require a new initial
fetch. Historical page defaults preserve the SDK's 90-day rules documented in
the [README](../README.md#history-coverage-and-executions); continuation retains
the first resolved range rather than recomputing omitted dates.

Fee reads use a separate account-scoped budget of ten requests per 30 seconds.
The provider supports orders after 2018-01-01, may omit unavailable fee rows,
and supplies no currency field for this API. See the
[actual-fee API](https://openapi.moomoo.com/moomoo-api-doc/en/trade/order-fee-query.html).
The legacy list tools retain their list-shaped response contracts. Both current
and historical deal tools explicitly refuse SIMULATE, consistent with the
[provider's deal-query restriction](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-fill-list.html).
Use paper order executed quantities and positions as supporting evidence.

## Callback observations

`get_trade_events(trd_env, acc_id="0", after_sequence=0, event_epoch=null,
limit=100)` reads one exact account's process-local observations. Order events
support REAL and SIMULATE; deal events support REAL only. The ring holds at most
1,024 events for 900 seconds (15 minutes) across all accounts. Pages allow
1–200 events.

Continue with the returned `event_epoch` and `next_sequence` together. The
response reports `has_more`, `retention_gap`, `restart_gap` and
`reconnect_count`. A stale process epoch returns a restart gap and the current
epoch. Retention gaps conservatively indicate that matching account events
may have been lost. Reconnects can miss events even without a retention gap.

Callbacks may arrive before a placement response. The server stores exact
broker order IDs immediately and obtains account scope from the protobuf
header, since the pinned SDK's decoded callback rows omit it. It invents no
caller-operation correlation. Callback absence proves neither order absence
nor fill absence, and this buffer never supplies durable journal recovery
evidence. See the official [order callback](https://openapi.moomoo.com/moomoo-api-doc/en/trade/update-order.html),
[deal callback](https://openapi.moomoo.com/moomoo-api-doc/en/trade/update-order-fill.html)
and [environment matrix](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).

## Enable optional controls

Existing deployments retain disabled daily budgets and operator approval,
with no symbol restriction when the setting is omitted. A READ_ONLY server
continues to refuse every trading mutation and does not need controls storage.
The following settings must reach the actual server process environment:

| Setting | Meaning |
| --- | --- |
| `MOOMOO_SYMBOL_ALLOWLIST` | Optional comma-separated exact codes, such as `US.AAPL,US.MSFT`. Every combo leg and exposure-adding modification is checked. An explicitly empty value is invalid. |
| `MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY` | Optional positive caps, such as `USD:1000`. An explicitly empty value is invalid. |
| `MOOMOO_REQUIRE_TRADE_APPROVAL` | `0` by default; `1` requires a trusted stored decision for every trading mutation, including cancellation. |
| `MOOMOO_TRADING_CONTROLS_PATH` | Dedicated persistent SQLite file, distinct from either execution journal. Required for budgets, approvals or an enabled operator channel in a writable mode. |
| `MOOMOO_CREATE_TRADING_CONTROLS` | `0` by default; set `1` only for deliberate first initialization, then return to `0`. |
| `MOOMOO_OPERATOR_AUTH_TOKEN` | Distinct operator bearer credential, different from `MCP_AUTH_TOKEN`. Required when approval is enabled. |
| `MOOMOO_OPERATOR_ID` | Server-configured printable identity, at most 128 characters, required with an operator credential. |

Use [.env.example](../.env.example) as a public template. Inject private tokens
through the operator-managed environment; keep the operator credential outside
ordinary MCP clients and agents. For containers, arrange explicit environment
forwarding and a persistent writable mount for the controls directory. A host
environment file supplies Compose interpolation; values reach Python only when
the deployment forwards them.

## Prepare, review and approve

1. Read `get_accounts`, `check_health` and `get_trading_capabilities` for the
   intended account and environment. Choose a caller-owned `operation_id` and
   the current environment's admission epoch: paper uses `execution_journal`,
   REAL uses `real_execution_journal`. Preserve both across retries.
2. Call `prepare_trade_request(method, parameters, ttl_seconds=300)`. Supported
   methods are `place_order`, `place_combo_order`, `modify_order` and
   `cancel_order`. Supply that method's arguments and durable identity. The
   server expands defaults, resolves an explicit account and stores the exact
   intent and review facts. Preparation changes only controls storage and
   performs no broker mutation or unlock. TTL must be 1–900 seconds; canonical
   request and review data are individually bounded to 64 KiB.
3. Show the complete returned review and parameters to the operator. The
   operator independently reads the same request through the private HTTP
   channel below, using the operator credential.
4. The operator submits an `APPROVED` or `REJECTED` decision with a reason.
   `MOOMOO_OPERATOR_ID` supplies decision identity; callers cannot choose it.
   Decisions are final, expire at the original request expiry, and persist
   with their reason and decision time. When fresh facts change a reviewed
   exposure contract, prepare and review it again.
5. Read `get_prepared_trade(request_id)` through MCP to observe status. After
   approval, use the response's top-level `method` to select the original
   mutation and pass its `parameters` plus `approval_id=request_id`.
   Preserve the exact order, account, environment, operation ID and admission
   epoch. A changed request cannot reuse the decision.
6. Inspect the journal result. Identical admitted retries return stored state
   or IN_FLIGHT without another SDK mutation or duplicate daily charge. An
   uncertain outcome remains a recovery task; a new operation ID represents a
   new trading decision rather than a retry shortcut.

The operator route is on the existing authenticated server, under
`/operator/approvals/{request_id}`. Keep access on the deployment's existing
private transport. It requires the separate operator token for both methods;
ordinary MCP credentials cannot approve. There is no approval MCP tool and no
`approved=true` shortcut. Request and decision bodies below are illustrative
HTTP templates, with placeholders rather than credentials:

```http
GET /operator/approvals/<request-id> HTTP/1.1
Host: 127.0.0.1:8000
Authorization: Bearer <operator-token>
```

```http
POST /operator/approvals/<request-id> HTTP/1.1
Host: 127.0.0.1:8000
Authorization: Bearer <operator-token>
Content-Type: application/json

{"decision":"APPROVED","reason":"Reviewed the exact account, environment and order details."}
```

POST accepts exactly the string fields `decision` and `reason`, at most 8 KiB
of body data, and a nonblank reason of at most 2,000 characters. For rejection,
use `"decision":"REJECTED"`. The same host/origin protections as MCP apply.

## Daily submission budgets and recovery

Daily limits measure conservative submitted value, keyed by UTC day, account,
environment and currency. A BUY fixed-limit order uses its limit price;
other orders use the policy's conservative reference price and verified
multiplier. Combo value measures absolute package premium. Unknown currencies
or valuation facts are refused when a daily cap must apply.

New placements and exposure-adding modifications charge their full assessed
submission value, rather than incremental portfolio exposure. The server
atomically consumes approval and commits the charge before unlocking or broker
dispatch. A charge can remain after a known-unsent unlock failure, a crash or
an uncertain outcome. This conservatism prevents a restart from reopening the
daily budget. Cancellations add no positive exposure charge and never refund
previous charges; they still require approval when enabled. Existing journal
and recovery blocks continue to apply.

After unlocking, the server checks approval expiry, the approved target
contract and the reservation's UTC day again before writing the journal's
dispatch marker. An expired approval or a crossed UTC day refuses dispatch.
The previous day's charge remains recorded, and this check does not create a
new charge for the new day.

This is a submission budget, not actual filled turnover, realized loss, net
exposure or buying power. Preserve the controls store together with both
environment-owned journals across restarts and rollback. There is no refund or
reset endpoint. Deleting storage is not a recovery procedure.

## Migration and verification

- Existing list-query callers can migrate gradually to typed single-order and
  page tools. Prices, requested quantities, executed quantities and raw broker
  status stay distinguishable.
- Paper clients that called deal-query tools must use current/historical order
  executed quantities. Deal APIs are explicitly REAL only.
- Price validation now needs instrument metadata for placement and
  exposure-adding modification even without a configured notional cap. Missing
  classification is a refusal before broker dispatch; explicit sessions add
  the documented US equity restrictions.
- Preserve old operation identity on retries. Default REAL session N/A and
  paper default/RTH remain compatible with existing records. A meaningful
  explicit REAL session change conflicts with the old operation.
- Enable controls deliberately after preparing durable storage and a separate
  operator access path. Keep each existing journal and controls database on
  persistent storage; use creation flags only for initial setup.

Source validation and mocked tests cover SDK arguments, exact identifiers,
quotas, cursor bounds, concurrent callbacks and durable control decisions.
They do not prove live broker behavior or complete the separate paper-provider
validation change. Verify deployed readiness and authorized integration
behavior independently before claiming live trading acceptance.
