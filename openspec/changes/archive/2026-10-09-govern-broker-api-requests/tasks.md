# Tasks

## 1. Quota engine and policies

- [x] 1.1 Add PyrateLimiter-backed dispatch accounting, weighted pending reservations, pacing, and scoped broker policies; verify rolling-window, cancellation, scope, and spacing tests and document the policy inventory.

## 2. SDK and MCP integration

- [x] 2.1 Share dispatch budgets across services and internal calls, apply bounded MCP admission, and preserve safe live/paper mutation boundaries; verify service and MCP integration tests including delayed workers and exhausted unlock capacity.

## 3. Integration verification

- [x] 3.1 Run full pytest, Ruff, basedpyright, strict OpenSpec validation, and applicable container checks; review the public diff for sensitive data and record the results.

## Workflow follow-up

- PR #50 opened and registered with this thread.
- User authorized merging after all checks pass.
- Requirements synced and this change archived on 2026-10-09.
