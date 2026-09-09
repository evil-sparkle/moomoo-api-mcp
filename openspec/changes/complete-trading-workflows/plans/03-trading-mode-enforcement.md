# R3: Enforce Trading Mode in the Service Layer

Status: Draft. Priority: High. Dependencies: None; health integration uses R1.
Requirement: [Configured trading mode](../specs/trading-policy/spec.md).

## Problem and Outcome

Startup currently says absent credentials mean simulation-only, but there is no
server policy enforcing that claim. Tool defaults are REAL, and gateway unlock
state is distinct from this process's intended trading capabilities.

Success means a deployment's configured mode determines which writes it permits,
including calls made directly to `TradeService`.

## Scope and Requirements

- R3.1: Add `MOOMOO_TRADING_MODE`: `READ_ONLY`, `SIMULATE`, or `REAL`; reject unknown
  values. Proposed default is READ_ONLY for server and direct service construction.
- R3.2: Enforce mode before any gateway request for placement, combo placement,
  modification, cancellation, and unlock.
- R3.3: Permit reads and previews in all modes subject to gateway permissions.
- R3.4: A password must not elevate the mode. Auto-unlock runs only in REAL mode.
- R3.5: Report configured mode and explicit policy errors. Never silently switch
  an order's requested environment or automatically retry a rejected live write.

This policy does not replace user authorization for an order, broker permissions,
or trading unlock. Credential storage redesign and per-user authorization are excluded.

## Proposed Contract

| Mode | Reads/previews | SIMULATE writes | REAL writes | Unlock |
| --- | --- | --- | --- | --- |
| READ_ONLY | Allowed | Denied | Denied | Denied |
| SIMULATE | Allowed | Allowed | Denied | Denied |
| REAL | Allowed | Allowed | Allowed | Allowed |

READ_ONLY as the new default is a breaking design choice requiring proposal
approval. Retain existing tool `trd_env` defaults: a mismatch returns a policy
error rather than sending an order to a different account environment.

Create one policy representation passed from `server.py` into `TradeService`.
Guard service methods, not only MCP wrappers. Keep quote subscription operations
available in every mode because they do not mutate trading orders or positions.

## Implementation Checklist

- [x] Approve the default and migration behavior before implementation.
- [x] Add configuration parsing and policy injection with a read-only default.
- [x] Guard every write/unlock service entry point before gateway calls.
- [x] Restrict auto-unlock and expose mode in health/configuration output.
- [x] Update fixtures to state their intended mode explicitly.
- [x] Update README and tool guidance, including cancellation behavior.

## Acceptance Tests

Parametrize every guarded method over all modes and applicable environments.
Denied cases must assert zero gateway calls, including account-selection queries.
Test both actual MCP dispatch and direct service use.

Additional cases: unknown configuration, absent mode, configured password in
READ_ONLY/SIMULATE, failed unlock in REAL, and allowed account reads in READ_ONLY.
Cancellation in a denied environment must also be denied, with a clear operator
message; the policy does not selectively bypass itself for cancellation.

## Rollout, Risks, and Completion

Ship a migration notice before rollout: existing write deployments must configure
SIMULATE or REAL explicitly. Expose the mode in startup diagnostics without logging
secrets. Operators must understand that READ_ONLY also prevents modifying or
canceling existing orders. Rollback restores a prior release explicitly; do not
add fallback logic that silently enables REAL mode after a configuration error.

Done when the full policy matrix and [shared gates](README.md) pass.
