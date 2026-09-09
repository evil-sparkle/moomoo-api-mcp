# R4: Preview Combo Order Account Impact

Status: Draft. Priority: High. Dependencies: R2, R3, existing combo-order support.
Requirement: [Combo preview](../specs/combo-order-preview/spec.md).

## Problem and Outcome

The server can submit a multi-leg order, but cannot expose the SDK's account-impact
calculation for that package before submission. Callers currently lack a read-only
way to inspect changes in margin and buying power through this MCP server.

Success means a caller can preview the same legs, quantity, price, and account
they intend to submit, receiving timestamped provider calculations without a write.

## Scope and Requirements

- R4.1: Add `preview_combo_order` backed by `comboorder_tradinginfo_query`.
- R4.2: Reuse placement leg validation and account selection, preserving exact
  closing IDs and explicit leg ratios.
- R4.3: Return available SDK impact fields and UTC observation time; preserve
  unavailable values as null rather than substituting zero.
- R4.4: Never place, modify, cancel, unlock, or reserve funds as part of preview.
- R4.5: Surface SDK failures and label results as point-in-time estimates, not
  acceptance or execution guarantees. Do not normalize price signs.

Preview of existing-order modifications, fee estimates not supplied by this SDK
response, strategy selection, and resolution of the price-sign convention are excluded.

## Proposed Interface

`preview_combo_order(combo_legs, price, qty, order_type="NORMAL",
trd_env="REAL", acc_id="0")`

Input leg dictionaries use the same contract as placement. Proposed output is a
dictionary containing `checked_at` and the SDK fields `nlv_change`,
`initial_margin_change`, `maintenance_margin_change`, `option_bp`,
`max_withdraw_change`, and `bp_decrease`. Verify missing-value handling and units
against the installed SDK before finalizing public field descriptions.

Add a method to `services/trade_service.py` and a tool to `tools/trading.py`.
Use existing helpers; avoid constructing a parallel validation path that can drift.
Read-only policy permits preview, but broker permissions can still reject it.

## Implementation Checklist

- [x] Verify query signature, response shape, units, and missing-field semantics.
- [x] Share validation/account selection with placement without changing its behavior.
- [x] Implement SDK query mapping and timestamped response.
- [x] Register the tool and document the difference between preview and submission.
- [x] Add service tests and an MCP retrieval-to-preview workflow test.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| Valid opening package | Expected legs and arguments reach query; impact returned |
| Closing IDs retrieved as strings | Exact IDs reach SDK preview |
| Missing ratio or invalid side | Validation error; no gateway call |
| Account omitted | Same market/environment selection as placement |
| Missing impact value | Null, not invented zero |
| SDK rejection | Explicit error; no fallback trading write |
| Preview under READ_ONLY | Policy permits query without unlock |

For every preview test, assert placement, modification, cancellation, and unlock
methods were never called. Use synthetic IDs and account fixtures.

## Rollout, Risks, and Completion

This is an additive tool. Broker availability and permissions must be documented
from actual results, not assumed universal. Preview values can change before an
order is submitted. A read-only gateway smoke test is optional; a non-marketable
live order is not a preview validation method.

Done when acceptance tests and [shared gates](README.md) pass.
Reference: [SDK API documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/comboorder-tradinginfo-query.html).
