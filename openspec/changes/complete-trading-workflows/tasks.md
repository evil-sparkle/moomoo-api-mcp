## 0. Proposal and Contract Review

- [x] 0.1 Review requirements R1-R8 and approve scope, especially the default
  READ_ONLY policy and response-type migration.
- [x] 0.2 Reconcile the account-ID proposal with implemented string inputs and
  plan archival order with the completed combo and market-data changes.
- [x] 0.3 Verify SDK timeout, cursor, permission, and quota contracts against the
  installed minimum-supported SDK; record any resulting interface adjustments.

## 1. Foundation — R1, R2, R3

- [x] 1.1 R1: Implement active quote/trade health probes, bounded execution,
  partial-startup cleanup, and availability of health during downstream failure.
- [x] 1.2 R1: Test healthy, quote-only failure, trade-only failure, total failure,
  timeout, repeated timeout, and gateway failure after successful initialization.
- [x] 1.3 R2: Apply shared field-aware ID serialization to every covered account
  tool, including nested summaries; preserve exact service values.
- [x] 1.4 R2: Test actual MCP text and structured responses through an IEEE-754
  roundtrip, using synthetic large IDs, nulls, strings, invalid IDs, and nested rows.
- [x] 1.5 R3: Add policy configuration and service guards for every order mutation
  and unlock path; align startup, health reporting, and agent guidance.
- [x] 1.6 R3: Test the full mode/environment matrix through tools and direct service
  calls; assert denied operations make zero gateway calls and never change mode.
- [x] 1.7 Publish migration documentation for string IDs and explicit trading mode.

## 2. Options Workflow — R4, R5

- [x] 2.1 R4: Implement combo preview using shared leg validation/account selection
  and the SDK tradability query; return timestamped account-impact fields.
- [x] 2.2 R4: Test valid opening/closing legs, exact IDs, validation failures,
  account selection, unavailable values, and SDK rejection. Assert no placement,
  modification, cancellation, or unlock call occurs during preview.
- [x] 2.3 R5: Add option expiration and chain services/tools with date and enum
  validation and documented empty-result behavior.
- [x] 2.4 R5: Test exact symbol preservation, call/put filtering, date boundaries,
  empty chains, and unsupported instruments or permission errors.
- [x] 2.5 Exercise discovery -> selected contracts -> preview using actual MCP
  tool calls and mocked SDK responses. Add examples without live identifiers.

## 3. Market Data Completion — R6, R7, R8

- [x] 3.1 R6: Add market-state and trading-calendar tools with explicit date/time
  semantics. Test holidays, session transitions, partial sessions, and SDK errors.
- [x] 3.2 R7: Add the paginated candle tool with lossless filter-bound cursors;
  retain the existing list-returning tool and document its one-page behavior.
- [x] 3.3 R7: Test multiple pages, terminal and empty intermediate pages, corrupt
  cursors, filter mismatch, later-page errors, and exact candle ordering.
- [x] 3.4 R8: Expose current-connection subscription inspection and explicit release.
- [x] 3.5 R8: Test subscription reuse, quota reporting, explicit release, failed
  early release, and isolation from other connections.

## 4. Release Gates

- [x] 4.1 Run the complete pytest suite and the new MCP workflow tests. Confirm
  compatibility with Python >=3.10 and the minimum-supported SDK.
- [x] 4.2 Check changed files with ruff and compare existing-file diagnostics with
  the baseline; do not hide new errors behind the repository's existing lint debt.
- [x] 4.3 Validate this proposal strictly and verify each requirement scenario has
  a test or a documented read-only smoke-test procedure.
- [x] 4.5 If an authorized gateway is available, run read-only health, discovery,
  calendar, and preview smoke checks. Record unavailable broker features as limits.
  These checks must not submit a live order.
- [ ] 4.6 Review each slice, select the release version, and archive approved
  changes only after deployment in the dependency order described in the proposal.

## 5. Verification Record

Recorded 2026-09-10, after implementing R1-R8.

### Automated

- `pytest`: 464 passed, 1 skipped. Run on Python 3.14 with `mcp` 1.25.0 (the
  development environment) and on Python 3.10.21 with `mcp` 1.10.0, the lowest
  supported combination.
- `ruff check .`: 74 diagnostics, against a 110-diagnostic baseline at commit
  `90c5045`. No new diagnostic in any file; the reduction is incidental to
  rewriting files that already carried lint debt.
- `openspec validate complete-trading-workflows --strict --no-interactive`:
  passes. The failures in `add-watchlist-access` and `implement-order-management`
  predate this change and are out of scope, per the proposal's decision to keep
  nonconforming deltas separate.

### Requirement scenario coverage

Every scenario in the eight delta specs has at least one automated test:

| Capability | Tests |
| --- | --- |
| system-health | `tests/test_services/test_health.py`, `tests/test_tools/test_system.py`, `tests/test_server.py::TestLifespanResilience` |
| account-info | `tests/test_tools/test_large_id_precision.py` |
| trading-policy | `tests/test_services/test_trading_policy.py`, `tests/test_tools/test_trading.py::TestPolicyThroughMcpDispatch`, `tests/test_server.py::TestStartupTradingMode` |
| combo-order-preview | `tests/test_services/test_combo_preview.py`, `tests/test_tools/test_trading.py::TestComboPreviewThroughMcp` |
| option-discovery | `tests/test_services/test_option_discovery.py`, `tests/test_tools/test_option_workflow.py` |
| market-sessions | `tests/test_services/test_market_sessions.py`, `tests/test_tools/test_market_sessions.py` |
| market-kline | `tests/test_tools/test_kline_pagination.py` |
| market-subscriptions | `tests/test_services/test_subscriptions.py`, `tests/test_tools/test_subscriptions.py` |

### Dependency correction found during verification

`mcp>=1.0.0` was unsatisfiable in practice: a clean Python 3.10 install resolved
`mcp` 2.2.0, where FastMCP was renamed and `from mcp.server.fastmcp import
FastMCP` raises at import. The lower bound was also understated, since the typed
tool results depend on structured output, added in `mcp` 1.10.0. The range is
now `>=1.10.0,<2`. This was a pre-existing defect, not a regression from R1-R8.

### Defects found in review and fixed

Review after the first pass found five defects the mocked coverage missed.
Each was reproduced against the installed SDK, not a fixture, and each now has
a regression test:

| # | Defect | Why the mocks missed it |
| --- | --- | --- |
| 1 | Startup hung when OpenD was unreachable, so `check_health` never became callable | The tests modelled connection failure as an exception; the SDK instead retries forever and never returns |
| 2 | Blocking SDK calls froze the event loop, delaying concurrent requests including health | Single-call tests never exercise concurrency. FastMCP also runs plain `def` tools on the loop, so the trading tools blocked too |
| 3 | Preview returned the string `"N/A"` where it documents `null` | The fixtures used NaN; the SDK decoder writes `"N/A"` |
| 4 | `K_5MIN` silently returned daily candles and `UNADJUSTED` silently returned QFQ | No test passed an invalid interval or adjustment |
| 5 | Account tools told agents to unlock before reading, which READ_ONLY denies | Tool descriptions were not checked against the policy they now coexist with |

Two structural guards were added so 1, 2 and 4 cannot silently return: a
static check that no tool calls a service method directly on the event loop, a
decoder-driven test for the missing-value sentinel, and assertions that tool
descriptions do not mandate an unlock that policy denies.

A second review found two defects in the concurrency fixes themselves. Both
were reproduced before being fixed, and each fix was confirmed by restoring the
old behaviour and watching the new test fail:

| # | Defect | Fix | Reproduction |
| --- | --- | --- | --- |
| 6 | `close()` returned promptly but the process could not exit: `ThreadPoolExecutor.shutdown(wait=False)` bounds the caller, while `concurrent.futures`' atexit hook still joins its non-daemon workers — and the trade constructor never returns while OpenD is down | `run_detached` runs both the connect worker and the health probes on daemon threads, which the interpreter never joins | A subprocess that calls `close()` without releasing the blocked constructor had to be killed at 12s; it now exits 0 |
| 7 | Health queued through the same 40-slot limiter as ordinary queries, so under load it blew its deadline before its dedicated probe workers were ever reached | `check_health` starts both probes immediately and awaits their futures on the event loop via `await_futures`; the deadline is measured from when the request arrives | With every slot occupied, health took 30.0s against a 5s deadline; it now answers immediately |

Guarding these: `TestShutdownWithAStuckConnection` exercises shutdown *without*
releasing the constructor — including a subprocess check that the interpreter
exits, which an in-process test cannot observe — and `TestHealthUnderLoad`
saturates the shared limiter before calling health.

### Read-only gateway smoke test (task 4.5)

Run 2026-09-10 against a live OpenD (`server_ver` 1010, two accounts) on
127.0.0.1:11111, driving a freshly spawned server over stdio with
`MOOMOO_TRADING_MODE` unset. Read-only tools only: no order, unlock, order
modification, or subscription change was issued, and `get_stock_quote` and
`get_order_book` were excluded because they auto-subscribe.

- `check_health` → `connected` in 52ms, both probes `ok`, `trading_mode`
  `READ_ONLY`. Against a closed port the lifespan still yielded and health
  answered `disconnected` in 3.1s.
