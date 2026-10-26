## 1. Diagnostics

- [x] 1.1 Report lock-at-rest observations, document semantics, and pass tests for failure, recovery, absent policy, and nonblocking health.

## 2. Restart and process follow-ups

- [x] 2.1 Await gateway connections after MCP replacement; verify shell syntax and run the isolated container smoke test when Docker is available.
- [x] 2.2 Add the PR spec-impact prompt and reconcile the archived checklist against source evidence, preserving the live REAL verification gap.

## 3. Verification

- [x] 3.1 Run relevant Python tests, lint, type checks, and strict OpenSpec validation; record any unavailable runtime gates.

## Workflow follow-up

- Implementation merged via [PR #73](https://github.com/evil-sparkle/moomoo-api-mcp/pull/73)
  on 2026-10-26 as `2b60ae2be08687a87fe27d585c17a4e598295ea7`.
- Canonical system-health specification synchronized and change archived in a
  separate follow-up PR under the repository policy.
- Any backport to the selected hotfix remains separate.
- Task 3.12 is explicitly deferred and non-blocking by user decision on
  2026-10-26. Accept the documented OpenD-wide behavior for the current design;
  successful live unlock/re-lock does not count as live scope proof.


## Verification evidence — 2026-10-25

- Focused service/health/server tests: 327 passed.
- Authorization, published contract, environment/Compose, and trading control
  transport tests: 149 passed. Only the existing protobuf deprecation warning.
- Ruff lint and format checks passed; basedpyright: 0 errors, warnings, or notes.
- Strict OpenSpec validation: 35 passed, 0 failed.
- `bash -n scripts/smoke-test.sh` and `git diff --check` passed.
- Final unchanged `bash scripts/smoke-test.sh`: exit 0. Synthetic gateway restart,
  MCP/container restart, new gateway connections, JSON/no-session-ID transport,
  network isolation and controls-volume persistence all passed. The script now
  uses portable `mktemp` without GNU-only suffix arguments for macOS execution.
- Earlier smoke attempts were not acceptance evidence: one hit the old GNU-only
  mktemp option; another was invalidated by editing the running shell script.
- During the initial repository verification, no remote deployment, mode change,
  REAL unlock, order, or production restart was performed. The user subsequently
  authorized the isolated local unlock test documented in
  `unlock-verification.md`; it successfully unlocked/relocked and restored the
  original paper service, with no orders. The implementation was subsequently reviewed and merged via PR #73.
