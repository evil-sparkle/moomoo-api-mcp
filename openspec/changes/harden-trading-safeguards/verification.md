# Verification log

## Instrument verification completed — 2026-09-23

This section supersedes the open instrument work in the historical entries below.
The source adapter now uses `option_contract_multiplier` for premium valuation
and preserves `option_contract_size` independently. Missing, non-finite,
non-positive and non-numeric multipliers refuse assessment. It does not substitute
size or a hardcoded 100. Regression tests cover valid unequal field values,
invalid/missing multipliers, single-order and combo caps, and a service refusal
before any SDK placement or unlock.

### Currency evidence for the supported US subset

- Moomoo's [US stock/ETF product page](https://www.moomoo.com/au/invest/us-stock)
  states that US stock/ETF orders require conversion to USD.
- Moomoo Singapore's [Auto Currency Exchange documentation](https://www.moomoo.com/sg/support/topic5_1090)
  identifies US stocks and US options as USD-settled products.
- The Options Industry Council's [equity/index option premium explanation](https://www.optionseducation.org/advancedconcepts/equity-vs-index-options)
  describes dollar-and-cent premiums, providing the quotation-unit cross-check.
- The earlier live US order and stock/ETF/option position responses reported USD.

Together, the broker's product-wide documentation and live corroboration establish
USD for the server's currently supported US STOCK/ETF/DRVT valuation subset.
This is not a claim that a market prefix alone determines currency or that other
classifications or markets have been verified. No currency field was added to the
snapshot; the policy continues to use its verified subset table.

At 14:24:13 UTC, the updated source adapter was loaded in a separate Python process
against the existing local OpenD. It queried AAPL, SPY, AAPL 2026-09-25 340 Call and
AAPL 2026-09-25 415 Call. Classifications were STOCK, ETF, DRVT and DRVT; monetary
multipliers were 1, 1, 100 and 100. Both option contract sizes were separately 100.
Each passed a pure BUY limit assessment using the USD cap. This verifies live
quote decoding and policy compatibility; it is not an order submission or an
independent currency measurement. No account query, unlock or order mutation was
issued by this follow-up. The running MCP service and deployed image were unchanged.

Task 1.1 is complete. Task 1.2a is also complete using the authorized GTC/GTD
provider evidence already recorded in Stage 2. Task 1.2's after-close retention
observation is the only remaining Stage 1 task; do not archive before it is recorded.

## Official-documentation follow-up — 2026-09-23

This follow-up supersedes the field-selection gap in the earlier live-run notes.
The current [official OpenD snapshot documentation](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)
(version 10.11) defines `option_contract_size` as shares per contract and
`option_contract_multiplier` as the contract multiplier. Its protocol definitions
also keep these separate, without the pinned SDK docstring's index-only qualifier.
Moomoo's [option-premium explanation](https://www.moomoo.com/ca/support/topic10_143)
values premium using price, contract multiplier and number of contracts.

The resulting implementation decision is to use `option_contract_multiplier` for
premium valuation and keep contract size separate. The documentation establishes
meaning; the existing independent position arithmetic confirms the sampled numeric
values on the installed 10.10.7008 gateway/SDK. No version upgrade was performed.
We do not need an adjusted contract merely to resolve which documented field to use.
The prior inference favoring `option_contract_size` was incorrect.

This is a documentary correction. The deployed adapter remains unchanged and still
refuses capped options. Activation needs focused regression checks proving that
valuation uses the multiplier when size differs, and refuses missing or invalid
multipliers. Currency confirmation remains a separate unfinished part of task 1.1.

## Live verification — 2026-09-23

Verified Stage 1 source `e8b2c52f2d92cb3b9d27308a04942690ee1cd621`, Python 3.12,
OpenD/SDK 10.10.7008. The local Linux/amd64 container was built from that commit;
the VPS uses its published CI image. Account identifiers, credentials and raw
account responses are omitted from this repository record.

The operator authorized bounded SIMULATE checks, then explicitly authorized REAL
reads and gateway locking only, and finally authorized direct VPS deployment.
No unlock or REAL order was issued. Market-selection implementation belongs to the
separate Stage 1.1 change.

