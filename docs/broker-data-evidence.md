# Broker data evidence and bounded research reads

This implements the broker boundary for WIS-25, WIS-26, WIS-29 and WIS-33–36.
WIS-37 and WIS-38 have explicit defer decisions below. Strategy selection,
payoffs, scoring, simulation and account importing belong to Options Strategy
Lab. WIS-32 owns deployment/schema delivery, independently of these source changes.

## Evidence identity

- Implementation base: `527ccde1551171a649339c11d24de0cdb278d564`.
- Local SDK actually installed and inspected: `moomoo-api 10.10.7008` from the
  unchanged lockfile. No SDK upgrade. Source package version: `0.2.1`.
- Documentation reviewed: **2026-10-26**, official Moomoo documentation **v10.11**.
- Installed methods/signatures/source hashes are reproducible with
  `.venv/bin/python scripts/inspect_broker_data_sdk.py`. This command makes no
  gateway or account request. SDK presence is not OpenD/region/entitlement support.
- Runtime health now reports the installed SDK version. Unestablished source and
  build revisions remain `unknown` / `null`; they are never guessed from the
  lockfile, gateway version, local checkout or current GitHub main.
- `check_health.market_data` distinguishes documentation, installed method,
  implementation decision, observed registration, credential permission and
  served schema. Connector import, gateway support and live verification stay
  `UNKNOWN`. It does not certify a host's imported catalogue or refresh it.

