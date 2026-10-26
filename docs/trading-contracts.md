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
- `submission_policy`: `local_rth_submission_time_gate=false`,
  `session_controls="EXECUTION_ELIGIBILITY"`, `broker_acceptance="UNKNOWN"`,
  `day_validity="BROKER_DEPENDENT"`, `fills="UNKNOWN"` and
  `local_scheduling=false`. These describe local behavior, not broker promises.

Market authorization does not prove short-selling or option permissions. See
the official [account fields](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html)
and [product and environment restrictions](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).
Discovery neither unlocks trading nor sends an order.

## Verify the contract actually served

Inspect `tools/list` from the connected MCP endpoint with the same credential
that will make the request. A repository checkout, a plugin's cached tool
declaration and an endpoint's served schema can differ. In particular, check
that placement exposes `session`, mutation `approval_id`, and both
`operation_id` and `admission_epoch`, including for `place_combo_order`.
Tokens are required for journaled mutations: each is a strict string of 1–64
characters, nonblank and printable. A stale declaration that omits these inputs
must be refreshed before constructing a mutation; inventing undeclared arguments
does not establish which server is running.

`tools/list` is an MCP protocol method, not a tool passed to `tools/call`.
After normal initialization, use the established authenticated client to obtain
the actual catalog. For example, its JSON-RPC request to `/mcp` is:

```json
{"jsonrpc":"2.0","id":"fixture-catalog-1","method":"tools/list","params":{}}
```

Inspect `result.tools[].inputSchema` and `description` in that response, rather
than relying on a host's displayed import. Listing tools performs no broker call.

`check_health.mcp_server` reports:

| Field | Meaning |
| --- | --- |
| `package_version` | Installed MCP package version, or `unknown` when unavailable. Different source builds can share a package version. |
| `sdk_version` | Installed moomoo SDK version, or null; the lockfile does not identify a deployed installation. |
| `build_revision` | Null until an embedded build identity is available. |
| `contract_version` | Explicit version of this server's published trading contract. |
| `source_revision` | Embedded source revision when available; currently `unknown`. Never infer a commit from OpenD or a local checkout. |
| `tools_schema_sha256` | SHA-256 of the tools visible to this authenticated caller, including descriptions, input/output schemas and annotations. |

