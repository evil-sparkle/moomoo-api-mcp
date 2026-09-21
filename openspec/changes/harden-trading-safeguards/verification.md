# Verification log

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