| Task | Result |
| --- | --- |
| 1.1 instrument facts | Snapshots, classifications and independent position arithmetic collected; field selection resolved by the follow-up above; adapter activation and full US currency confirmation remain open |
| 1.2 terminal DAY-order retention | Current and historical queries passed immediately and three times later in the same session; after-close observation pending |
| 1.2a non-DAY submission | Optional; not run |
| 1.3 locked REAL reads | Lock and all seven reads passed |
| 9.2 live paper safeguard sequence | Placement, not-sent over-cap modification, cancellation and non-halted health passed |
| 9.3 VPS deployment | Stage 1 deployed; authenticated initialize and post-deployment checks passed |

### Instrument reads and the remaining semantic gap

At 12:13:35 UTC, `get_market_snapshot` returned:

| Code | Last | Bid | Ask | Lot size | Contract size | Contract multiplier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| US.AAPL | 339.75 | 340.5 | 340.6 | 1 | NaN | NaN |
| US.SPY | 773.38 | 772.4 | 772.47 | 1 | NaN | NaN |
| US.AAPL260925C340000 | 3 | 3 | 3.15 | 100 | 100 | 100 |
| US.AAPL260925C415000 | 0.0001 | 0 | 0.01 | 100 | 100 | 100 |
| US.QQQ260925P737000 | 1.27 | 1.25 | 1.28 | 100 | 100 | 100 |
| US.QQQ260925P742000 | 2.26 | 2.29 | 2.32 | 100 | 100 | 100 |

`get_stock_basicinfo` with that explicit code list returned every code: AAPL was
`STOCK`, SPY `ETF`, and all four options `DRVT`. Option nominal value and underlying
lot multiplier were `N/A`. The quiet AAPL 415 call reported volume zero and
`update_time=2026-09-21 09:30:00`: no trades today does not imply all quote fields
are absent. Its bid normalizes to absent; the positive stale last and ask remain
usable under Stage 1's existing rule, which has no age threshold.

Two matched QQQ option positions supplied independent `market_val`, `qty` and
`nominal_price` values. `market_val / (qty * nominal_price)` was approximately 100
for each, independently confirming the premium multiplier for those contracts.
Both candidate snapshot fields also equalled 100. Therefore this arithmetic does
**not** distinguish which field remains correct when contract deliverables differ
from the premium multiplier. In particular, the previous prediction that
`option_contract_multiplier` would be `N/A` for non-index options was contradicted
by the gateway. The historical SDK evidence below is preserved as the earlier
finding, not as a current observation. No global option-field activation was made.

USD was present in the sampled US order/position responses. This is sample
confirmation, not a proof that every instrument the supported US subset may admit
has the same quote currency. Task 1.1 stays open for those two semantic conclusions.

### Bounded paper order and retention

The live harness used the unchanged Stage 1 `TradeService`, its real instrument
adapter and an explicit US SDK context. The context was supplied by the harness
because the current MCP server's default context does not discover the US paper
account; this verifies Stage 1 service behavior, not Stage 1.1 MCP market routing.
The wrapper rejected any mutation outside SIMULATE, the selected paper account,
and the bounded order; it prohibited unlocking and recorded each SDK mutation.

At 12:12:02 UTC it placed one `US.AAPL` BUY `NORMAL` order, quantity 1, price USD 1,
`DAY`, with `acc_id="0"`, quantity cap 1 and notional cap USD 2. Automatic account
resolution selected the sole eligible US paper account. A price-only modification
to USD 3 raised `OrderNotSentError` with calculated notional USD 3 exceeding USD 2;
the wrapper recorded **zero additional SDK mutations**. The only mutations were
placement and cancellation. Cancellation was acknowledged, and both queries then
returned `CANCELLED_ALL`, `dealt_qty=0`, `dealt_avg_price=0`, and stored TIF `DAY`.

| Observation (UTC) | Current-order query | Historical-order query |
| --- | --- | --- |
| 2026-09-23 12:12:03 | terminal order found | terminal order found |
| 2026-09-23 12:13:09 | terminal order found | terminal order found |
| 2026-09-23 12:20:01 | terminal order found | terminal order found |
| 2026-09-23 12:33:01 | terminal order found | terminal order found |