The digest is over every visible tool's JSON serialization with aliases and
without null fields, sorted by tool name. Serialize that array with Python
`json.dumps(definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
and hash its UTF-8 bytes. Compare only within the same credential scope:
restricted clients can have a different catalog and digest. Compare the actual
`tools/list` response as well as the digest; metadata cannot prove what an
external host has cached.

`check_health.market_data` adds layered capability evidence described in
[Broker data evidence](broker-data-evidence.md). A credential with only
`market_read` skips private trade and journal probes; `health_scope` explicitly
reports `MARKET_DATA_ONLY` and trade status is `not_probed`. The aggregate health
status may be degraded because the private service was deliberately not probed.

`gateway_version` identifies OpenD. It does not identify this MCP package,
contract, source build or a host's cached tools. The repository serves stateless
Streamable HTTP at `/mcp` with JSON responses. Its process owns execution state;
an HTTP request ID or connection/session identifier is not an operation ID.
Deployment image identity belongs to the image/release metadata; this package
does not currently embed a source commit. Checking source, lockfile, Docker and
transport configuration requires no production credentials or broker mutation.

External hosts own reconnecting and refreshing their imported tools and
initialization instructions. After an authorized server update, refresh or
reconnect the host, inspect its declarations again, and compare them with the
served catalog. An unchanged or missing field is unresolved drift, not evidence
that the host is using the new contract. This server cannot refresh that cache.

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

### Execution session versus submission time

`session="RTH"` selects execution eligibility. There is no local market-open
submission gate: a request submitted outside regular hours can still reach the
broker, which can accept, reject or retain it under its own rules. `DAY` does not
promise that an outside-hours submission will survive until the next opening.
An acknowledgement does not promise a pending order or a fill.

Treat an explicit instruction to "submit only during regular market hours" as
an additional condition on the agent's call. Immediately before submission,
check the exact instrument's fresh `get_market_state` observation and
`get_trading_days` for the relevant market-local date, holidays and half days.
A calendar date alone provides no session opening/closing times, and a market
state observation can become stale. If these facts do not establish the
authorized submission window, defer without sending. Never turn missing timing
evidence into an assumption that RTH is open.

"Submit this RTH order once now" can authorize immediate submission even when
the market is closed, if the user explicitly accepts that timing. This differs
from "submit once when RTH opens." The server has no future submission scheduler;
do not promise an automatic opening-time call, overnight pending status, next-day
validity or a fill. A later call needs its timing condition checked then.

## Authorize, persist and submit once

Obtain explicit trading authorization for the account, environment and complete
economic intent: instrument, side, quantity, price, order type, validity, session
and submission timing. Read-only inspection or a preview is not authorization.
Never infer permission to switch REAL/SIMULATE or choose another account from
an error. Account and broker order identifiers remain exact decimal strings.

Generate `uuid.uuid4().hex` once for each newly authorized intent. Before any
preparation or mutation call, durably persist the exact method and arguments,
including the explicit account, environment, operation ID and current admission
epoch. Paper obtains the epoch from `check_health.execution_journal`; REAL uses
`check_health.real_execution_journal`. Each token must be nonblank, printable
and 1–64 characters. A UUID hex token has 32 characters; JSON-RPC request IDs
and broker order IDs serve different purposes.

Reload that record after a crash or reconnect. Never regenerate an ID, refresh
its epoch or construct a replacement order because a result was lost. Existing
operations retain their original epoch even after a server restart. A current
epoch is for newly authorized decisions, not recovery of an earlier request.

Submit the exact authorized mutation once. A transport timeout, cancellation,
disconnect, unreadable receipt or uncertain outcome leads to read-only
inspection: `get_execution(operation_id, trd_env=original_environment)` and
current/history broker orders scoped to the same account and environment.
`reconcile_execution` advances the server's query-only recovery when due.
Do not promise the absence of an order from an empty scoped query.

Identical journaled calls are defensively deduplicated by the server and return
stored state or `IN_FLIGHT`. This does not authorize a client's automatic
resend. Preserve acknowledgements, requested and executed quantities, raw broker
status, and uncertainty separately; admission and acknowledgement are not fills.

### Input rejection and permitted technical correction

Mutation errors carry dispatch evidence. Interpret the scope, not just a label:

| Evidence | What it establishes |
| --- | --- |
| `validation_phase="INPUT_VALIDATION"`, `journal_record_created=false`, `journal_record_scope="THIS_INVOCATION"`, `mutation_outcome="NOT_SENT"` | This invocation failed input validation before creating a journal row or dispatching. `identity_status="UNKNOWN"` and `prior_attempt_outcome="UNKNOWN"` leave earlier attempts unresolved. |
| `validation_phase="PRE_DISPATCH"`, `journal_record_created=null` | This call was refused before dispatch; global journal absence is unknown. |
| `validation_phase="ADMITTED"`, `journal_record_created=true` | This invocation has authoritative durable admission evidence; a later refusal does not erase it. |
| `validation_phase="DISPATCHED"` or `"ACKNOWLEDGED"` | Preserve the dispatch outcome even if nested validation also failed. Inspect the original operation and broker evidence. |

`validation_issues` names the input path, type, field and constraint, with token
bounds (`min_length=1`, `max_length=64`) and `actual_length` for strings. It
does not echo the supplied token. A missing runtime token has `constraint="required"`.
For example, a 66-character operation token is invalid. These errors have
`retryable=false`: repeating the failed request unchanged is not a repair.

A permitted technical correction requires both existing authorization for the
same economic intent and evidence that the invalid request was never admitted
or sent in any attempt. Respect a literal user limit of one tool invocation:
even an unsent input failure does not authorize a second call under that limit.
For a known first invocation rejected solely for token
length, correcting the token format can remain within that authorization. A
durable caller record should retain the rejection and the explicit correction;
do not silently overwrite it. Changing account, environment, price, size,
instrument or timing requires authorization for that change.

An approval for an exact operation ID does not transfer to a corrected ID.
Prepare the corrected exact request and obtain a fresh operator decision when
approval is required. If the user authorized only a specific ID, changing that
ID also needs their authorization. A later invocation's input rejection never
clears an earlier uncertain attempt: recover the original persisted identity
and inspect it. There is no implicit correction or replacement path in the
reference recipe below.

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
ordinary MCP clients and agents. The base Compose file forwards these settings,
preserving omitted versus explicitly empty symbol restrictions and daily caps.
Symbol restrictions alone need no database.

For daily budgets, approvals, or an operator channel, add
`docker-compose.controls.yml` after your execution-journal overlays. It mounts
`trading-controls-data` at `/var/lib/moomoo/trading-controls` and fixes
`MOOMOO_TRADING_CONTROLS_PATH` to that directory's `controls.sqlite3`. The image
prepares the directory for UID 10001. `scripts/compose-prod.sh` selects the
overlay automatically for configured controls in SIMULATE or REAL, including
deployments with a ChatGPT tunnel. The Ansible migration remains READ_ONLY.

For example, a manual REAL deployment with approvals uses:

```sh
docker compose -f docker-compose.yml -f docker-compose.real.yml \
  -f docker-compose.controls.yml up -d
