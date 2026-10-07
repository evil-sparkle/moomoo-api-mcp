# QQQ position accounting investigation

Investigation date: 2026-10-07. Status: **boundary verified; numerical mechanism reproduced; broker accounting cause unresolved**.

This is a reports-only investigation. The main commit inspected and used as the branch base is `c864762df221d5e4ce5c2e6d04678b54988d0a3e`, fetched from `origin/main` on the investigation date. Account-specific numbers, identifiers, raw responses, transaction histories and screenshots are excluded from this public report. The accompanying [calculation report](2026-10-07-qqq-pnl-calculation-model.md) contains explicitly synthetic numbers. Exact calculations and original capture evidence remain local and private.

## Executive findings

| Finding | Evidence and qualification |
| --- | --- |
| The disputed basis values already exist before the MCP service conversion. | A refreshed REAL-account query captured the decoded OpenD protobuf response and the installed SDK DataFrame. The affected costs and monetary P/L values agreed. This locates the earliest verified boundary; it does not identify the upstream implementation. |
| The inspected MCP path preserves the captured numerical values. | The same SDK response passed through the installed `TradeService`, account tool, identifier serializer and FastMCP structured/text output without changing the affected monetary fields. SDK ratio conversion to percentages is deliberate. |
| The supplied reversal calculation reproduces five API metrics. | Offline Decimal arithmetic using the user's displayed cash amounts reproduces `average_cost`, `unrealized_pl`, `realized_pl`, `diluted_cost` and `pl_val` at the observed precision; `cost_price` also matches the diluted-cost alias. Evaluating the same cash assumptions at the later captured SDK mark also matches all five captured metrics. This is a candidate mechanism, not an inspection of broker code. |
| The app-compatible average is also reproducible. | A hypothetical buy-before-sell weighted average matches the supplied displayed app average. It does not establish the broker's correct posting order or justify reordering the observed events. The app's unrealized P/L still differs by one cent from this illustration under the supplied mark and ordinary cent rounding. |
| The historical totals can be reconciled separately. | Supplied share cash, options premium cash, fees, dividends, withholding and the historical ending market value reproduce the app's stocks, derivatives, combined net P/L and gross trading P/L. Their scope differs from the current LONG row. Completeness of the supplied history is not independently established. |
| Exercise/assignment is better supported than an ordinary same-day trade explanation. | The user's transaction-history transcription describes offsetting transfers and zero-value expiry removals. A separate read-only broker cash-flow response contains a description referencing a QQQ call exercise. Its clearing and settlement dates differ. Structured linkage to the two share records, transfers and basis adjustments is unavailable. |

The leading hypothesis is that an average-cost calculation retained the old LONG average while the reported share quantity became negative, then applied a weighted purchase formula to that signed quantity. The match is unusually specific, but other upstream accounting paths could produce the same values. No financial field has been corrected and no application behavior has been changed.

## Deployed provenance and capture conditions

| Item | Verified observation |
| --- | --- |
| Production revision label | `c864762df221d5e4ce5c2e6d04678b54988d0a3e` |
| Docker image identity | `sha256:9e29c45af5f2f110a7872a215f01b293189350b6be417df1d820492af3746f34` |
| Installed SDK | `moomoo-api` 10.10.7008 |
| MCP library / Python | MCP SDK 1.25.0 / Python 3.12.13 |
| OpenD version evidence | Health returned `gateway_version="1010"` at `2026-10-07T11:18:54Z`. A full OpenD patch/build identity was not independently established; this abbreviated server value must not be equated with the SDK package version. |
| Environment/account | REAL, MARGIN, US-authorized account. The exact discovered account ID was used and is withheld. No default, rounded or guessed ID was substituted. |
| Safety state | Connected; `trading_mode=READ_ONLY`; execution journal `DISABLED` in the read-only health response. No unlock, cost-setting, order, recovery or deployment action was performed. |
| Initial boundary capture | `2026-10-07T10:53:52.373757+00:00` through `2026-10-07T10:53:52.534487+00:00` |
| Same-response FastMCP capture | Position request `2026-10-07T11:04:19.238208+00:00` through `2026-10-07T11:04:19.408293+00:00`; associated bounded-history capture completed at `11:05:08.123662+00:00`. |
| Later cash-flow capture | `2026-10-07T11:21:57.167252+00:00` through `2026-10-07T11:22:21.584134+00:00`. Explicit settlement-related clearing dates were queried; records remain private. |

