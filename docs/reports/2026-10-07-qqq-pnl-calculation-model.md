# QQQ accounting mechanism: algebra and synthetic reproduction

This accompanies the [investigation report](2026-10-07-qqq-pnl-investigation.md). **Every numerical example below is invented.** The illustrative instrument `US.SYNTH` is a placeholder, not captured broker evidence. Real QQQ account figures and exact transaction timestamps are withheld from the public repository; their Decimal reproduction is retained locally.

The account-specific calculation supplied by the user reproduces five disputed API metrics at observed precision. The equations below explain that match without asserting where the broker implements them or selecting a corrected basis.

## Inputs and scope

Let:

- `q0` be the verified starting LONG share quantity and `C0` its displayed purchase cash for this candidate.
- `n` be the equal quantity sold and repurchased in the settlement pair, with `n > q0`.
- `S` and `B` be the displayed cash proceeds/cost of that sale and purchase, respectively.
- `q` be the later remaining LONG quantity and `m` its mark.
- `TS` and `TB` be all share-sale and share-purchase cash included in the supplied candidate scope.
- `Dg` be gross dividends in that scope.

The signed weighted-average extension is the hypothesis being investigated. The cash-flow calculation assumes the same share inventory scope, no unsupported opening position, and the stated treatment of closed-short P/L and dividends. Those assumptions require independent verification for a real account.

| Synthetic input | Value |
| --- | --- |
| Initial LONG quantity `q0` | 2.5 shares |
| Initial displayed purchase cash `C0` | 150.00 |
| Pair: SELL `n` | 100 shares; displayed cash `S=7000.00` |
| Pair: BUY `n` | 100 shares; displayed cash `B=6500.00` |
| Later SELL | 2 shares; displayed cash 160.00 |
| Remaining LONG quantity `q` | 0.5 shares |
| Live mark `m` / unrounded value `V=q*m` | 80.00 / 40.000 |
| All share-sale cash `TS` | 7160.00 |
| All share-purchase cash `TB` | 6650.00 |
| Gross dividends `Dg` / withholding | 2.00 / 0.60 |
| Share fees / option fees | 1.00 / 1.50 |
| Separate option premium cash | Sales 18.00; purchases 30.00; net −12.00 |
| Historical-cutoff ending share value `VH` | 39.00 |

For Decimal reproduction, construct inputs from these strings with precision 60. Preserve displayed cash amounts; do not substitute purchase budgets, requested order quantities or floating-point products of rounded fill prices. Do not round intermediate averages or P/L. Quantize only for comparison: costs to `0.001`, monetary API illustrations to `0.0001`, and app-style totals to `0.01`, using `ROUND_HALF_UP` as an explicit comparison convention. The provider's complete rounding policy is unverified.

## Actual calculation path of the candidate

The synthetic recorded order is SELL then BUY. An ordinary long-only moving-average update is deliberately extended across negative quantity:

| Step | Quantity | Carried basis | Average used |
| --- | --- | --- | --- |
| Opening | `q0=2.5` | `C0=150` | `A0=C0/q0=60` |
| SELL 100, retaining old average | `q0-n=-97.5` | `(q0-n)*A0=-5850` | 60 carried from the old LONG position |
| BUY 100 | `q0=2.5` | `-5850+B=650` | `A_signed=650/2.5=260` |
| Later SELL 2, same assumed average | `q=0.5` | `q*A_signed=130` | 260 |

This is a working arithmetic path, not just comparison with fixture constants. Its central assumption is that the old long average survives a negative-inventory state and contributes a **negative** carried basis to the next buy:

```text
A0       = C0 / q0
A_signed = ((q0 - n) * A0 + B) / q0
```

When an ordinary purchase formula is valid only within one inventory side, this extension is not justified by that formula. It is the proposed numerical mechanism, not an independently verified broker policy.

## Alternatives that must remain separate

The hypothetical BUY-first long-only path gives:

```text
A_buy_first = (C0 + B) / (q0 + n)
            = 6650 / 102.5
            = 64.878048780487804878...
```

A subsequent sale within positive inventory leaves that illustrative average unchanged. In the real supplied evidence this alternative matches the displayed app average, but not its unrealized amount exactly to the cent. It is a counterfactual treatment, not permission to rewrite the recorded event order.

A conventional SELL-first split with separate long and short inventory, **ignoring transfer adjustments**, instead does this:

1. Close 2.5 old long shares at 70 against their average 60.
2. Open 97.5 short shares at 70.
3. Cover those 97.5 shares at 65.
4. Open the remaining 2.5 new long shares at 65.

That illustrative reopened-long average is **65**, distinct from both 260 and 64.8780487804878.... A later two-share sale leaves 0.5 shares at this assumed basis. This comparison demonstrates why neither “split at zero” nor “process buys first” can be chosen solely because it appears reasonable. Daily holding-period rules and exercise/transfer adjustments may require a different allocation.

## Reproduction of the five position metrics

For this candidate only, the temporary closed-short gain is:

```text
G_short = (n - q0) * (S/n - B/n)
        = 97.5 * (70 - 65)
        = 487.5
```