```

Set `MOOMOO_CREATE_TRADING_CONTROLS=1` only for deliberate first initialization;
after it succeeds, restore `0` and recreate with the same command. Missing
storage then refuses startup. Preserve the controls volume and the execution
volumes through upgrades and mode changes. For direct Python execution, supply
your own durable controls path and export the settings into the process.

## Prepare, review and approve

1. Read `get_accounts`, `check_health` and `get_trading_capabilities` for the
   intended account and environment. Persist a caller-owned `operation_id` and
   the current environment's admission epoch: paper uses `execution_journal`,
   REAL uses `real_execution_journal`, together with the complete authorized
   request before calling preparation. Preserve them for later inspection.
2. Call `prepare_trade_request(method, parameters, ttl_seconds=300)`. Supported
   methods are `place_order`, `place_combo_order`, `modify_order` and
   `cancel_order`. Supply that method's arguments and durable identity. The
   server expands defaults, resolves an explicit account and stores the exact
   intent and review facts. Preparation changes only controls storage and
   performs broker reads for review, with no broker mutation or unlock. Persist
   the returned `request_id`, complete parameters and review immediately.
   Preparation is not idempotent: calling again creates another request ID.
   A lost preparation reply is unresolved preparation, not permission to create
   another approval request automatically. TTL must be 1–900 seconds; canonical
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
   epoch. A changed request cannot reuse the decision, including an operation
   ID format correction. `get_prepared_trade` reads the stored request and
   decision; it does not refresh preview facts, journal readiness or the epoch.
   `APPROVED` can therefore be stale for current admission after a restart.
6. Submit once and inspect the journal result. Defensive identical admitted
   calls return stored state or `IN_FLIGHT` without another SDK mutation or
   duplicate daily charge; clients must not turn this into automatic retries.
   `CONSUMED` approval does not prove dispatch, because consumption precedes
   unlocking and dispatch. An uncertain outcome remains a recovery task; a new
   operation ID represents a separately authorized new decision.

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

### Tested Python caller recipe

[`examples/trading_client.py`](../examples/trading_client.py) provides a small
local persistence example, tested in
[`tests/test_trading_client_recipe.py`](../tests/test_trading_client_recipe.py).
It has no transport setup, credentials, broker connection, approval polling,
automatic correction or retry loop. Inject the host's existing async
`call_tool(method, parameters)` adapter; it must return one call result or raise
without retrying a mutation internally. SDK adapters must still inspect MCP
`isError` and preserve text/structured error evidence.

After obtaining authorization and observing the current epoch, create the
record once. The values below are synthetic; this snippet does not connect to
a server:

```python
from pathlib import Path

from examples.trading_client import load_intent, persist_authorized_intent, submit_once