The capability-reporting pattern was reviewed in OKX agent-trade-kit at
[`c8f7d76545482f8688db7385bddf2e77a06f6bf2`](https://github.com/okx/agent-trade-kit/tree/c8f7d76545482f8688db7385bddf2e77a06f6bf2):
[`packages/mcp/src/server.ts`](https://github.com/okx/agent-trade-kit/blob/c8f7d76545482f8688db7385bddf2e77a06f6bf2/packages/mcp/src/server.ts)
separates enabled modules, configuration and authentication;
[`packages/core/src/tools/types.ts`](https://github.com/okx/agent-trade-kit/blob/c8f7d76545482f8688db7385bddf2e77a06f6bf2/packages/core/src/tools/types.ts)
ties annotations to tool metadata. This implementation uses no OKX dependency
or copied code. Configured permissions never establish provider acceptance.

## Public contracts

| Tool | Bound and contract |
| --- | --- |
| `get_market_snapshot_evidence` | 1–100 exact unique codes; requested/returned/missing IDs, preserved bid/ask/sizes, UTC observation times. Legacy `get_market_snapshot` is unchanged. |
| `get_instrument_facts` | 1–100 codes; the existing shared `InstrumentAdapter`, static cache and fresh snapshots. Separate monetary multiplier, deliverable size and lot size. Each fact carries sources, observation times and `KNOWN` / `UNKNOWN` / `CONFLICT`. |
| `get_order_fees(..., include_evidence=true)` | Existing `order_id_list`, REAL account authorization, 1–400 IDs and service. Default false retains the old list. Evidence reports omitted IDs, missing amounts/details and unknown currency; duplicate/unexpected rows fail. |
| `get_option_chain` | Existing call defaults and expiry limit preserved; optional `option_cond_type`, typed `data_filter` and `include_evidence`. Explicit `ALL/WITHIN/OUTSIDE`; no arbitrary kwargs. |
| `get_earnings_calendar` | Explicit market/start/end, at most seven days apart; 1–1000 returned rows. Provider order and before/after/unknown timing preserved. No automatic pagination. |
| `get_corporate_actions_dividends` | One exact code, 1–1000 rows; provider dates, event status and monetary fields remain as supplied. No amount/currency extracted from prose. |
| `get_rehab` | One exact code, 1–1000 rows; raw adjustment factors and bases. Not a complete corporate-action feed. |
| `get_history_kl_quota` | Aggregate only; no used-security detail list, so another client's research universe is not disclosed. Missing quota is unknown; zero is valid. |
| `get_option_volatility` | One exact provider-verified option, WEEK/MONTH/QUARTER/HALF_YEAR/YEAR, HV lookback 5–250 days, 1–1000 rows. IV/HV remain percentage points, not fractions. |

Research responses accept at most 2000 provider rows and 2 MiB of encoded data;
larger responses fail explicitly. A smaller requested output limit sets
`coverage.truncated`; it never silently asserts complete coverage. Snapshot and
chain failures remain errors, not empty success. `coverage.complete_response`
only describes response coverage, not correct/current data or complete history.
`provider_history_complete` is always unknown. Nonfinite raw numeric values are
normalized to null; missing fields are not synthesized. No interpolation or
forward-fill is performed. Duplicate keyed rows fail rather than being summed.

These tools use `market_read`, existing async admission and the shared dispatcher.
The only account-scoped tool here is the existing fee query. A market-only client
cannot see/call account or order tools, and its health request skips private
trade/account/journal probes. No deployed grant or credential was changed.

## Quality and financial interpretation

Observation timestamps are UTC request/receipt times. Provider date/time fields
remain verbatim, with market-local semantics documented by the upstream method.
Snapshot `update_time` is not a timestamp for each bid/ask side. Delay, consolidated
NBBO status, feed entitlement and atomic package liquidity remain unknown unless
separately evidenced. An open session does not prove each leg's quote is fresh.

Contract facts never substitute 100 for a missing/zero monetary multiplier, never
use deliverable size as that multiplier, and never infer currency from a symbol
or account reporting currency. `N/A` settlement is unknown. Complete exercise,
settlement/cutoff and adjusted-deliverable facts remain a consumer compatibility
gate. Cached static observations and fresh snapshot receipt times are distinct.

Chain filters use IV percentage points, unit delta in [-1, 1], signed theta/rho,
nonnegative gamma/vega and integer OI/volume. All bounds are finite, min <= max,
and extra fields are rejected. The provider truncates IV filter precision to
integer percentage points and Greeks to three decimal places. The evidence
scope preserves the caller's submitted bounds, not a claim about provider data
freshness. OI updates daily. Filtering does not replace independent checks of
required-side sizes, quote age/skew, costs and contract compatibility.

Actual fee evidence has no synthesized total. Reported zero differs from null,
omitted orders or unavailable detail. Consumers must establish order/report/date/
currency attribution before reconciliation, avoid double-counting combo parents
and legs, and avoid equal per-fill allocation assumptions. Historical charges are
not pre-trade estimates. **No private AAPL order/fee reconciliation was run.**

Earnings/dividend fiscal/event dates are not historical publication availability.
Revisions and announced-versus-estimated status are preserved only when supplied.
Adjustment factors are not an event archive. Volatility dates, lookback and units
are explicit; annualization basis, expired-contract coverage and point-in-time
revision/availability semantics remain unknown. IV below HV is not automatically
undervaluation or a physical expected-return claim.

## Provider limits and primary sources

All rates below are requests per rolling 30 seconds. Local budgets coordinate
one MCP process using one dispatcher, not independent clients/processes or server
restarts. Provider errors are not automatically replayed. Quota observations are
not reservations; other clients may consume them.

| Operation | Rate / source |
| --- | --- |
| Chain | [10; at most 30 inclusive expiry days](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html) |
| Snapshot | [60; maximum 400 provider codes/request](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html), evidence tool locally limits to 100 |
| Earnings | [60; explicit range no more than seven days apart](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-earnings-calendar.html) |
| Dividends | [30; stocks/funds](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-corporate-actions-dividends.html) |
| Adjustments | [60](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-rehab.html) |
| Option volatility | [30; option contracts only](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-volatility.html) |
| Historical quota | [No published numeric call rate](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-history-kl-quota.html); [current docs describe a seven-day quota window](https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html), deployment-specific applicability unverified |
| Fees | [10 per account; REAL only, CA excluded; provider coverage after 2018-01-01](https://openapi.moomoo.com/moomoo-api-doc/en/trade/order-fee-query.html) |

## Explicit optional capability decisions

All six methods below exist in the locally installed SDK, but none is exposed by
this change. Their runtime gateway/market/region/entitlement support remains
unverified. Existing expiry → chain → snapshot remains usable independently.

| Issue / method | Evidence and defer decision |
| --- | --- |
| WIS-37 `get_option_strategy` | [Leg-combination discovery](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-strategy.html), 30/30s. Not a strategy selection engine. Defer until sanctioned runtime and contract semantics are evidenced. |
| WIS-37 `get_option_strategy_spread` | [Valid strategy widths](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-strategy-spread.html), 30/30s. Not bid/ask spread or optimization. Same defer. |
| WIS-37 `get_option_quote` | [Combination snapshot](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-quote.html), 120/30s. SDK argument is `option_legs`, while current docs name `combo_leg_list`. No verified package venue/size/timestamp/synchronization/firmness evidence. Defer; do not label it either proven synthetic or firm. |
| WIS-37 `get_option_strategy_analysis` | [Broker P/L/PoP/Greeks](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-strategy-analysis.html), 30/30s. Fee assumptions, sign conventions and probability/model basis are not established. SDK argument also differs from current docs. Defer until independently cross-checked; broker PoP is not measured win rate. |
| WIS-38 `get_stock_filter` | [Screening](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-stock-filter.html), 10/30s, up to 200 rows/page. Dynamic sorting can move results between pages; regular-session data only. Defer broad screening until selected field units, market acceptance and filter-bound pagination are justified and tested. |
| WIS-38 `get_financials_statements` | [Statements](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-financials-statements.html), 30/30s, 1–50 rows/page, `next_key` terminal `-1`. Distinguishes fiscal periods, currency and accounting basis but does not establish historical publication/revision availability. Defer until scale/basis mappings and bounded revision-aware pagination are verified. Never substitute current statements for historical TTM or point-in-time fundamentals. |

These are evidenced defer outcomes allowed by WIS-37/WIS-38, not claims that the
upstream methods are absent or universally unsupported. No SDK upgrade or paid
entitlement change is required by the fallback.

## Live evidence and outstanding gates

Bounded existing-connector checks on **2026-10-26**:

- `get_market_state` at `14:34:18Z` reported SPY/QQQ/AAPL and the two sampled
  options `AFTERNOON`.
- `get_option_chain(code="US.SPY", start="2026-10-30", end="2026-10-30",
  option_type="CALL")` returned 249 rows. This is a later independent audit, not
  an automatic retry. The earlier timeout's cause remains unknown; it was not
  evidence of invalid input or entitlement denial.
- `get_market_snapshot` for the three underlyings plus exact previously discovered
  `US.QQQ261030C704000` and `US.AAPL261030C347500` returned five rows with populated
  bid/ask/sizes. Option update times were `2026-10-26 10:30:52` and
  `2026-10-26 10:34:14` respectively. These timezone-free provider strings are
  preserved as such; the older QQQ observation is not asserted fresh.
- Both option snapshots reported multiplier/deliverable 100 and AMERICAN
  exercise. SPY chain settlement was `N/A`; lifecycle compatibility remains open.

These checks exercised the existing deployed connector, **not the new wrappers**.
They establish neither the deployed source/SDK build nor new-tool delivery,
explicit delay rights, native analytics, events, volatility or quota acceptance.
WIS-32's credential-visible endpoint → connector gate and WIS-29's separately
authorized private reconciliation remain open. Deployment, live wrapper acceptance,
independent review, CI and coordinator merge/production-port gates remain separate.

## Reproducible local validation

```sh
.venv/bin/python scripts/inspect_broker_data_sdk.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/basedpyright
.venv/bin/pytest -q
.venv/bin/pytest -q tests/test_delivery_schema.py
git diff --check
```

Use the exact implementation/review SHA recorded in the PR. Offline tests use
broker doubles, not real/paper orders. A source/test pass is not broker acceptance.
