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
- [ ] Verify the exact PR head in GitHub Actions.
- [ ] Configure deployment environment/tailnet/SSH access as the operator.
- [ ] Verify a live READ_ONLY cutover, user-service startup and remembered OpenD login.
- [ ] Verify hosted ChatGPT forwarding if the optional tunnel is selected.