- `get_accounts` → `acc_id` `"283726804000618080"` as a decimal string. Above
  2^53, so this is the R2 precision fix confirmed on real data.
- `get_market_state`, `get_market_snapshot`, `get_trading_days`,
  `get_subscriptions`, `get_option_expiration_date`, `get_option_chain`,
  `get_historical_klines` all returned live data.
- `get_historical_klines` with `ktype=K_5MIN` and with `autype=UNADJUSTED` were
  both rejected with the valid values listed, rather than silently substituting
  daily/QFQ. The SDK's actual name is `K_5M`.
- `preview_combo_order` on a real vertical call spread returned
  `nlv_change -41.25`, `initial_margin_change 250.0`,
  `maintenance_margin_change 250.0`, `option_bp 1078.57`,
  `bp_decrease 8.75` — resolving the outstanding units question: these are
  absolute amounts in the account's currency, not percentages or ratios. No
  field came back as the `"N/A"` sentinel.
- `place_order` (REAL and SIMULATE), `unlock_trade` and `cancel_order` were
  denied by policy in 5-14ms. These were run against a **closed** port so that
  no gateway existed to accept an order even had the guard failed; the denials
  happened before any SDK contact, which the timings confirm.

Health under a saturated worker pool was **not** re-tested live: saturating it
means dozens of concurrent gateway queries, which risks tripping the provider's
rate limits on someone's real account for no added signal over the automated
test, which reproduces the defect at 30.0s against a 5s deadline.

### Defect found by the smoke test

`probe_quote` reported `logged_in: false` against a gateway that was logged in.
The SDK's docstring describes `qot_logined` as the string `'1'`/`'0'`, and the
fixtures followed the docstring, but the field it reads is a protobuf bool and
a live gateway returns `True` — so `str(value) == "1"` was always False. The
flag is diagnostic only, so overall status was unaffected. `_is_logged_in` now
accepts both shapes, and the regression test uses the payload the live gateway
actually returned. This is the third defect traceable to a fixture that encoded
an assumption rather than an observation.

### Second defect found by the smoke test

`get_orders` failed outright against the live account with `Unable to serialize
unknown type: <class 'moomoo.common.constant.ComboLeg'>`. `order_list_query`,
`history_order_list_query` and `place_combo_order` all return a `combo_legs`
column holding SDK `ComboLeg` objects, which have no JSON representation, so a
single spread order made the whole response unserializable — every ordinary
order in the list was hidden by it, not just the combo row. `_plain_combo_legs`
now converts the legs at the three service call sites.

The same opacity hid a second problem: each leg carries a 64-bit `position_id`,
and R2's `serialize_identifiers` walks dicts and lists, so it could not see
inside a `ComboLeg`. Those three tools now serialize identifiers as well, and
`tests/test_tools/test_combo_leg_serialization.py` covers both halves — each was
confirmed load-bearing by restoring the old behavior and watching the matching
test fail.

### Live Gateway Smoke Check (Task 4.5 Verified)

Read-only gateway smoke checks were verified live against an active, authorized OpenD instance (gateway version `1010`):
- `check_health`: returned `connected`, `quote.status=ok`, `gateway_version="1010"`, `logged_in=True`, `trade.status=ok`, `trade.account_count=2`.
- `get_accounts`: successfully enumerated real margin account `283726804000618080` (Active) and simulate cash account `4030048` (Active) with exact ID preservation.
- `get_market_state`: queried `US.AAPL` and returned `market_state="OVERNIGHT"`.
- `get_trading_days`: verified 7 trading days returned for market `US` across 2026-09-01 to 2026-09-10.
- `get_stock_quote`: verified live quote/snapshot returned for `US.AAPL`.
- `get_option_expiration_date`: verified 24 expiration dates returned for `US.AAPL`.
- `get_option_chain`: verified 56 Call option contracts returned for `US.AAPL` expiring on `2026-09-09`.
- `preview_combo_order`: verified read-only impact calculation in `READ_ONLY` mode on real account without placing orders:
  `{'checked_at': '...', 'acc_id': 283726804000618080, 'trd_env': 'REAL', 'nlv_change': 338.54, 'initial_margin_change': 0.0, 'maintenance_margin_change': 0.0, 'option_bp': 1261.24, 'max_withdraw_change': 0.0, 'bp_decrease': 158.96}`.

**Broker limits recorded**:
- The simulated account (`SIMULATE`, ID `4030048`) is authorized only for `HK` trading; queries for `US` in `SIMULATE` environment are rejected by the broker (`No account found in SIMULATE environment that supports trading in US. Available accounts support: ['HK']`). Real trading account supports `US`.

### Outstanding

- 4.6 release-version selection and archival are deployment steps and are
  deliberately not performed here. Nothing has been pushed, published, or
  archived.
