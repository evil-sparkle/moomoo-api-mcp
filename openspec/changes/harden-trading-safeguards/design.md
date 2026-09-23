# Design

## Context

See `proposal.md` for the motivation. The design depends on these facts about the
current code and the SDK. They were checked against `main` at `f0ae2ef` and against
`moomoo-api` in the dev venv.

- **Limits are checked early but not reliably.** `TradingPolicy.check_order_limits`
  runs before the gateway call. It skips the notional check when the price is
  missing or `<= 0`. It has no multiplier or currency. `from_env` accepts `nan` and
  `inf`, because `float("nan") <= 0` is false.
- **`modify_order` passes a zero quantity.** It sends `qty=0` to the limit check
  when only the price changes. It has no view of the existing order.
- **REAL writes can reach the wrong account.** Tools default `trd_env="REAL"`, while
  service methods default `"SIMULATE"`. `acc_id == 0` resolves through
  `_find_best_account`, which returns the first account in the environment whose
  `trdmarket_auth` contains the market.
- **The SDK already handles what startup auto-unlock did.**
  - `OpenSecTradeContext.unlock_trade` fetches the account list itself
    (`_check_acc_id`), so the startup `get_accounts()` before unlock is redundant.
  - The SDK caches a successful unlock in `_ctx_unlock`. `on_api_socket_reconnected`
    replays that unlock after every reconnect.
  - A lock (`is_unlock=False`) clears the cache.
  - So startup auto-unlock leaves the gateway unlocked across reconnects. The JIT
    relock clears that state, but only when a write happens.
- **JIT relock failures are swallowed.** `_jit_trade_unlock` reads credentials from
  `os.environ` on every call. It swallows a relock failure with `logger.error`. The
  receipt is not lost today, because the exception is caught inside `finally`.
  Nothing stops the next write.
- **Response conversion can fail after a successful write.** Write methods convert
  the SDK response (`as_frame(...).to_dict`) inside the unlock block. A conversion
  error after `RET_OK` escapes as an ordinary exception, and `ret != RET_OK` becomes
  `RuntimeError("place_order failed: …")`. Neither tells the caller whether the
  request may have been sent, or whether the gateway acknowledged it.
- **Configuration errors surface late.** `TradingPolicy.from_env()` runs in
  `_build_services()`, lazily on the first request. A configuration error therefore
  surfaces on the first tool call, not at process start.
- **The notional check needs two reads, not one.** Verified against `moomoo-api`
  10.10.7008 in the dev venv.
  - `get_market_snapshot` needs no subscription and returns `last_price`,
    `bid_price`, `ask_price`, `lot_size` and, for options,
    `option_contract_size` and `option_contract_multiplier`.
  - It does **not** return a security classification. `MarketSnapshotQuery` carries
    no `sec_type`, `stock_type` or `security_type` field. Classification comes from
    `get_stock_basicinfo`, which returns `stock_type`.
  - It does **not** return a currency field.
  - `bid_price`, `ask_price` **and `option_contract_multiplier`** are the string
    `'N/A'` when the gateway omits them, so quote fields are not reliably numeric.
    Normalization covers all three, not only the two price fields (task 1.1).
  - `option_contract_size` is copied out of an optional proto field with no
    `HasField` guard, so an omitted value arrives as `0.0` and cannot be told apart
    from a real zero (task 1.1). A non-positive multiplier is therefore treated as
    absent, never as zero.
- **`order_list_query` can target one order.** It accepts `order_id`.

## Goals / Non-Goals

**Goals:**

- Every order-mutating path goes through one pre-dispatch sequence, taking the
  steps that apply to it, in this order:
  1. validate;
  2. check the halt;
  3. resolve the account;
  4. assess limits;
  5. unlock, dispatch and relock.

  Steps 2 and 4 are for exposure-adding writes. An exposure-reducing one — a
  cancellation, or a `CANCEL`/`DISABLE`/`DELETE` modification — skips both by
  design: an operator facing a halt has to be able to pull orders, and pulling
  one has no notional to measure.

  Every refusal happens before the single SDK write call. The halt comes before
  the account resolution because resolving the default `acc_id="0"` reads the
  account list from the gateway, and a halted write is refused before any
  gateway request at all, read or write. The authoritative halt check is the one
  inside the just-in-time lock; this one only saves the work.