Both queries preserved the caller remark `s1-check-20260923-day`, broker order ID,
code, side, quantity, price, status, submission/update timestamps and TIF. The
history query was filtered by code/date and matched to the exact broker ID on the
client, since it has no order-ID parameter. These are positive matches; an empty
query would not prove that an uncertain submission never existed.

The service health result at 12:12:03 UTC was `connected`, quote/trade `ok`,
`logged_in: true`, `trading_mode: SIMULATE`, and `execution_halted: false`.
Task 9.2's four live steps passed. The after-close retention observation must
reuse this cancelled order; it must not resubmit the placement. Approximately 21 minutes of
same-session visibility does not establish a maximum retention window.

The one-shot harness and raw evidence are retained in the operator's local
`stage1-verification-2026-09-23` evidence directory, outside Git. Its dispatch
marker prevents an accidental repeat placement. While the local container remains
available, the read-only follow-up is:

```sh
docker exec moomoo-stage2-local /app/.venv/bin/python /tmp/stage1-paper-check.py recheck
```

### REAL reads after an acknowledged lock

At 12:13:01 UTC, the explicit US trade context acknowledged
`unlock_trade(is_unlock=False)`. The following reads then succeeded without any
unlock:

| Service operation | Provider path | Result |
| --- | --- | --- |
| get_accounts | get_acc_list | RET_OK |
| get_assets | accinfo_query, REAL | RET_OK |
| get_positions | position_list_query, REAL | RET_OK |
| get_orders | order_list_query, REAL | RET_OK |
| get_deals | deal_list_query, REAL | RET_OK, empty result |
| get_max_tradable | acctradinginfo_query, REAL | RET_OK |
| preview_combo_order | Stage 1 service preview for a two-leg US option combo | success |

The empty deal result is a successful read, not evidence of fills. The first six
checks exercised their underlying SDK reads; preview exercised the service path.
The VPS's deployed Stage 1 service subsequently acknowledged its public
`lock_trade` method as well. No read-only JIT unlock is needed on this observed
gateway. This does not simulate relock failure or prove HALTED recovery under a
fault; those transitions remain covered by the automated tests.

### VPS deployment and authentication

The operator authorized SSH through the existing `robin-vultr` alias and direct
deployment. The checkout is `/home/robin-vultr/moomoo`; rootless Docker runs
container `moomoo-api-mcp`. It previously ran `c4b42c7`.

The deployed commit is `e8b2c52f2d92cb3b9d27308a04942690ee1cd621`, image tag
`e8b2c52`, digest
`sha256:867dc574d2e657dd7f551f4c68e9193a244faab482f656e2b00440e9ed1a55fd`.
[CI run 35734460223](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/35734460223)
passed 964 tests with one skipped test, Ruff lint/format, basedpyright, strict
OpenSpec validation, image publication and the container smoke test. This updates
the earlier 928-test count recorded when Stage 1 was initially implemented.

The production sequence was:

1. `scripts/deploy.sh --prepare e8b2c52f2d92cb3b9d27308a04942690ee1cd621` checked
   the ECR tag, checked out the target and pulled its image without restarting.
2. A one-off container ran the target's `load_settings()` without starting OpenD
   or the MCP server. It validated `READ_ONLY`, quantity cap 500, no notional cap,
   streamable HTTP, configured authentication and `locks_gateway_at_rest: true`.
3. `scripts/compose-prod.sh up -d --no-deps moomoo-mcp` recreated the service.
   The named volume `moomoo_opend-data` remained mounted at
   `/home/opend/.com.moomoo.OpenD`.
4. `python3 scripts/deploy_verify.py verify --url http://127.0.0.1:8000/mcp
   --timeout 90` confirmed MCP initialization with the configured token.
5. An initialization request without authentication returned HTTP 401.
6. A separate service probe in the deployed container used the installed Stage 1
   code and configuration. Its public `lock_trade` returned `status: locked`,
   `execution_halted: false`, `halt_cleared: false`. Health at 12:19:28 UTC was
   `connected`, quote/trade `ok`, `logged_in: true`, mode `READ_ONLY`, and
   `execution_halted: false`. This was a service probe, not an HTTP tool call.

The container started at 12:18:57 UTC and still had zero restarts at the follow-up.
The user systemd unit was active; only host-loopback MCP port 8000 was published.
No secret file was viewed or edited, no gateway unlock was issued, and no REAL
order test was run. The optional REAL-order step is not required for task 9.3.

