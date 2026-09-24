# Proposal

## Why

Before REAL trading can be relied on, the safeguards the server already has must
hold without gaps. A source review of `main` at `f0ae2ef` found several places where
they don't:

- The notional guardrail is skipped when the price is missing or zero. It ignores
  contract multipliers and currency.
- A price-only or quantity-only `modify_order` is checked against a quantity of zero.
- A limit of `nan` or `inf` switches a limit off.
- Account `"0"` in REAL silently picks the first matching account.
- Trading is unlocked by two separate paths: startup and just-in-time.
- A failed relock is logged and then ignored.
- The HTTP transport runs without authentication when no token is set.

This is Stage 1 of the review: repair what exists before building the execution
journal and the approval workflow on top of it.

## What Changes

- **Fail-closed order limits.**
  - Limit configuration SHALL be finite and positive. Every order value is
    validated too: quantities must be positive integers and prices finite.
  - When a notional cap is configured, an order's value is computed per instrument:
    reference price × quantity × contract multiplier, in the instrument's currency.
    A value that cannot be established is refused, not waved through.
  - The reference price follows one fixed rule, by side and order class.
    - A BUY with a fixed limit uses its limit price.
    - Every other order uses the larger of its own prices and the snapshot's market
      reference, and is refused when no market reference is available.
  - The quantity cap applies to the largest leg quantity of a combo, not the
    package count.
- **Currency-qualified caps in a new variable.**
  - `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` holds the caps, for example
    `USD:25000,HKD:200000`.
  - Orders in a currency without a cap are refused while any notional cap is
    configured.
  - The legacy `MOOMOO_MAX_ORDER_NOTIONAL` is ignored when the new variable is set.
    One `.env` can therefore serve the new image and, after a rollback, the old one.
  - **BREAKING**: the legacy variable set on its own is a startup error, because a
    cap without a unit cannot be applied.
- **Modifications are checked as the order that would result.**
  - A `NORMAL` modification fetches the existing order, merges in the requested
    changes, and checks the result.
  - `ENABLE` is checked the same way, because it re-activates exposure.
  - An order that cannot be found is refused.
- **Explicit account routing.**
  - **BREAKING**: `place_order`, `place_combo_order`, `modify_order` and
    `cancel_order` require `trd_env`; it no longer has a default.
  - **BREAKING**: `modify_order` and `cancel_order` resolve the account before
    dispatch. They currently pass `acc_id="0"` straight to the SDK and let it pick
    its own default; from now on `"0"` resolves only when exactly one account is
    eligible, and an ambiguous or unlisted account is refused as not sent.
  - **BREAKING**: REAL mode requires `MOOMOO_REAL_ACC_IDS`. REAL writes may only
    target those accounts. `acc_id="0"` resolves only when exactly one allowed
    account fits; otherwise the request is refused.
  - Every write result names the `acc_id` and `trd_env` it used.
  - **BREAKING**: an unrecognized `MOOMOO_SECURITY_FIRM` is a startup error. It is
    no longer silently ignored.
- **One unlock lifecycle.**
  - **BREAKING**: startup auto-unlock is removed.
  - In REAL mode with a stored trade credential, the gateway is locked at rest: on
    connect and on every reconnect. It is unlocked just in time for a single write,
    then relocked.
  - Manual `unlock_trade` is refused when a stored credential exists. It remains
    for deployments that have none.
- **Relock failure halts, and never loses a receipt.**
  - The service holds an explicit `ARMED` / `HALTED` execution state.
  - A failed relock after a write moves it to `HALTED`. The write's own outcome is
    still returned: an acknowledged write keeps its receipt, with the lock failure
    reported alongside it.
  - While `HALTED`, REAL placements, combo placements and `NORMAL`/`ENABLE`
    modifications are refused. Cancellations stay allowed.
  - The only way back to `ARMED` is a successful `lock_trade`. It is a lock-only
    request, serialized with writes. A cancellation's successful relock or a
    reconnect lock does not clear the halt.
  - `check_health` reports the state.