The following equations reproduce all five synthetic metrics and the securities `cost_price` alias:

| Metric | Calculation | Exact synthetic result | Comparison precision |
| --- | --- | --- | --- |
| `average_cost` | `A_signed` | 260 | 260.000 |
| `unrealized_pl` | `q * (m - A_signed)` | −90 | −90.0000 |
| `pl_val` | `TS - TB + V - G_short + Dg` | 64.5 | 64.5000 |
| `realized_pl` | `pl_val - unrealized_pl` | 154.5 | 154.5000 |
| `diluted_cost` | `(V - pl_val) / q` | −49 | −49.000 |
| Securities `cost_price` illustration | Same diluted metric | −49 | −49.000 |

The negative diluted cost is allowed as an illustration of recovered cash exceeding remaining basis. An unusual average or a negative diluted cost alone is not evidence of an error.

`realized_pl + unrealized_pl = pl_val` holds exactly even while the average disagrees with either alternative calculation. It is an internal consistency check, not proof of reconciliation. Realized and diluted values here are derived from the candidate position-P/L total; the five matching outputs are therefore not five statistically independent proofs of a root cause.

The candidate position-P/L formula excludes the closed-short gain from the current LONG row, includes gross dividends and does not subtract the historical share fees/withholding. These are explicit scope assumptions. A numerical match supports this allocation hypothesis; it does not establish the provider's contract for every account or holding period.

Use `V=q*m` in the calculation. A market-value display rounded to cents can differ from the unrounded value enough to change a four-decimal P/L comparison. Likewise, computing P/L from a cost already rounded to three decimals can fail to reproduce the broker's more precise P/L.

## Transfers and exercise/assignment evidence

The synthetic adjustment scenario includes offsetting transfer entries at the two delivery prices, split between the original long quantity and temporary short quantity:

```text
Transfer equivalent = q0 * (S/n) + (n-q0) * (B/n)
                    = 2.5 * 70 + 97.5 * 65
                    = 6512.50
```

Synthetic transfer-in and transfer-out equivalents of equal size cancel in the aggregate cash-flow view. Their direction pairing and accounting treatment are an assumed illustration. They are **not** extra independent purchases/sales added to `TS` or `TB`.

Also suppose the illustrative expiring calls have zero-value removal entries. That supports investigating lifecycle delivery, but a removal is not itself a premium execution, and zero displayed value does not prove zero basis effect. Event IDs, delivery multipliers, notices and posting/basis rules are needed to link option lifecycle, share deliveries and transfers without double-counting them.

No transfer is silently applied as a cost correction in the candidate arithmetic. The mechanism tests an assumed basis path that could have omitted, misapplied or differently scoped adjustments. Without their verified treatment, this remains partial/unverified reconciliation.

## Separate historical stocks, derivatives and combined P/L

At the synthetic historical cutoff, use `VH=39`, rather than the live `V=40`. Assume zero beginning inventory/value for this illustrative full period and no unsupported additional adjustments:

| Scope | Calculation | Synthetic result |
| --- | --- | --- |
| Stocks gross trading | `TS - TB + VH` | 549.00 |
| Stocks net | `549 + Dg - withholding - share_fees` | 549.40 |
| Derivatives net | `option_premium_cash - option_fees` | −13.50 |
| Combined gross trading | `549 - 12` | 537.00 |
| Combined fees | `1 + 1.5` | 2.50 |
| Net cash dividends | `2 - 0.6` | 1.40 |
| Combined net | `549.40 - 13.50`, or `537 - 2.50 + 1.40` | 535.90 |

The bridge from the synthetic current LONG position-P/L candidate to stocks net is:

```text
Stocks_net - Position_PL
  = G_short - share_fees - withholding + (VH - V)
  = 487.5 - 1 - 0.6 + (39 - 40)
  = 484.9

64.5 + 484.9 = 549.4
```

Thus disagreement between a current LONG row and historical share P/L can be accounted for by closed-short scope, fees, withholding and a different valuation cutoff, without changing the broker row. Options remain a separate component.

If an app aggregate uses ambiguous “Stock Sell” and “Stock Buy” labels, hypothetical totals `7178` and `6680` would include the synthetic option cash of 18 and 30 in addition to share cash. Their difference plus `VH` gives gross combined trading P/L `537`. This is a possible label interpretation, not a claim that those app labels are documented as including options. In the private evidence, analogous differences match the supplied option premium net, which warrants obtaining a definitive screen-scope explanation.

## Reproduction result and limits

Offline Decimal recalculation verified the signed-inventory path, the buy-first comparison, the separate long/short comparison, all five synthetic position metrics, transfer equivalent, historical totals and scope bridge. These are report calculations, not production regression tests or evidence that a broker implementation follows this path.

The exact supplied QQQ calculation also matches all five reported API fields at their precision. The implementation boundary was independently checked as described in the investigation report. The remaining questions are where upstream accounting applies this mechanism, whether it is intentional for this scope, how transfers and exercise/assignment affect basis, and which app valuation/rounding policy explains the residual cent difference. No corrected calculation is selected or implemented.
