# Tasks

## 1. Verify SDK and gateway facts the design relies on

These tasks are read-only or SIMULATE-only. None places a REAL order. Record each
finding in `design.md` under the decision it affects.

Two harnesses run them, and `verification.md` holds the evidence:
`scripts/verify_sdk_facts.py` (offline, also run by `tests/test_sdk_facts.py`) and
`scripts/verify_gateway_facts.py` (operator-run, one authorization flag per
non-read-only phase, no REAL-order path).

- [ ] 1.1 Establish the instrument facts the assessment needs, for a US stock, a US
  ETF and a US equity option.
  - From `get_market_snapshot`: `last_price`, `bid_price`, `ask_price`, `lot_size`,
    `option_contract_size` and `option_contract_multiplier`. Also record all three
    price fields for an option with no trades today, to capture what the gateway
    returns when a quote is absent.
  - From `get_stock_basicinfo`, called with an explicit `code_list` of those codes:
    `stock_type`. Record whether every requested code is returned.
  - **Monetary multiplier semantics.** Determine which option field, multiplied by
    the quoted price, yields the cash value of one contract. Verify it against an
    independent figure — an independently established premium cash value, or a
    matched position's market value for that same contract — not by observing that a
    field equals 100. Do not use `option_contract_nominal_value` as the US evidence:
    the published field table marks it HK-options-only, and a nominal amount is not
    the premium being assessed. A
    field equalling 100 for standard US contracts does not establish what it means.
    Record the finding and fix Decision 3's multiplier accordingly.
  - **Currency.** Confirm that every instrument valued on `US` quotes in USD, so the
    verified-market table in Decision 2 can list it as confirmed rather than
    provisional. Do not extend the table to a market that has not been checked this
    way.

  Verify by recording each field and its source call, and by stating which of the two
  option fields carries the monetary multiplier and on what evidence.

  **Status: partially established.** The SDK's own field table narrows the
  multiplier to `option_contract_size` and marks `option_contract_multiplier` as
  index-options-only; two further findings (the `'N/A'` sentinel reaching the
  multiplier, and `option_contract_size` being unable to report itself missing) are
  recorded in Decision 3. The instrument reads, the independent cross-check and the
  currency confirmation need a gateway and an account, which this session had
  neither of. Options stay refused while a cap is configured. See
  `verification.md`.
- [ ] 1.2 Measure how long a terminal paper order stays queryable, using a `DAY`
  order.

  Official Moomoo paper-trading documentation states that paper orders are valid for
  the day only, and that deal-related operations are not available in paper trading.
  The earlier form of this task assumed a multi-day GTC paper order, which that
  documentation rules out; it is replaced rather than retried.

  In SIMULATE, place a `DAY` limit order far from the market, cancel it, and then
  record whether `order_list_query(order_id=…)` and the history-order query still
  return it: immediately, later the same session, and after the trading day closes.

  Verify by recording both retention windows. This is what Decision 5's risk actually
  needs — how late a modification can still find its target order — and it is
  answerable on a day-only provider.

  **Status: not run** — needs a gateway and paper-order authorization. Run
  `verify_gateway_facts.py paper-retention`, then `paper-retention-recheck` after
  the close. One offline finding constrains the measurement:
  `history_order_list_query` accepts no `order_id`, so the history side filters by
  code and matches the id client-side.

- [ ] 1.2a *(optional, separately authorized)* If the day-only constraint is to be
  tested rather than assumed, attempt one SIMULATE order with a non-`DAY` time in
  force and record the outcome.

  A `RET_OK` on submission SHALL NOT be recorded as acceptance. Query the resulting
  order and record its **stored `time_in_force`**: a gateway that accepts the request
  and silently stores `DAY` has confirmed the documented constraint, not contradicted
  it. Only a stored non-`DAY` value contradicts the documentation.

  This places an order and is not part of the required set. It requires its own
  authorization, and nothing in this change depends on its outcome.