The exact share-position request used `code="US.QQQ"`, `position_market=TrdMarket.NONE`, no P/L ratio bounds, `trd_env="REAL"`, the exact discovery ID, `refresh_cache=True` and `show_option_strategy_view=False`. SDK defaults were `asset_category=AssetCategory.NONE`, `currency=Currency.USD` and `acc_index=0`. In this SDK, the NONE market enum serializes as `"N/A"`; that is not a failed or unknown market selection.

The captures ran in an isolated process inside the existing production container, using its installed application and SDK. The initial capture replayed one captured DataFrame through the service and serializer. The second used the installed account tool's real FastMCP dispatch with a capture subclass and a context wrapper restricted to reads. It observed the protobuf, DataFrame, service records and resulting JSON from the same position call. It did **not** instrument the running HTTP worker. A separate connected MCP read corroborated the stable cost fields, but its changing market-price fields were a different snapshot. Thus same-response preservation is established for the installed code path, not an atomic comparison of two independently timed HTTP/SDK requests.

The four installed application modules below had SHA-256 hashes identical to the inspected main checkout. Docker revision metadata therefore has independent source-level corroboration for this path. This does not attest every file in the image.

## Code-path evidence

All repository links pin the inspected commit rather than a moving branch.

| Boundary | Function and source | Observed behavior |
| --- | --- | --- |
| Account discovery/selection | [`TradeService.get_accounts`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/services/trade_service.py#L683), and [`get_accounts`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/account.py#L54-L61) | Accounts are read from the provider; the MCP boundary preserves identifiers as strings. |
| Position service | [`TradeService.get_positions`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/services/trade_service.py#L872-L911), [`_query_positions`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/services/trade_service.py#L914-L964) | Resolves the read account, forwards query flags, checks the provider return code and returns DataFrame records. No basis/P&L calculation appears here. |
| SDK payload guard | [`as_frame`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/services/sdk_response.py#L21-L40) | Checks that the successful payload is a DataFrame and returns that object. No financial validation or recalculation. |
| MCP account tool | [`get_positions`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/account.py#L218-L231), [`run_blocking`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/offload.py#L31-L48) | Calls the service on a worker thread, then serializes identifiers. |
| Identifier serialization | [`IDENTIFIER_FIELDS`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/serialization.py#L19-L21), [`serialize_identifiers`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/serialization.py#L69-L104) | Converts known account/position/combo/deal identifiers; leaves other scalar fields unchanged. Numeric equality was checked through both structured output and parsed JSON text records. |
| Paper recovery | [`reconcile_execution`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/trading.py#L735-L746), [`PaperExecution.observations/reconcile`](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/services/paper_execution.py#L462-L510) | Concerns journal-owned SIMULATE execution outcomes. It is not REAL-account financial reconciliation and was not invoked. |

The installed SDK sources are version-specific local evidence, not repository files:

| Installed source in `moomoo-api` 10.10.7008 | Mapping |
| --- | --- |
| `moomoo/trade/trade_query.py`, `PositionListQuery.unpack_rsp`, lines 312–345 | `costPrice → cost_price`; `averageCostPrice → average_cost`; `dilutedCostPrice → diluted_cost`; `plVal → pl_val`; `unrealizedPL → unrealized_pl`; `realizedPL → realized_pl`. Optional presence checks produce validity flags or the SDK missing-value sentinel. |
| Same decoder, lines 324, 330–334 | Quantity, price and market value pass through; `plRatio` and `averagePlRatio` are multiplied by 100 to produce percentages. This scaling is distinct from monetary-field preservation. |
| `moomoo/trade/open_trade_context.py`, `position_list_query`, lines 416–464 | Builds a DataFrame from decoded records. |
| `moomoo/trade/trade_query.py`, `HistoryDealListQuery.unpack_rsp`, lines 822–838 | Exposes the raw `createTime` string as `create_time`. The epoch timestamps are not retained in the normal deal DataFrame, so they were captured separately from the protobuf. |

The captured `PositionListQuery` **class-source** SHA-256 was `0acd934d8ebe784d0c0740976555f9cf92f056aa2c9d1d9c2609305bd6b79b1b`, independently rechecked in the installed image. It hashes `inspect.getsource(PositionListQuery)`, rather than the whole SDK file or only `unpack_rsp`.

“OpenD boundary” here means the protobuf response object delivered to the SDK decoder. No broker server implementation, internal OpenD accounting implementation or raw network-packet trace was inspected. Attribution to one of those upstream layers would exceed the evidence.

## Scopes and chronology

| Scope | Treatment in this investigation |
| --- | --- |
| Current QQQ shares | Exact `US.QQQ`, LONG position, live snapshot. Broker fields remain authoritative as reported values, not independently certified accounting. |
| QQQ option contracts | Separate instrument group requiring underlying identity and contract metadata. Premium cash is not share cost automatically. |
| Underlying-level historical QQQ P/L | Includes the supplied share and option history at the selected historical cutoff. It is not the current share row's unrealized P/L. |
| Realized/unrealized | Separate decomposition under the provider's basis. Their sum matching position P/L checks consistency, not correctness. |
| Average/diluted cost | Different metrics. For securities, the API describes `cost_price` as diluted cost, and unrealized/realized amounts for universal securities accounts as average-cost metrics. [Position field documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html). |
| Gross/net | Trading cash P/L is kept separate from share/option fees, gross dividends and dividend withholding. |
| Cutoffs | App holdings observation: October 7; app historical analysis: explicitly through October 5; refreshed captures: live October 7. An “All Dates” label does not erase the screen's cutoff. |

For the September 5 pair, broker deal strings and separately captured epochs both put SELL before BUY. Epoch comparison indicates UTC−04:00, consistent with exchange daylight time, rather than Singapore local time. The strings and epochs are preserved unchanged in the private evidence. The provider states that stock trading timestamps use exchange time; its configurable futures timezone does not apply here. [Trading timezone FAQ](https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html).

The user-supplied starting inventory and the two equal-size share records imply LONG → SHORT → LONG when applied in that reported order. No intermediate broker position snapshot was captured. Reported deal timestamps establish record chronology; they do not establish when transfers were posted or in which order a basis engine processed exercise/assignment adjustments. The cash-flow response's distinct clearing and settlement dates also warn against treating a posting date as the settlement date.

The user's app transcription describes offsetting transfer entries split between the original long shares and the temporary short shares, plus zero-value removal of the expiring calls. Their aggregate transfer equivalents reproduce the screen's transfer amount to cents, and the displayed transfer cash directions offset. Neither zero net transfer cash nor zero option-removal value proves zero cost-basis or holding-period effect. The reported zero-value removals are not additional premium executions.

Broker cash-flow descriptions independently support the presence of a QQQ call exercise. The returned rows do not supply structured instrument identity, an exercise/assignment link to both share records, transfer basis, or before/after cost. Assignment of the companion call and the detailed adjustment sequence remain hypotheses.

## Position-reversal hypothesis: support and limits

| Evidence | Implication |
| --- | --- |
| Sell-before-buy records, conditional negative inventory, and exact cost/P&L reproduction | Supports a signed-inventory weighted-average mechanism. A high average alone would be much weaker evidence. |
| Closed-short gain is precisely the adjustment needed to bridge share lifetime cash flow to the current LONG position-P/L candidate | Supports exclusion of the closed-short outcome from that LONG scope, conditional on the supplied totals. It does not prove the broker allocation policy. |
| Exercise description, offsetting transfers, matching strike-level settlement cash and expiry removals | Supports exercise/assignment-related delivery over an ordinary discretionary round trip. No authoritative event linkage was obtained. |
| Published cost rules distinguish long and short costs and define universal-account holding periods using beginning/end-of-day positions | A naive long-only formula applied to negative quantity is not established by those rules. An intraday zero crossing also cannot automatically be declared a holding-period reset. [Position cost guidance](https://www.moomoo.com/sg/support/topic5_37). |
| Published historical-analysis guidance values selected dates at closing prices and describes special transfer/exercise treatment | The historical app analysis may have adjustment rules that differ from live position fields. Its exercise-stock example uses a prior closing-price basis, so neither strike-only accounting nor buy-first treatment is universally justified. Applicability to this exact account/event remains unverified. [P/L Analysis guidance](https://www.moomoo.com/sg/support/topic5_614?from_platform=1). |
| A conventional chronological long/short split gives a different reopened-long basis from the app-compatible average | Choosing an apparently reasonable generic algorithm would not itself explain the app. The synthetic calculation report shows this distinction. |

The alternative explanation is a deliberate or inconsistent difference between app and API accounting scopes/adjustments, rather than a simple incorrect field computation. Documentation supports several scope differences but does not fully explain this event. Both explanations remain open.

## History coverage and data-access limits

The newer authorized retrieval queried all symbols without a filled-status restriction. Both history endpoints succeeded for these explicit, non-overlapping intervals:

| Requested inclusive dates | Deal query | Order query |
| --- | --- | --- |
| 2025-01-01 through 2025-06-29 | Retrieved | Retrieved |
| 2025-06-30 through 2025-12-26 | Retrieved | Retrieved |
| 2025-12-27 through 2026-06-24 | Retrieved | Retrieved |
| 2026-06-25 through 2026-10-07 | Retrieved | Retrieved |

Calls were paced conservatively with a shared four-second minimum gap; no interval failed or needed a retry. This establishes successful retrieval of those requested windows, not lifetime completeness. The SDK/API exposes no continuation token in these responses, and retention completeness was not established. The previously observed gateway rejection above 360 days was not independently found as a universal documentation limit; 180-date chunks avoid relying on it.

The original filled-status-only, exact-share retrieval could omit options and cancelled orders with partial executions. Executions are the primary fill evidence; requested quantity, limit price and fractional purchase-budget `amount` are not executed consideration. A fractional order with requested `qty=0` can still have positive `dealt_qty`. Orders corroborate their deals rather than adding another cash flow. Current/history copies require stable-identity deduplication; combo parent/leg and settlement representations require linkage before aggregation.

Exact shares are selected by equality with `US.QQQ`. Derivatives require broker `stock_owner`/underlying metadata. Metadata queries resolved some current/recent QQQ contracts but left expired contracts unresolved. A standard-symbol parser validated against owner, expiry, call/put and strike metadata could be a future fallback for ordinary contracts; adjusted contracts and unresolved combo legs require further evidence. No `startswith("US.QQQ")` classification or QQQM inclusion was used to establish complete QQQ totals.

The current MCP history docstrings incorrectly describe empty start as maximum range and empty end as today: [`get_history_orders`, lines 652–653](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/trading.py#L652-L653) and [`get_history_deals`, lines 697–698](https://github.com/evil-sparkle/moomoo-api-mcp/blob/c864762df221d5e4ce5c2e6d04678b54988d0a3e/src/moomoo_mcp/tools/trading.py#L697-L698). Installed SDK history methods call `normalize_start_end_date(..., 90)` at `open_trade_context.py:785,868`; `common/utils.py:104–117` implements:

| Supplied dates | Installed SDK window |
| --- | --- |
| Both supplied | Explicit bounds |
| Only end supplied | Start 90 days before end |
| Only start supplied | End 90 days after start |
| Both omitted | End today according to the SDK process clock; start 90 days earlier |

Date-only bounds expand to `00:00:00` and `23:59:59`. The provider documents these default combinations and a limit of 10 requests per 30 seconds per account for each history endpoint. [Historical orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-list.html), [historical executions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-history-order-fill-list.html). No docstring or retrieval implementation is changed by this branch.

Missing evidence includes the independently verified opening basis and complete account lifetime history; authoritative expired/adjusted option metadata and delivery multipliers; notices linking exercise and assignment to the share/transfer entries; basis and holding-period allocation before and after those entries; all adjustment/fee coverage; the provider's rounding policy; and the exact app screen classification rules. The screenshots themselves were not available for independent inspection; this investigation uses the user's transcription as external evidence.

## Recommended next steps and potential fixes — review only

1. Privately obtain broker statements and exercise/assignment notices linking the paired share deliveries, option removals and transfers. Request the basis before/after each posting, long/short P/L allocation and holding-period rules. Compare the app and API at the same mark/cutoff without changing any cost setting.
2. Ask the provider to explain the reproduced signed-quantity calculation and whether the current LONG row intentionally excludes the closed SHORT outcome. Include the exact local evidence privately; the public synthetic example is a reproducible illustration, not a submitted broker reproduction.
3. In a separate future change, consider correcting history-window documentation and adding a narrow read-only diagnostic response that preserves broker fields and separately labels assumptions, external app evidence, scope, cutoff, provenance, coverage and unresolved events. Failure must remain failure, never an empty interval; retries should be bounded and limited to documented transient/rate failures, with conservative backoff.
4. Consider an upstream average-cost remedy only after policy is confirmed. Review handling of zero crossings, separate long/short inventory, transfers and exercise/assignment event linkage. Do not reorder broker records, replace `average_cost` with `cost_price`, hardcode an app value, or use lifetime purchases divided by lifetime bought shares as current-position basis.
5. Any later implementation should verify numeric preservation, fractional/cancelled partial fills, stable-ID/combo deduplication, QQQ versus QQQM underlying identity, both event orders, zero crossings, transfer/lifecycle gaps, cutoffs, rounding and failed history intervals. Paper recovery is outside this financial-accounting scope.

## Read-only verification procedure

Prerequisites are an already authenticated OpenD session, authorized REAL-account read access, the inspected SDK/image, and a private evidence directory outside Git with directory mode 0700 and file mode 0600. If access fails, record the failed operation and stop dependent claims; do not unlock or alter settings.

Discover accounts and retain the exact returned string ID. Record image revision/digest, installed versions, health, flags and UTC capture times. Capture the protobuf at `PositionListQuery.unpack_rsp` in an isolated process and observe its DataFrame through the installed service and account-tool dispatch. Compare monetary fields directly and ratios with their documented SDK scaling. Treat separate live queries as separate snapshots.

Retrieve explicit bounded history windows without restricting to filled order statuses, preserving identifiers and raw timestamps. Pace calls below provider limits, record each interval's return status, and obtain exercise/assignment/transfer evidence separately. Resolve option underlyings with authoritative metadata before aggregation. Reproduce the supplied cash calculations with Decimal as described in the calculation report, then identify which scope and adjustment assumptions remain unverified.

Do not invoke trading, unlock, cost modification, reconciliation/recovery or deployment tools. Keep raw outputs and exact account calculations local. No capture script or diagnostic tooling is delivered in this branch.

## Report validation

Offline Decimal checks reproduced the supplied five metrics, the diluted alias, both supplied P/L ratios and the separate historical totals. The same candidate was checked against the later captured SDK mark and matched its five metrics at observed precision. The synthetic report calculations were independently recalculated. Installed source mappings, application hashes, captured boundary equality, timestamp offsets and read-only health/cash-flow access were checked. Markdown links and the reports-only diff were reviewed.

No application tests, lint/type checks, new regression tests, OpenSpec changes or deployment checks are claimed for this reports-only deliverable. A numerical match and an internally consistent provider row do not certify a reconciled position or a corrected financial figure.