- The policy stays pure and unit-testable. Gathering the facts it needs, such as
  instrument data and the existing order, is the trade service's job.
- Configuration is parsed and validated once, at process start, before any
  transport is served.

**Non-Goals:**

- Aggregate exposure across working orders and positions, and maximum-loss
  modelling.
- Persisting the halt across restarts. The persistent operator pause belongs to
  Stage 4.
- Operation IDs or duplicate suppression. That is Stage 2. This change only makes
  the dispatch boundary visible in errors.
- Restructuring `TradeService` beyond what these paths need.

## Decisions

### 1. One settings object, loaded before serving

Add `moomoo_mcp/settings.py` with `load_settings(environ) -> Settings`. The frozen
dataclass holds:

- the OpenD host and port;
- the `TradingPolicy`, including its limits and the REAL account allowlist;
- the stored trade credential, which is plain text or MD5, with plain text taking
  precedence;
- the security firm, validated against `SecurityFirm`;
- the transport;
- the auth token and the unauthenticated-HTTP opt-out.

`main()` calls it first, so invalid configuration exits before anything listens.
`_build_services()` reuses the loaded settings and no longer reads `os.environ`
itself. `TradeService` receives the credential in its constructor, replacing the
`os.environ` reads inside `_jit_trade_unlock`.

- *Alternative:* keep `from_env()` inside `_build_services`. It was rejected
  because configuration errors would still appear only on the first tool call. The
  supervisor would then keep a server running whose every request fails.
- *Consequence:* a configuration error makes the MCP process exit. The supervisor
  then stops OpenD, and `restart: unless-stopped` restarts the container repeatedly.
  The log line names the variable. This is the intended fail-closed behaviour, and
  the deploy runbook documents it.

### 2. Limit configuration and currency

- **Validation.** `TradingPolicy.__post_init__` validates `max_order_qty` and each
  notional cap with `math.isfinite(x) and x > 0`. Direct construction is therefore
  covered too, not only `from_env`.