- **Dispatch boundary in errors.** Every order mutation reports exactly one
  outcome.
  - **Not sent**: refused before the gateway call started.
  - **Outcome unknown (possibly sent)**: the call started and no acknowledgement
    came back. This includes gateway error codes, which cannot be told apart from
    timeouts, and the caller must check `get_orders` before any retry.
  - **Acknowledged**: the gateway returned success. If the receipt can't be read,
    the error says so and forbids a resend.
  - The server never claims a request reached the gateway or the broker unless it
    was acknowledged.
- **BREAKING**: the HTTP transports (`streamable-http`, `sse`) refuse to start
  without `MCP_AUTH_TOKEN`. The only exception is an explicit
  `MCP_ALLOW_UNAUTHENTICATED_HTTP=1` in READ_ONLY mode.

### Non-goals (later changes)

- Stage 2: the persistent execution journal, operation IDs, duplicate suppression
  and reconciliation.
- Stage 3: prepared orders bound to a trusted Telegram approval.
- Stage 4: a persistent operator pause, readiness reporting, a notification outbox,
  subscription reuse, and module and CI restructuring.
- Maximum-loss risk for short options and combos. The combo cap in this change
  measures premium and is documented as premium, not as maximum loss.
- Aggregate exposure across working orders and positions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `trading-policy`: finite limit configuration, the currency-qualified cap variable
  and legacy-variable handling, instrument-aware notional assessment with a fixed
  reference-price rule that fails closed, order-value validation, and the REAL
  account allowlist.
- `trade-unlock`: removes startup auto-unlock and the manual-unlock environment
  fallback, and locks at rest in REAL mode with a stored credential. It adds the
  `ARMED`/`HALTED` execution state, with `lock_trade` as its only recovery. It also
  makes `lock_trade` serialize with writes and report the halt.
- `order-placement`: requires `trd_env`, adds explicit account resolution and the
  resolved account in results. It reports not sent, outcome unknown, or
  acknowledged.
- `order-modification`: checks the resulting order for `NORMAL` and `ENABLE`, and
  requires `trd_env`.
- `combo-order-placement`: account selection under the allowlist and ambiguity
  rule, and leg-quantity and premium limits.
- `configuration`: `MCP_AUTH_TOKEN` is required for HTTP transports; adds new and
  changed variables; validates `MOOMOO_SECURITY_FIRM`.
- `transport-sessions`: an HTTP endpoint is never unauthenticated outside the
  explicit READ_ONLY opt-out.
- `system-health`: reports the execution state.

## Impact

- **Code**:
  - `services/trading_policy.py`: configuration parsing, limit assessment, the
    and the allowlist.
  - `services/trade_service.py`:
    - unlock lifecycle, lock at rest, relock handling, and the execution state;
    - order lookup for modifications;
    - instrument reference lookup;
    - account resolution;
    - dispatch-boundary errors.
  - `server.py`: removes `_auto_unlock_trade` and adds startup auth enforcement.
  - `tools/trading.py`: required `trd_env` and updated docstrings.
  - `tools/account.py`: `unlock_trade` refusal and docstring.
  - `services/base_service.py`: health fields.
  - `TradeService` needs read access to the quote context through an instrument
    adapter. The adapter combines the instrument snapshot (prices, contract fields)
    with the broker's instrument reference data requested by explicit code list (the
    security classification), normalizes non-numeric quote fields, and refuses before
    dispatch when any required valuation fact is missing. The snapshot alone does not
    carry a security classification or a currency.
- **Tests**:
  - `tests/test_services/test_trading_policy.py`
  - `tests/test_services/test_trade_service.py`
  - `tests/test_server.py`
  - `tests/test_tools/test_trading.py`
  - `tests/test_tools/test_account.py`
- **Configuration and operations**:
  - `.env.example`, `docker-compose.yml` (new variables), `docs/deploy-vps.md`,
    `docs/state-and-restarts.md`, `README.md`, and the `openspec/config.yaml`
    context (auto-unlock and "HTTP currently runs unauthenticated").
  - The live VPS `.env` must gain `MOOMOO_REAL_ACC_IDS`, and
    `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` if a cap is used, before upgrading.
    Without them the new image refuses to start.
  - The legacy `MOOMOO_MAX_ORDER_NOTIONAL` can stay in place. The old image reads
    it, and the new image ignores it, so a rollback needs no `.env` edit.
- **Agent (ZeroClaw)**: write tool calls must pass `trd_env`. Tool descriptions
  change.
- **Dependencies**: none new.
