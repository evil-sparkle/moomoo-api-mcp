# Proposal

## Why

Only option-chain calls currently have provider admission. Twenty-one other
runtime SDK operations have documented frequency limits, including internal
order checks, paper recovery, snapshots, and live unlock/relock calls.

## What Changes

- Use PyrateLimiter's exact rolling-window buckets for provider accounting.
- Centralize operation policies, shared groups, account/user scopes, refresh
  conditions, pagination exemptions, and dispatch spacing.
- Apply bounded asynchronous admission to MCP calls and immediate admission to
  direct synchronous callers; reserve pending capacity until actual dispatch.
- Govern internal order checks and recovery calls through the same budgets.
- Reserve live write and unlock/relock capacity before unlocking, and reserve
  paper mutation capacity before writing a dispatch marker. Never replay writes.
- Document every runtime SDK operation's policy and the process-local boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-request-governance`: Extend shared operation admission to all limited
  runtime broker operations while preserving dispatch and cancellation safety.

## Impact

Services, SDK dispatch, MCP worker offloading, dependency configuration, tests,
and README. Public tool arguments and result schemas remain compatible.