# Existing private directory on durable local POSIX storage, outside the repo.
intent_path = Path("/private/client-state/authorized-intent.json")
intent = persist_authorized_intent(
    intent_path,
    "place_order",
    {
        "code": "US.FIXTURE",
        "price": "1.00",
        "qty": 1,
        "trd_side": "BUY",
        "trd_env": "SIMULATE",
        "acc_id": "900000000000000001",
        "order_type": "NORMAL",
        "time_in_force": "DAY",
        "session": "N/A",
        "adjust_limit": 0,
        "aux_price": None,
        "trail_type": None,
        "trail_value": None,
        "trail_spread": None,
        "remark": "",
        "admission_epoch": "fixture-epoch-0001",
    },
)
# After restart, use load_intent(intent_path); never recreate the intent.
```

Use `intent["method"]` and `intent["parameters"]` for the explicit preparation
flow above when controls are configured. Keep the returned preparation ID and
review durably in your existing caller state. After the operator decision,
read `get_prepared_trade` for that exact request ID. Show the expanded parameters
and review, check any submission-time condition, then pass that response as
`prepared_trade=approved` to `await submit_once(intent_path, call_tool, ...)`.
Without required operator approval, call `await submit_once(intent_path, call_tool)`
only after the same authorization and timing checks.

The recipe refuses a prepared response unless it is `APPROVED`, unconsumed and
matches the stored method and complete persisted parameter object, including
JSON value types. Before authorization and persistence, inspect the selected
mutation's served schema and explicitly include its authorized defaults as in
the example. Added prepared fields fail closed even when they equal a default;
operator approval cannot supply economic terms omitted from the persisted user
intent. It also refuses semantically equivalent changes. Resolve a mismatch
through explicit review; never overwrite an attempted intent or transfer its
approval. The server remains authoritative for approval authentication, expiry,
admission and fresh controls.

Persistence writes a private complete temporary file, flushes and `fsync`s it,
atomically publishes it without overwriting an existing file, then `fsync`s the
directory. Before transport, the same primitive records the exact outgoing
request and approved snapshot in `<intent-name>.attempt.json`. Re-entry refuses
once that file exists, even after an error, timeout, cancellation, process
restart or a crash between recording the attempt and invoking transport.
Persistence failure prevents the call. This conservative gap can leave a
recorded attempt that was never sent; inspect rather than deleting the marker.

The files are caller bookkeeping, not a second execution journal. Preserve them
on durable storage, do not put them in a public repository, and do not use a new
path to bypass an uncertain operation. Multi-host coordination, approval
creation/recovery, scheduling, and broker recovery remain with the existing
host and server workflows. The tests use temporary directories and injected
fake transports; they make no broker calls in either environment.

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
# Execution lifecycle and client permissions

`MOOMOO_TRADING_MODE` caps deployment writes. `REAL` permits both live and
configured paper execution; paper retains its own allowlist, journal and verified
US stock/ETF NORMAL/DAY/RTH scope. The same account eligibility now governs
capability discovery, approval preparation and mutation execution.

An optional `MCP_RESTRICTED_AUTH_TOKEN` provides a second client credential. It
must differ from the ordinary MCP and operator tokens. Configure its grants with
`MCP_RESTRICTED_PERMISSIONS`, a comma-separated subset of `market_read`,
`real_read`, `simulate_read`, `simulate_write`, `real_write`, and `gateway_control`.
The default includes market/account reads and paper writes; it excludes live
writes and gateway control. `MCP_RESTRICTED_REAL_ACC_IDS` and
`MCP_RESTRICTED_SIMULATE_ACC_IDS` optionally restrict readable/writable accounts
to exact decimal IDs. Omitted account scopes retain the permissions' account
scope; explicitly malformed or empty scopes fail startup. Client grants always
intersect with the deployment policy, allowlists, executor support and readiness.

The ChatGPT tunnel selects the restricted token when configured. A REAL backend
requires this distinct credential without `real_write` or `gateway_control`.
Ordinary MCP clients retain their existing deployment authority. Restricted
clients cannot use operator endpoints, inspect another account through cached
pagination, or recover stored execution evidence outside their readable scope.

## Pause new risk

Configure the separate operator credential, operator ID and initialized controls
storage described above. The authenticated operator endpoint is
`/operator/execution/{trd_env}`, where `trd_env` is `REAL` or `SIMULATE`.

- `GET` returns the persistent pause state; optional `?acc_id=...` includes that
  account's pause and the environment-wide pause.
- `POST` accepts `{"paused": true, "reason": "operator review"}`. An optional
  exact string `acc_id` scopes the decision to an account; omitted/`"0"` scopes
  it to the environment. Resume with `paused: false` and an attributed reason.

Pauses survive restarts and block exposure-adding dispatch. Environment and
account pauses combine: resuming one account does not override an environment
pause. A successful pause decision serializes with both execution dispatch paths.
Cancellations and broker-query recovery remain available subject to their normal
authorization, approval and journal safety checks. An unresolved outcome still
requires reconciliation; pausing does not bypass that gate. Gateway `lock_trade`
remains separate from this persistent operator pause.

## Observe after disabling writes

Ordinary READ_ONLY requires no journals. Set `MOOMOO_OBSERVE_EXECUTION=1` and
explicit existing `MOOMOO_JOURNAL_PATH` and/or `MOOMOO_REAL_JOURNAL_PATH` to retain
inspection and query-only recovery outside writable modes. Observation never
initializes journals or enables broker mutations. Only one process owns each
journal. Existing configured controls remain inspectable through the trusted
operator channel. Keep the same persistent storage across mode changes.

## Common limit defaults

Source and Compose launches default `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` to
`USD:500` when omitted or blank. Choose explicit currency-qualified caps for the
intended order sizes, or explicitly set `NONE` to disable this cap. This changes
the earlier source-launch default, which had no notional cap. Instrument support
and all other policy checks still apply.