- [ ] 1.3 With the operator's confirmation, lock the live gateway (`lock_trade`),
  then call these REAL reads:
  - `get_accounts`, `get_assets`, `get_positions`, `get_orders` and `get_deals`;
  - `get_max_tradable` and `preview_combo_order`.

  Record any read that fails because the gateway is locked. Verify by listing the
  per-read outcome. If a read fails, add a task in group 5 to wrap it in the JIT
  unlock before removing startup unlock.

  Also record whether the `lock_trade` request itself succeeds on this gateway. The
  SDK's lock path resolves a REAL account before issuing the lock, so a gateway that
  cannot resolve one produces a failing lock — and `lock_trade` is the only route out
  of `HALTED`. Record the outcome so the halt has a verified recovery path.

  **Status: not run** — needs a REAL gateway, the credential, and authorization to
  change the gateway's lock state. The premise is confirmed offline: `_check_acc_id(
  TrdEnv.REAL, 0)` sits outside the `is_unlock` branch, so locking resolves a REAL
  account too. Run `verify_gateway_facts.py real-reads
  --i-authorize-real-gateway-lock`.

## 2. Settings and policy configuration

- [ ] 2.1 Add `moomoo_mcp/settings.py` with `Settings` and `load_settings(environ)`.
  It covers the OpenD host and port, the policy, the credential (plain text takes
  precedence), a security firm validated against `SecurityFirm`, the transport, the
  auth token and `MCP_ALLOW_UNAUTHENTICATED_HTTP`. It uses no regex. Verify with new
  `tests/test_settings.py` cases for these inputs:
  - each invalid variable, with the error naming it;
  - credential precedence;
  - an unknown firm.
- [ ] 2.2 Change `TradingPolicy`:
  - validate `max_order_qty` and the caps in `__post_init__` (finite, `> 0`);
  - make `max_order_notional` a currency → amount mapping, parsed from
    `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` (`CURRENCY:AMOUNT[,…]`);
  - reject entries without a currency, duplicates, non-alphabetic and
    non-three-letter codes, `nan` and `inf`;
  - handle the legacy `MOOMOO_MAX_ORDER_NOTIONAL` as design Decision 2 describes.
    It is ignored, with an INFO log, alongside the new variable, and is a startup
    error when set alone. It is never parsed as a limit.

  Verify with `test_trading_policy.py` cases for each rejection, including direct
  construction. Add a case for both variables set, which enforces the new cap and
  logs, and one for the legacy variable alone, where the error names the new
  variable.
- [ ] 2.3 Add `real_acc_ids` to the policy, parsed from `MOOMOO_REAL_ACC_IDS`
  (comma-separated decimal identifiers). It is required when the mode is REAL and
  ignored otherwise. Verify with tests for a missing, empty or malformed value in
  REAL, and for an absent value in SIMULATE.

## 3. Order-value validation and limit assessment

- [ ] 3.1 Add `validate_order_values` to `services/validation.py`. It checks:
  - a positive `int` quantity, with `bool` rejected;
  - finite, non-negative single-leg price, `aux_price` and trail values;
  - `price > 0` for `FIXED_LIMIT_TYPES`, defined here together with
    `NO_FIXED_LIMIT_TYPES` (design Decision 3). `TRAILING_STOP_LIMIT` is not a
    fixed-limit type;
  - a finite combo price, with its sign preserved.

  It absorbs the existing stop and trailing required-field checks. Verify with unit
  tests for each rule, including that `TRAILING_STOP_LIMIT` accepts a zero price.
- [ ] 3.2 Add the frozen dataclasses `InstrumentFacts` and `OrderFacts`, and the pure
  method `TradingPolicy.assess_order` that implements design Decision 3:
  - the quantity cap on the largest leg quantity;
  - the market → currency table;
  - the multipliers for STOCK, ETF and DRVT;
  - the market reference `M` = max of the positive `last_price`, `bid_price` and
    `ask_price`;
  - the reference-price table: BUY fixed-limit → `price`; SELL fixed-limit →
    `max(price, M)`; no-fixed-limit → `max(M, aux_price, price)`, where `M` is
    required;
  - refusal of order types in neither class;
  - combo premium using the monetary multiplier, with equal monetary multipliers and
    equal contract sizes, no stock leg and fixed-limit types
    only;
  - fail-closed refusals, with messages naming the value, currency and reason.

  Take instrument facts as already-normalized numbers: `assess_order` performs no
  arithmetic or finiteness test on a value the adapter has not reduced to a number or
  to `None`, and refuses when a required fact is `None`.

  Remove `check_order_limits`. Verify with tests covering every scenario in
  `specs/trading-policy` and `specs/combo-order-placement`, including 5 × 3.00 × 100
  = 1,500 USD, and these cases:
  - a BUY limit at 90 with `M` 150 → 900, permitted;
  - a SELL limit at 90 with `M` 150 → 1,500, refused;
  - a MARKET order at price 0 → `M`;
  - a BUY STOP with `aux_price` 130 and `M` 120 → 1,040;
  - a SELL limit with no `M` → refused;
  - a combo at -2.50 × 3 × 100 = 750;
  - a synthetic instrument whose contract size and monetary multiplier differ,
    asserting that both the single-leg notional and the combo premium use the
    monetary multiplier. This is the case that catches the wrong field being reused;
  - the 1:2:1 butterfly quantity of 6.

## 4. Dispatch boundary, account routing and modification checks in `TradeService`

- [ ] 4.1 Add `services/order_errors.py` with `OrderNotSentError`,
  `OrderOutcomeUnknownError` and `OrderReceiptUnreadableError`. Add a
  `_not_sent(operation)` wrapper that converts every pre-dispatch failure, keeping
  `__cause__`: `TradingPolicyError`, `ValueError`, `TypeError`, not-connected errors,
  and any failure raised by the instrument adapter. Nothing raised before the SDK
  write may escape the boundary unconverted. Verify with unit tests
  on the message text:
  - not sent: "no order was sent";
  - unknown: "may have been sent", "outcome is unknown", "check get_orders before
    retrying";
  - unreadable: "acknowledged", "do not resend".

  Also verify that no message contains "reached", and that the cause is chained.
- [ ] 4.2 Replace `_find_best_account` with `_resolve_account(trd_env, market,
  acc_id)`, following design Decision 6:
  - check the allowlist for explicit REAL identifiers without a gateway call;
  - resolve `acc_id == 0` only when exactly one account is eligible;
  - resolve modify and cancel without a market;
  - mask candidates to their last four digits.

  Verify with tests for a single eligible account, two eligible accounts, none, an
  unlisted explicit account, and SIMULATE being unaffected by the allowlist.
- [ ] 4.3 Add the instrument adapter and accept it as `instrument_lookup` in the
  `TradeService` constructor, building `OrderFacts` only when a notional cap is
  configured. The adapter:
  - calls `get_market_snapshot` for prices and contract fields, and
    `get_stock_basicinfo` with an explicit `code_list` for `stock_type`, using the
    codes the order names;
  - normalizes every quote field to a number or `None`, treating absent, non-numeric
    (including the `'N/A'` sentinel), non-finite and non-positive values as absent;
  - returns `InstrumentFacts` with the classification, the monetary multiplier and
    the normalized prices;
  - refuses when either call fails, when a requested code is not returned, or when
    the classification, multiplier or currency cannot be established.

  Verify that tests assert: neither call is made when no cap is set; a snapshot
  carrying `'N/A'` for bid and ask yields `None` for those fields and no type error;
  a code missing from the classification response refuses the order; and every
  adapter failure surfaces as not sent, never as an unknown outcome.
- [ ] 4.4 Rework `place_order`, `place_combo_order` and `preview_combo_order` onto
  the pre-dispatch sequence:
  - make `trd_env` keyword-only and required on the write methods;
  - have preview use the same resolver;
  - add `acc_id` and `trd_env` to the write results.

  Verify with the updated `test_trade_service.py` placement and combo tests.
- [ ] 4.5 Change `modify_order` so `NORMAL` and `ENABLE` fetch the existing order
  with `order_list_query(order_id=…, refresh_cache=True)`, merge the requested
  changes, and assess the result with the existing order's side and order type. A
  missing order is refused as not sent. `CANCEL`, `DISABLE` and `DELETE` skip the
  assessment. Verify with tests for:
  - price-only on a BUY limit (10 × 500);
  - quantity-only on a BUY limit (100 × 50);
  - ENABLE over the cap;
  - an unknown order.

## 5. Unlock lifecycle and execution state

- [ ] 5.1 Pass the credential into `TradeService` from the settings, and remove the
  `os.environ` reads from the trade service. Verify that JIT tests construct the
  service with a credential instead of patching the environment.
- [ ] 5.2 Replace `_jit_trade_unlock` with `_dispatch_write`, following design
  Decision 7:
  - serialize with `_jit_lock`;
  - on unlock failure, raise `OrderNotSentError`;
  - map the post-boundary outcomes using the Decision 7 table:
    - `ret != RET_OK` or an SDK raise → `OrderOutcomeUnknownError`;
    - `RET_OK` with a conversion failure → `OrderReceiptUnreadableError`;
  - attempt the relock in `finally`, appending a relock failure to any raised
    error.

  Route `place_order`, `place_combo_order`, `modify_order` and `cancel_order` through
  it. Verify with tests for:
  - a gateway error code → unknown;
  - an SDK raise → unknown;
  - `RET_OK` with an unconvertible payload → receipt unreadable;
  - an unlock failure, with no write call made;
  - relock always being attempted.
- [ ] 5.3 Add `_ExecutionState` (`ARMED`/`HALTED`) with exactly the Decision 8
  transition table:
  - JIT relock failure → `HALTED`, keeping the original `halted_since` if already
    halted;
  - successful `lock_trade` → `ARMED`;
  - failed `lock_trade` → unchanged, with `last_lock_error` updated;
  - successful JIT relocks, locks at rest and health checks → no transition.

  Gate REAL placement, combo placement and `NORMAL`/`ENABLE` modify before dispatch,
  and let cancel, `CANCEL`, `DISABLE` and `DELETE` through. Return the receipt with
  `gateway_relock_error` and `execution_halted: true` when an acknowledged write's
  relock fails. Verify with tests covering every Execution Halt scenario in
  `specs/trade-unlock`, including that a halted cancellation's successful relock and
  a reconnect lock both leave the state `HALTED`.
- [ ] 5.4 Make the public `lock_trade` take `_jit_lock` in blocking mode, drive the
  `HALTED` → `ARMED` transition, and return `execution_halted` and `halt_cleared`
  from the service and the `lock_trade` tool. Verify with tests:
  - `lock_trade` waits while a JIT write holds the lock;
  - it clears a halt on success, reporting `halt_cleared: true`;
  - it keeps the halt and the original `halted_since` on failure;
  - it reports `halt_cleared: false` when already `ARMED`.
- [ ] 5.5 Extend `_enforce_gateway_lock` to REAL with a credential. On reconnect,
  skip the lock when `_jit_lock.acquire(blocking=False)` fails. Verify with tests:
  - REAL with a credential locks on connect and on reconnect;
  - a reconnect during a held JIT lock issues no lock;
  - SIMULATE, and REAL without a credential, issue no lock;
  - a successful lock at rest does not change the execution state.
- [ ] 5.6 Make the public `unlock_trade` refuse when a credential is configured, and
  add a private `_unlock_gateway` for JIT. In `tools/account.py`, remove the
  environment fallback and the `"none"`/`"null"` handling, require an explicit
  password or hash, and state the persistence of a manual unlock in the result and
  docstring. Verify with `test_account.py` cases for each Manual Unlock Tool
  scenario.
- [ ] 5.7 Delete `_auto_unlock_trade` and its call in `_build_services`. Remove or
  replace the `TestAutoUnlock` tests and
  `test_real_mode_unready_connection_skips_auto_unlock`. Verify with a test that
  REAL startup with a credential makes no unlock request.

## 6. Server wiring, authentication and health

- [ ] 6.1 Have `main()` call `load_settings()` before choosing a transport, and have
  `_build_services()` use the loaded settings. Wire `instrument_lookup` to the
  shared quote context's snapshot. Verify with a `test_server.py` case showing that
  an invalid variable exits before `mcp.run` or `uvicorn.run` is called.
- [ ] 6.2 Refuse `sse` and `streamable-http` without a token, unless
  `MCP_ALLOW_UNAUTHENTICATED_HTTP=1` is set and the mode is READ_ONLY. The opt-out
  logs a warning. Replace `test_main_logs_streamable_http_endpoint_without_auth`
  with tests for:
  - the refusal;
  - the opt-out in READ_ONLY;
  - the opt-out refused in REAL and SIMULATE;
  - stdio without a token.
- [ ] 6.3 Add `execution_halted`, `halted_since` and `halt_error` to
  `HealthCheck.result()`, without any probe dependency. Verify with `test_health.py`
  and `test_system.py` cases for the `ARMED` and `HALTED` states, with status
  unchanged and the state not cleared by the health call.

## 7. Tool surface

- [ ] 7.1 In `tools/trading.py`, remove the `trd_env` default from `place_order`,
  `place_combo_order`, `modify_order` and `cancel_order`, and rewrite their
  docstrings:
  - remove "Default is REAL";
  - document the account resolution rule;
  - document fail-closed limits, the reference-price rule in brief, and
    premium-not-max-loss for combos;
  - document the halt, and `lock_trade` as its only recovery;
  - document the three outcomes, including never resending an outcome-unknown or
    receipt-unreadable request without first checking `get_orders`.

  Update the `lock_trade` docstring to describe halt recovery and its result fields.
  Verify with `test_trading.py` asserting that the tool schemas list `trd_env` as
  required.

## 8. Configuration, docs and project context

- [ ] 8.1 Update the configuration files:
  - `.env.example`: add `MOOMOO_REAL_ACC_IDS`,
    `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`, `MCP_AUTH_TOKEN` as required for HTTP,
    and `MCP_ALLOW_UNAUTHENTICATED_HTTP`. Mark `MOOMOO_MAX_ORDER_NOTIONAL` as a
    legacy, rollback-only setting.
  - `docker-compose.yml`: pass through the new variables, and keep passing through
    `MOOMOO_MAX_ORDER_NOTIONAL`.

  Verify with `tests/test_compose_topology.py` still passing, and
  `./scripts/smoke-test.sh` in CI.
- [ ] 8.2 Update the documentation:
  - `docs/deploy-vps.md`: the migration steps from design.md, the rollback with no
    `.env` edit, and the crash-loop signal.
  - `docs/state-and-restarts.md`: the unlock lifecycle, the `ARMED`/`HALTED`
    transitions, and `lock_trade` recovery.
  - `README.md`: the configuration table.
  - The `openspec/config.yaml` context: remove startup auto-unlock and
    "HTTP currently runs unauthenticated"; add `settings.py` to the module map.

  Verify by searching the docs for `auto-unlock` and `_auto_unlock_trade`, and
  finding none remaining. Every `MOOMOO_MAX_ORDER_NOTIONAL=` mention must be labelled
  legacy or rollback-only.

## 9. Integration check

- [ ] 9.1 Run the full local gate and verify that each command passes:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run basedpyright`
  - `uv run pytest`
  - `npx -y @fission-ai/openspec@1.13.1 validate --all --strict --no-interactive`
- [ ] 9.2 Against the live gateway in SIMULATE, run through each step and record the
  outcomes in the PR description:
  1. place a limit order with `acc_id="0"`;
  2. modify only its price past the cap and observe a not-sent refusal;
  3. cancel it;
  4. check that `check_health` shows `execution_halted: false`.
- [ ] 9.3 Operator-run, after adding the new variables to the VPS `.env`: deploy,
  confirm authenticated `initialize` through `deploy_verify.py`, and optionally place
  and cancel a minimal far-from-market REAL limit order. Verify with the deploy log
  and the order's final status in `get_history_orders`. This task is not performed
  by the agent.