## Original offline verification — historical record

The sections below describe the original implementation session, which had no
gateway. Their pending statuses are superseded by the dated live results above
and the current task checklist. They are retained to distinguish offline evidence
from observations made later with account access.

What task group 1 actually established, and what it could not.

Two harnesses produce everything below.

- `scripts/verify_sdk_facts.py` — offline. It re-reads the pinned SDK and re-checks
  every fact the design rests on. `tests/test_sdk_facts.py` runs the same checks in
  CI, so a future SDK bump fails with the fact's own name.
- `scripts/verify_gateway_facts.py` — operator-run. It covers what only a live
  gateway and an account can answer. Its two non-read-only phases each need their
  own authorization flag, and it has no code path that places a REAL order.

## Environment this run had

No OpenD gateway and no account. The session container has no `MOOMOO_*`
configuration, no `.env`, and nothing listening on 11111 or 11112. Every check that
needs a gateway is therefore **not run**, not *failed* — see "Still outstanding".

## 1.1 Instrument facts — partially established

### Established offline, from the pinned SDK (`moomoo-api` 10.10.7008)

Run `uv run python scripts/verify_sdk_facts.py` to reproduce. 18/18 facts hold.

| Fact | Evidence |
| --- | --- |
| The snapshot carries no security classification | `MarketSnapshotQuery.unpack_rsp` emits no `sec_type`, `stock_type`, `security_type` or `sec_type_str` key |
| The snapshot carries no currency field | no key in `unpack_rsp` contains `currency` or `curr_code` |
| The snapshot carries the valuation prices | `last_price`, `bid_price`, `ask_price` and `lot_size` are all emitted |
| `get_stock_basicinfo` takes an explicit `code_list` and returns `stock_type` | `get_stock_basicinfo(self, market, stock_type='STOCK', code_list=None)` |
| `SecurityType` defines `STOCK`, `ETF` and `DRVT` | the three classifications Decision 3 handles |

Decisions 2 and 3 stand on these as written: classification comes from
`get_stock_basicinfo`, and currency has to be established outside the snapshot.

### Two findings that sharpen the design

**1. The `'N/A'` sentinel reaches the multiplier, not only bid and ask.**

`unpack_rsp` falls back to the string `'N/A'` for three fields:
`ask_price`, `bid_price` **and `option_contract_multiplier`**. Design Decision 3
names only bid and ask. The adapter's normalization must cover the multiplier too,
or a `'N/A'` multiplier reaches arithmetic as a string.

**2. `option_contract_size` cannot report itself missing.**

```
snapshot_tmp['option_contract_size'] = record.optionExData.contractSizeFloat
```

No `HasField` guard, and `contractSizeFloat` is an optional proto field. An
unpopulated message reads back `0.0` with `HasField` `False`, so once the SDK has
copied the value out, "the gateway did not send it" and "the gateway sent zero" are
the same number. A zero multiplier would value every option at nothing. This is why
the adapter treats a non-positive multiplier as **absent** and refuses, rather than
trusting the number — the design's normalization rule, now with a reason attached.

### The monetary multiplier — narrowed, not closed

Task 1.1 asks which option field, multiplied by the quoted price, yields the cash
value of one contract, and forbids answering it by observing that a field equals
100.

The SDK's own field table answers the *semantic* half. From
`OpenQuoteContext.get_market_snapshot`'s docstring, verbatim:

| Field | SDK description | Translation |
| --- | --- | --- |
| `option_contract_size` | 每份合约数 | units of the underlying per contract |
| `option_contract_multiplier` | 合约乘数，**指数期权特有字段** | contract multiplier, **a field specific to index options** |
| `option_contract_nominal_value` | 合约名义金额 | contract nominal amount |
| `option_owner_lot_multiplier` | 相等正股手数，指数期权无该字段 | equivalent underlying lot count; absent for index options |

The same table appears twice in the SDK, for the snapshot and for the option-chain
call, with identical wording.

This is documentary evidence, not a coincidence of 100, and it points the other way
from the field name that sounds right:

- `option_contract_size` is the units-per-contract figure. Price × that is the cash
  value of one contract, which is the monetary multiplier Decision 3 defines.
