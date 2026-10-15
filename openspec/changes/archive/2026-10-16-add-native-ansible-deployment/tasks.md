## Source implementation

- [x] Add successful-CI manifest selection with exact run/attempt and immutable digests.
- [x] Add the shared manual deployment workflow and automatic main-CI dispatcher.
- [x] Add application-owned Ansible, public release helpers and named-state preservation.
- [x] Add rollback, READ_ONLY and authenticated MCP gates, and separate broker readiness.
- [x] Document environment access, migration, rollback and verification limits.

## Validation and acceptance

- [x] Exercise synthetic Ansible first migration, upgrade failure and first-cutover recovery.
- [x] Render the pinned overlay through real Compose and check external volume identity.
- [x] Exercise MCP checks against a local HTTP fixture with synthetic credentials.
- [x] Complete repository lint, typing, regression and OpenSpec checks.
- [x] Verify the exact PR head in GitHub Actions.
- [x] Configure deployment environment/tailnet/SSH access as the operator.
- [x] Verify a live READ_ONLY cutover, user-service startup and remembered OpenD login.
- [x] Verify hosted ChatGPT forwarding if the optional tunnel is selected.

## Live acceptance evidence

- PR #62 merged after all 16 applicable exact-head GitHub checks passed.
- Main CI [38819690033](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/38819690033), attempt 1, published the immutable release for commit `45ebae326325fe132c6a713527bd9b1fd2f2f5e0`.
- Automatic deployment [38820255332](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/38820255332) completed successfully on 2026-10-16 (Asia/Singapore).
- Independent host checks matched both running image digests to that release, preserved both named state-volume identities, enabled native user startup, disabled legacy startup, and confirmed user lingering. Other running containers retained their identities.
- A read-only call through the connected Moomoo MCP integration returned `connected`, `READ_ONLY`, quote login true, and successful quote/trade probes after cutover. Local tunnel liveness and readiness also passed.
