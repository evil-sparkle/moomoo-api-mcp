# Tasks

## 1. Client authorization and account selection

- [x] 1.1 Add restricted credentials and request-scoped tool/environment/account grants; verify HTTP denial, worker propagation, cached pagination and stored evidence tests, and document the configuration.
- [x] 1.2 Resolve executable accounts consistently in capability discovery and preparation; verify two-account allowlist and client-scope regressions.
- [x] 1.3 Allow a safely restricted ChatGPT client on REAL backends; verify configuration, runtime preflight and token-separation tests and document migration.

## 2. Execution lifecycle

- [x] 2.1 Persist attributed environment/account pauses and enforce them at dispatch while preserving journaled cancellation; verify restart, scope and dispatch tests and document the operator endpoint.
- [x] 2.2 Add opt-in observation of existing journals and controls outside writable modes; verify creation remains disabled and broker mutation policy remains enforced, and document state ownership.
- [x] 2.3 Remove quote-classification dependency from paper cancellation; verify unavailable metadata does not block a valid journaled cancellation.
- [x] 2.4 Align source/Compose caps and add offline settings/storage validation; verify explicit NONE and invalid/missing storage regressions and document the changed source default.

## 3. Deployment transactions

- [x] 3.1 Persist private immutable-image effective configuration snapshots and preserve all state mounts across mode changes; verify Compose and rollback tests and update the runbook.
- [x] 3.2 Preflight candidate application settings/storage before service replacement and restore the actual prior configuration on failure; verify edited-environment rollback and preflight-order tests.
- [ ] 3.3 Support all selected trading modes and explicit tunnel disablement through Ansible; verify host/playbook transition tests and update native deployment documentation.
- [x] 3.4 Apply exclusive project ownership to deployment, rollback and service lifecycle commands; verify lock ownership and restart-race regressions.

## 4. Integration verification

- [ ] 4.1 Pass complete Python tests, native deployment tests, Ruff lint/format, basedpyright and strict OpenSpec validation; independently review the security and deployment boundaries and fix findings.
- [ ] 4.2 Review the public diff for secrets, create the implementation PR and register it with this thread.

## Workflow follow-up

- After the user approves merging the completed implementation PR, archive this OpenSpec change in a separate PR and automatically merge that archive PR according to repository guidance.