- `option_contract_multiplier` is **index-options-only**. For a US *equity* option
  the gateway has no reason to populate it, and the SDK then returns the string
  `'N/A'`. Reading it as the general monetary multiplier would refuse every US
  equity option, or worse, silently pick up an index option's figure.
- `option_contract_nominal_value` is a nominal amount, which task 1.1 already ruled
  out as evidence.

**What is still missing is the independent cross-check.** Task 1.1 requires the
chosen field to be confirmed against a figure established another way — a known
premium cash value, or a matched position's market value. That needs an account.
`scripts/verify_gateway_facts.py positions` does exactly that arithmetic
(`market_val ÷ (qty × price)` against each candidate) and names the field that
reproduces the broker's own number.

**Consequence for this change:** the design's un-verified state still applies. An
option is not assessable, and is refused, while a notional cap is configured. The
adapter carries the evidence above at its single point of change, so flipping it on
once the cross-check lands is a one-line change with a test, not a redesign.

### Currency — unchanged, still provisional

Confirming that every `US`-quoted instrument quotes in USD needs the live
instrument reads. The verified-market table keeps its `provisional` status and no
market is added to it.

## 1.2 / 1.2a Paper-order retention — not run

Needs a gateway and paper-trading authorization. `verify_gateway_facts.py
paper-retention` places one far-from-market SIMULATE `DAY` limit order, cancels it,
and probes both queries immediately and later in the session;
`paper-retention-recheck` takes the after-close half with the recorded order id.

One offline finding shapes how the measurement must be taken:
`history_order_list_query` accepts **no** `order_id` — only `code`, dates and
environment. The retention probe therefore filters the history result by order id
client-side, which the script does.

Task 1.2a is optional and separately authorized. It is not attempted, and nothing
in this change depends on it.

## 1.3 REAL reads against a locked gateway — not run

Needs a REAL gateway, the trade credential, and explicit authorization to change
the gateway's lock state. `verify_gateway_facts.py real-reads` covers it behind
`--i-authorize-real-gateway-lock`.

The design's associated risk is **confirmed offline**, which raises its importance:

```
# 解锁要求先拉一次帐户列表, 目前仅真实环境需要解锁
ret, msg, acc_id = self._check_acc_id(TrdEnv.REAL, 0)
```

That call sits **outside** the `if is_unlock:` branch in
`OpenSecTradeContext.unlock_trade`, so *locking* also resolves a REAL account. A
gateway that cannot resolve one produces a failing `lock_trade` — and Decision 8
makes `lock_trade` the only exit from `HALTED`. The spec already states that a
failed lock leaves the halt in place rather than appearing to clear it; task 1.3
records whether this gateway can lock at all.

Also confirmed offline, for Decision 7: `unlock_trade` caches the credential in
`_ctx_unlock` on success and clears it on lock, and
`OpenTradeContextBase.on_api_socket_reconnected` replays that cached unlock after
every reconnect. Startup auto-unlock therefore does leave the gateway unlocked for
the life of the process, exactly as the design argues.

## Still outstanding — the exact checks that need account access

| # | Check | Needs | How |
| --- | --- | --- | --- |
| 1.1a | Snapshot and classification fields for a US stock, ETF and equity option | quote gateway | `verify_gateway_facts.py instruments --stock … --etf … --option …` |
| 1.1b | All three price fields for an option with no trades today | quote gateway | same run, `--quiet-option` |
| 1.1c | Independent cross-check of the monetary multiplier | account with an option position | `verify_gateway_facts.py positions` |
| 1.1d | Confirm `US` instruments quote in USD | broker confirmation | not automatable from the snapshot; the SDK exposes no currency field |
| 1.2 | Terminal paper-order retention, both windows | SIMULATE account + paper-order authorization | `paper-retention`, then `paper-retention-recheck` after the close |
| 1.3 | REAL reads while locked, and whether `lock_trade` succeeds | REAL gateway + credential + lock-state authorization | `real-reads --i-authorize-real-gateway-lock` |

Until 1.1c and 1.1d land, a capped deployment refuses options and refuses every
market outside the verified table. Both are the fail-closed direction, and both are
what the design already specifies for the un-verified state.