- **Format.** The caps come from a new variable,
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`. The policy field `max_order_notional`
  becomes `Mapping[str, float]`, keyed by an upper-case currency.
- **Legacy variable.** `load_settings` reads `MOOMOO_MAX_ORDER_NOTIONAL` only to
  classify it:
  - Absent: nothing happens.
  - Present alongside the new variable: it is ignored, and a single INFO log line
    says so.
  - Present alone: `TradingModeConfigError` naming the new variable.

  The value is never parsed as a limit.
- **Parsing.** Parse with `split(",")`, then `partition(":")`, with explicit
  checks: a three-letter alphabetic code (`isalpha()`) and no duplicates. No regex.
- **Currency.** The snapshot carries no currency field (verified against
  `moomoo-api` 10.10.7008), so currency has to be established another way.

  A market prefix is **not** an instrument's currency, and this change does not treat
  it as one. A prefix names a venue; instruments on one venue may quote in more than
  one currency, HK dual-counter (HKD/RMB) being the standing example. The prefix is
  therefore used only as a **verified per-market subset**: a market appears in the
  table only once it has been confirmed that every instrument this server will value
  on that venue quotes in the stated currency.

  | Market prefix | Currency | Status |
  | --- | --- | --- |
  | `US` | `USD` | provisional — task 1.1 confirms |

  Live evidence on 2026-09-23 returned USD for the sampled US paper order and
  US stock/ETF/option positions. This confirms those samples, not every instrument
  the `US` subset can admit; the table remains provisional. The snapshot still
  provides no currency field. See `verification.md`.

  No other prefix is supported while a notional cap is configured, and an instrument
  whose currency cannot be established this way is **refused**, not valued at a
  guessed currency. Adding a market to the table is a deliberate act backed by
  verification, not an inference from its prefix.

  This is deliberately narrower than the markets the server can trade. A cap that
  cannot be expressed in the instrument's own currency is not applied loosely; the
  order is refused.

- *Alternative:* keep one unit-less cap and ignore currency. It was rejected because
  a 25,000 cap means roughly 7.8× different exposure in USD and in HKD.
- *Alternative:* a single cap plus a `…_CURRENCY` variable. It was rejected because
  it cannot express a second market without another format change later.
- *Alternative:* reuse `MOOMOO_MAX_ORDER_NOTIONAL` with the new format. It was
  rejected for rollback reasons. The previous image parses that variable with
  `float()` and fails on `USD:…`. A rollback would then require editing `.env` under
  pressure. With a new name, one `.env` serves both images: the old image enforces
  its unit-less cap, and the new one enforces currency caps.
- *Alternative:* use a currency field from the snapshot. It is not available:
  `MarketSnapshotQuery` exposes no currency field in `moomoo-api` 10.10.7008. Should a
  future SDK add one, it would take precedence over the verified-market table without
  changing the approach.

### 3. Notional assessment: the policy decides, the service gathers facts

The policy gets a pure method:

```
assess_order(operation, OrderFacts) -> None   # raises TradingPolicyError
```

`OrderFacts` carries:

- the order type and side;
- the quantity;
- the price and trigger price;
- the legs, each with a ratio and its `InstrumentFacts`.

`InstrumentFacts` holds the code, currency, security classification, monetary
multiplier, and normalized `last_price`, `bid_price` and `ask_price`.

`TradeService` builds `OrderFacts`. It receives `instrument_lookup`, a callable
`codes -> list[InstrumentFacts]`, wired in `server.py` to an **instrument adapter**
over the shared quote context. The lookup is called only when a notional cap is
configured.

The adapter performs two reads per assessment and combines them:

1. `get_market_snapshot(code_list)` — prices, `lot_size`, and the option contract
   fields.
2. `get_stock_basicinfo(market, stock_type, code_list=...)` — the security
   classification (`stock_type`). The call passes an **explicit `code_list`** so it
   returns the requested instruments rather than enumerating a market.

Both calls are made with the codes the order names, and both must succeed for the
instrument to be assessable.

**Quote normalization precedes assessment.** The adapter converts each quote field to
a number or to `None` before the policy sees it. A value that is absent, non-numeric
(including the `'N/A'` sentinel), non-finite, or not greater than zero becomes `None`.
The policy therefore never performs arithmetic or a finiteness test on a string, and
`assess_order` stays pure over numbers. Normalization is the adapter's job precisely
so the policy has no gateway-shaped edge cases in it.

**The adapter sits inside the pre-dispatch boundary.** Every adapter failure — either
call failing, a classification that cannot be obtained, a multiplier that cannot be
established — is a refusal before the SDK write, converted by `_not_sent(operation)`
and reported as *not sent*. No adapter path can raise past that boundary, so a
missing valuation fact can never surface as an unknown outcome.

The rules are listed below. They are also specified in `trading-policy`.

- **Monetary multiplier.** The multiplier converts a quoted price into money for one
  unit of quantity: `money = reference price × multiplier`. Equities quote in money
  per share, so `STOCK` and `ETF` use `1`. Options quote per share of the underlying
  while trading in contracts, so `DRVT` uses the broker-reported contract field.
  - The classification comes from `get_stock_basicinfo`'s `stock_type`, not from the
    snapshot.
  - **Field selected from current official documentation, 2026-09-23:** use
    `option_contract_multiplier` for premium valuation; retain
    `option_contract_size` separately for deliverable-size compatibility checks.
    The [OpenD snapshot field table and protocol](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)
    distinguish shares per contract from the contract multiplier and do not label
    the latter index-options-only. Moomoo's [premium valuation formula](https://www.moomoo.com/ca/support/topic10_143)
    uses option price multiplied by contract multiplier and contract quantity.
    Together these establish the field choice; the live matched-position arithmetic
    supplies the independent numeric cross-check for the sampled contracts.
  - The pinned SDK docstring's index-only annotation disagrees with the current
    documentation and observed non-index option values. It is not sufficient
    evidence to substitute deliverable size for the monetary multiplier. A live
    adjusted-contract example is not required merely to choose the documented field.
  - This records the design decision, not a deployed code change. The adapter still
    has `OPTION_MONETARY_MULTIPLIER_FIELD = None`; activation and focused regression
    checks remain to be applied. Missing, non-finite or non-positive multiplier
    values must still refuse assessment, without falling back to size or hardcoded
    100. Capped options remain refused in the current deployed image.
  - Any classification other than `STOCK`, `ETF` or `DRVT` is refused.
- **Order classes.** Two frozensets live in `services/validation.py` and are shared
  with Decision 4:
  - `FIXED_LIMIT_TYPES`: `NORMAL`, `ABSOLUTE_LIMIT`, `SPECIAL_LIMIT`,
    `SPECIAL_LIMIT_ALL`, `AUCTION_LIMIT`, `STOP_LIMIT` and `LIMIT_IF_TOUCHED`.
  - `NO_FIXED_LIMIT_TYPES`: `MARKET`, `AUCTION`, `STOP`, `MARKET_IF_TOUCHED`,
    `TRAILING_STOP` and `TRAILING_STOP_LIMIT`.

  These cover every order type this server submits. The SDK's `OrderType` also
  defines `TWAP`, `TWAP_LIMIT`, `VWAP` and `VWAP_LIMIT`; official Moomoo
  documentation marks those algorithmic variants as **query-only**, so they are not
  submission types, are deliberately unclassified, and this change neither adds
  algorithmic submission support nor removes a submission capability.

  While a cap is configured, a type in neither set is refused. A future submission
  order type is therefore refused until someone classifies it.
- **Market reference `M`.** The largest of the normalized `last_price`, `bid_price`
  and `ask_price`. Normalization (above) has already reduced absent, non-numeric,
  non-finite and non-positive values to `None`, so `M` is the maximum of whatever
  numbers remain and is `None` when none remain. No staleness threshold applies.
- **Reference price for a single-leg order.**

  | Side | Class | Reference | `M` required |
  | --- | --- | --- | --- |
  | BUY | fixed-limit | `price` | no |
  | SELL | fixed-limit | `max(price, M)` | yes |
  | any | no-fixed-limit | `max(M, aux_price if > 0, price if > 0)` | yes |

  Why each row takes that value:
  - **BUY fixed-limit.** The limit is an upper bound on the fill price, so it is
    exact and needs no market data. A BUY limit below the market is judged on what
    it can actually cost.
  - **SELL fixed-limit.** The fill is at the limit or better, so the limit is a
    lower bound, and the market reference supplies the upper side.
  - **No-fixed-limit.** There is no bound, so the estimate takes the market and any
    trigger or price the caller supplied. Adding a candidate can only raise the
    estimate. The rule never lowers it below `M`.
- **Combo.** `|price| × qty × monetary multiplier`, using the same verified monetary
  multiplier as a single-leg order. Deliverable contract size and monetary multiplier
  are distinct broker fields; the premium uses the multiplier, and equal contract
  sizes remain a separate compatibility restriction. All legs must be `DRVT` with
  equal monetary multipliers and equal contract sizes, and the order type must be in
  `FIXED_LIMIT_TYPES`. A combo without
  a fixed limit is refused, because no net package reference price is available.
- **Modification.** The same table applies to the merged order. The side, order type
  and trigger price come from the existing order, and a fresh snapshot is taken.
- **Quantity cap.** It applies to `qty × max(qty_ratio)` and needs no instrument
  data.

- *Alternative:* inject `MarketDataService` into `TradeService`. It was rejected
  because it couples two services for what is a narrow, order-shaped read. The
  adapter keeps tests trivial and keeps the policy pure.
- *Alternative:* fetch instrument data on every order, for validation. It was
  rejected because it adds latency and a quote dependency when no cap is configured.
- *Alternative:* infer the classification from snapshot fields that do exist, such as
  `option_valid` or the presence of `option_contract_size`. It was rejected because
  `stock_type` is an authoritative field and inference is not; a guardrail should not
  price an instrument by guessing what kind of instrument it is.

### 4. Numeric validation of order values

Order values are validated before the account lookup:

- A quantity must be a positive `int`; `bool` is refused.
- Floats must satisfy `math.isfinite`.
- Single-leg prices, `aux_price` and trail values must be `>= 0`.
- `price > 0` is required for `FIXED_LIMIT_TYPES` (Decision 3).
  `TRAILING_STOP_LIMIT` is deliberately excluded, because its limit follows the
  trail and a zero `price` is valid.
- A combo price must be finite, with its sign untouched.

This is the existing `stop_order_types` and `trailing_order_types` check, extended
and moved into a single `validate_order_values` in `services/validation.py`.

### 5. Modifications assess the resulting order

For `NORMAL` and `ENABLE`, `modify_order` calls `order_list_query(order_id=…,
trd_env, acc_id, refresh_cache=True)`. It then:

1. merges the requested quantity and price over the existing values;
2. takes the code, order type, trigger price and legs from the order;
3. runs the same validation and assessment as a placement.

An order that is not found, or has unreadable fields, is refused as not sent.

- `CANCEL`, `DISABLE` and `DELETE` skip the assessment.
- `cancel_order` is unchanged, apart from routing and the dispatch boundary.
- *Alternative:* require callers to supply both fields. It was rejected because it
  shifts the check onto the agent, and the value that matters is the broker's
  current order, not the agent's memory of it.
- *Risk:* a target order may no longer be retrievable when a modification is
  assessed, so the modification is refused. The refusal is fail-closed.
  Task 1.2 measures how long a terminal `DAY` order stays queryable in paper, by
  order and history-order query. That measures the paper interface's retention
  behaviour; it does **not** establish REAL GTC-order visibility, which this change
  does not test. Under either, a target that cannot be retrieved remains a refusal.
  On 2026-09-23 the bounded paper order remained visible in both queries immediately
  and later in the same session, with terminal status, zero fills, and its original
  remark. After-close visibility remains unmeasured; these observations are lower
  bounds, not a retention guarantee.

### 6. Account routing

- **Resolution.** `_resolve_account(trd_env, market | None, acc_id)` replaces
  `_find_best_account`.
- **Explicit accounts.** An explicit REAL `acc_id` is checked against the allowlist
  without any gateway call.
- **`acc_id == 0`.** Eligible accounts are those from `get_acc_list` whose
  environment matches. When a market is known (placement or preview), they must also
  be authorized for it. In REAL, they must also be allowlisted.
  - Exactly one eligible account: it is used.
  - Zero, or more than one: the request is refused. Candidates are listed by their
    last four digits only.
- **Modify and cancel.** These have no market, so `acc_id == 0` resolves only when
  the environment has one eligible account.
- **Results.** Every write result gains `acc_id` and `trd_env`. `acc_id` is
  serialized as a string by the existing `serialize_identifiers`.
- **Required `trd_env`.** Tools and service write methods lose the `trd_env`
  default. In the service, `trd_env` becomes keyword-only (`*, trd_env: str`) to
  avoid reordering positional parameters.
- **Read tools.** Read tools keep their REAL default, which is the user's stated
  preference. A wrong-environment read cannot move money.
- **Preview.** `preview_combo_order` uses the same resolver, so it previews the
  account the placement would reach.

### 7. One unlock lifecycle, with a dispatch helper

Replace the `_jit_trade_unlock` context manager with `_dispatch_write(trd_env,
operation, call) -> (records, relock_error)`. It runs these steps:

1. Take `_jit_lock`.
2. If REAL and a credential is configured, unlock. A failure raises
   `OrderNotSentError`.
3. Call the SDK write. This is the dispatch boundary.
4. If there is a credential, relock in `finally`. On failure, record the error and
   set the halt.
5. Convert the response.

The dispatch boundary is where the SDK write call starts. What happens after it
decides the outcome:

| What happened after the boundary | Outcome | Raised or returned |
| --- | --- | --- |
| `ret == RET_OK` and the response converts | acknowledged | the receipt |
| `ret == RET_OK` and conversion raises | acknowledged, receipt unreadable | `OrderReceiptUnreadableError` |
| `ret != RET_OK` | outcome unknown | `OrderOutcomeUnknownError` |
| the SDK call raises | outcome unknown | `OrderOutcomeUnknownError` |

A non-OK return counts as outcome unknown, because matching the gateway's error text
would be neither reliable nor regex-free. Only `RET_OK` counts as an
acknowledgement. No message says the request "reached" the gateway or the broker.
Unknown-outcome messages say the request "may have been sent".

- **Receipt with a relock failure.** On an acknowledged write whose relock fails,
  the write method returns the receipt plus `gateway_relock_error` and
  `execution_halted: true`.
- **Error with a relock failure.** When the write raised, the relock failure is
  appended to the error's message.
- **Lock at rest.** `_enforce_gateway_lock` runs when the mode is `READ_ONLY`, or
  when the mode is `REAL` and a credential is configured.
  - On the reconnect path, it uses `_jit_lock.acquire(blocking=False)`. If a write
    holds the lock, it skips the lock request, and the write's relock covers it.
    The SDK has already replayed the cached unlock that the in-flight write needs.
- **Startup.** `_auto_unlock_trade` and its call in `_build_services` are deleted.
- **Live locked-read verification (2026-09-23).** With explicit operator
  authorization, gateway locking succeeded and accounts, assets, positions,
  current orders, deals, maximum tradable quantity and combo preview all succeeded
  against REAL while locked. No read-only JIT unlock was needed on this gateway.
  The deployed VPS Stage 1 service also acknowledged an explicit lock-only request.
- **`unlock_trade`.** The public method refuses when a credential is configured,
  with the "writes unlock just in time" explanation. The JIT path uses a private
  `_unlock_gateway`. `tools/account.py` drops its environment fallback and its
  `"none"`/`"null"` string handling. It requires an explicit password or hash.

- *Alternative:* keep startup unlock and remove JIT. It was rejected because the
  gateway would then stay unlocked for the life of the process. That is exactly the
  exposure the lock guards against.
- *Alternative:* keep manual unlock alongside a credential. It was rejected because
  one call would silently undo lock-at-rest until the next write.

### 8. Execution state: `ARMED` / `HALTED`

`_ExecutionState` in `TradeService` is guarded by its own `threading.Lock`. It holds
`halted_since` (an ISO time, or `None`) and `last_lock_error`. It exists only in
REAL mode with a credential. In any other configuration it stays `ARMED`, because
nothing can halt it.

| From | Event | To | Recorded |
| --- | --- | --- | --- |
| `ARMED` | JIT relock fails | `HALTED` | `halted_since = now`, `last_lock_error` |
| `HALTED` | JIT relock fails (after a permitted cancellation) | `HALTED` | `last_lock_error` only |
| `HALTED` | `lock_trade` fails | `HALTED` | `last_lock_error` only |
| `HALTED` | `lock_trade` succeeds | `ARMED` | both cleared |
| `ARMED` | `lock_trade` succeeds or fails | `ARMED` | nothing |

These events are not transitions:

- a successful JIT relock;
- a lock at rest on connect or reconnect, whether it succeeds or fails;
- `check_health`.

- **Lock-only recovery.** `lock_trade` is the only transition to `ARMED`, and it is
  the only lock request that is not paired with an unlock the server made itself.
  - A JIT relock that succeeds after a cancellation only undoes that cancellation's
    own unlock. It says nothing about why the earlier relock failed.
  - A reconnect lock happens without anyone seeing the halt.
  - Requiring an explicit call makes recovery a visible, logged action by an
    operator or agent.
- **Serialization.** The public `lock_trade` takes `_jit_lock` in blocking mode. It
  therefore never locks the gateway inside another write's unlock window, and it
  cannot report a clear while a cancellation's relock is still pending. It returns
  `{status: "locked", execution_halted, halt_cleared}`.
- **Gate.** The pre-dispatch sequence reads the state for REAL `place_order`,
  `place_combo_order`, and `modify_order` `NORMAL`/`ENABLE`, before it resolves
  the account. `HALTED` raises a `TradingPolicyError` naming the halt and
  `lock_trade`. `_not_sent` wraps it as `OrderNotSentError`. The authoritative
  read is the one inside `_jit_lock`; this early one exists so a halted write
  pays for no gateway request at all, the account-list read included.
- **Report.** `HealthCheck.result()` adds `execution_halted`, `halted_since` and
  `halt_error` (= `last_lock_error`). It reads memory only, with no probe.

In-memory is sufficient here. A new process starts `ARMED` and locks at rest on
connect. This is not an operator pause. A persistent pause is a separate, later
capability, and nothing here anticipates its storage.

### 9. Error types at the dispatch boundary

Add `services/order_errors.py` with three exception types, one for each outcome
that is not a clean receipt:

- **`OrderNotSentError(RuntimeError)`** covers every refusal before the boundary,
  whatever its cause: policy, validation, limits, account, halt, not connected, or
  unlock failure. Its message states that no order was sent. `__cause__` keeps the
  original `TradingPolicyError` or `ValueError`.
- **`OrderOutcomeUnknownError(RuntimeError)`** is raised when the call started and
  no acknowledgement was received. Its message says the request may have been sent,
  that the outcome is unknown, and that `get_orders` must be checked before any
  retry. It includes the gateway message.
- **`OrderReceiptUnreadableError(RuntimeError)`** is raised when the gateway
  acknowledged the request but its response could not be read. Its message says the
  gateway acknowledged the request, that the order identifier is unknown, and that
  the request must not be resent. It points to `get_orders`.

Write methods wrap their pre-dispatch phase once, with a
`_not_sent(operation)` context manager, so individual checks keep raising their
natural types.

- *Alternative:* append "no order was sent" to each existing message. It was
  rejected because it is easy to miss one path, and callers cannot branch on it.
- *Alternative:* one post-boundary error type. It was rejected because an
  acknowledged request and an unknown outcome call for different operator actions:
  the first means find the order, the second means establish whether one exists.
- *Cost:* tests that expect `TradingPolicyError` from write methods change to expect
  `OrderNotSentError`, with a cause of `TradingPolicyError`. `check_write` itself
  and the unlock refusal still raise `TradingPolicyError`.

### 10. HTTP authentication at startup

`main()` refuses `sse` and `streamable-http` when `auth_token` is blank, unless both
of these hold:

- `MCP_ALLOW_UNAUTHENTICATED_HTTP == "1"`;
- `settings.policy.mode is READ_ONLY`.

The opt-out logs a warning. The HTTP path always uses the `create_*_app` +
`uvicorn.run` route. The no-token branch runs `mcp.run(transport=...)` only under
the opt-out. `stdio` is unaffected.

## Risks / Trade-offs

- **[Risk]** `lock_trade` cannot succeed without a resolvable REAL account. The SDK's
  `unlock_trade` calls `_check_acc_id(TrdEnv.REAL, 0)` on both the unlock and the lock
  path, so a deployment whose gateway exposes no REAL account gets a failing lock.
  → **Mitigation:** in REAL mode a REAL account exists by definition, so this is
  bounded. It matters because `lock_trade` is the only route out of `HALTED`
  (Decision 8): an operator facing a halt on a gateway that cannot resolve a REAL
  account has no recovery. Task 1.3 records the lock outcome alongside the read
  checks, and the `trade-unlock` spec states that a failed `lock_trade` leaves the
  halt in place rather than appearing to clear it.
- **[Risk]** Some REAL reads (`accinfo_query`, `acctradinginfo_query`,
  `comboorder_tradinginfo_query`) may require an unlocked gateway once startup
  unlock is gone.
  → **Mitigation:** task 1.3 checks each read against the live gateway while locked
  before the removal ships. The existing `unlock_trade` docstring already says "many
  gateways serve these without any unlock". If one does fail, that read also goes
  through `_dispatch_write`-style JIT unlock. That is a scoped addition, not a
  change of approach.
- **[Risk]** For SELL fixed-limit and no-fixed-limit orders, the reference price
  depends on a snapshot that may be delayed, depending on quote permissions. A stale
  `M` can understate the value when the market has risen.
  → **Mitigation:** BUY fixed-limit orders, the common case, do not use `M` at all.
  For the other rows, `M` is only one candidate in a maximum, alongside any price or
  trigger the caller supplied. A staleness threshold can be added later without
  changing the table. The limitation is stated in the spec.
- **[Trade-off]** A SELL fixed-limit order, or an order without a fixed limit, on an
  instrument whose quote fields all normalize to `None` is refused while a cap is
  configured. An illiquid option quoting `'N/A'` for bid and ask is the common case.
  → **Mitigation:** this is intended fail-closed behaviour. The error names the
  missing market reference, and the operator can place the order with no cap
  configured.
- **[Trade-off]** The verified-market currency table covers fewer markets than the
  server can trade, so a capped deployment refuses orders on unlisted markets.
  → **Accepted.** The snapshot carries no currency field, and a market prefix is not
  an instrument's currency — HK dual-counter instruments quote in HKD or RMB on one
  venue. Valuing by prefix outside a verified subset would misprice exactly those
  instruments while appearing to work. Refusing is the fail-closed direction, and a
  market is added to the table by verification, not by inference.
- **[Trade-off]** A combo cap on premium is not a cap on maximum loss.
  → **Mitigation:** it is named "package premium" in the errors, tool docs and spec.
  Max-loss is a later change.
- **[Risk]** An extra gateway read per modification (`order_list_query`) adds
  latency and one more failure mode.
  → **Mitigation:** acceptable. A failure refuses the modification as not sent.
- **[Risk]** Breaking configuration makes the live container crash-loop after an
  upgrade if `.env` is not migrated first.
  → **Mitigation:** follow the migration plan order, and treat the log line naming
  the variable as the signal.
- **[Trade-off]** Required `trd_env` adds friction for the agent.
  → **Mitigation:** it is intended. The tool call now records the environment
  explicitly.

## Migration Plan

**Deployment evidence, 2026-09-23.** The operator authorized direct VPS deployment.
Commit `e8b2c52f2d92cb3b9d27308a04942690ee1cd621` replaced `c4b42c7`; the production
image digest is `sha256:867dc574d2e657dd7f551f4c68e9193a244faab482f656e2b00440e9ed1a55fd`.
The existing READ_ONLY configuration (quantity cap 500, no notional cap) passed
the target image's settings validation without secret-file changes. The named
OpenD volume survived recreation. Authenticated initialization, rejection of
unauthenticated requests, broker login and both health probes passed. Explicit
`lock_trade` returned `locked`, with `execution_halted: false`. No unlock or REAL
order was issued. This verifies the read-only deployment, not REAL execution or
Stage 1.1 market selection. See `verification.md` for the command/result record.

1. Before deploying, edit the VPS `.env`:
   - Set `MOOMOO_REAL_ACC_IDS=<your REAL acc_id>`. Get it from `get_accounts`.
   - Add `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:<amount>`, plus any other traded
     currencies. Leave the existing `MOOMOO_MAX_ORDER_NOTIONAL` in place for the old
     image.
   - Confirm `MCP_AUTH_TOKEN` is set. The runbook already requires it.
2. Add `MOOMOO_REAL_ACC_IDS`, `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` and
   `MCP_ALLOW_UNAUTHENTICATED_HTTP` to the `docker-compose.yml` environment
   passthrough. They default to empty. Keep `MOOMOO_MAX_ORDER_NOTIONAL` passed
   through.
3. Deploy through the normal path. `deploy_verify.py` proves that authenticated
   `initialize` works.
4. Call `check_health` and confirm `execution_halted: false`.
5. Place and cancel a SIMULATE order.
6. Optionally, place and cancel a minimal REAL limit order far from the market. This
   step is operator-run.
7. **Rollback:** redeploy the previous image tag with no `.env` edit. The old image
   ignores `MOOMOO_REAL_ACC_IDS` and `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`, and
   enforces its unit-less `MOOMOO_MAX_ORDER_NOTIONAL` as before. The old image also
   restores startup auto-unlock. That is expected behaviour for that version.
8. Update the ZeroClaw agent prompt or skills for two changes: write tools now
   require `trd_env`, and `modify_order`/`cancel_order` now resolve the account
   before dispatch, so a call that relied on the gateway's own default for
   `acc_id="0"` must name an account when more than one is eligible. Teach it that
   `lock_trade` is how an operator clears a halt, and that a refused lock leaves the
   halt in place.
9. If a notional cap is configured, confirm the traded markets appear in the verified
   currency table and that any option instruments have a verified monetary
   multiplier. Instruments outside those sets are refused while a cap is set; this is
   intended, and is the migration's most likely surprise.
10. After Stage 1 has been stable for a while, remove `MOOMOO_MAX_ORDER_NOTIONAL`
   from `.env`, which ends rollback compatibility for that setting.
