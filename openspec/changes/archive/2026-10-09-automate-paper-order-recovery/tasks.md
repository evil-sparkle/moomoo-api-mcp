# Tasks

## 1. Durable recovery identity and policy

- [x] 1.1 Add schema 3 migration, persisted placement tags, checks and atomic audited recovery decisions; verify store migration, restart, audit and rollback fault tests and document schema compatibility.

## 2. Automatic recovery engine

- [x] 2.1 Implement shared-read evidence classification, successful-round policy and late-order accounting; verify unique/conflicting/negative/error/mutation-target scenarios using real SQLite and a fake broker, and document policy limitations.
- [x] 2.2 Integrate a process-owned stoppable worker and recovery context in execution/health replies; verify startup, uncertain response, session independence, scheduling and shutdown tests.

## 3. Remove operator capability

- [x] 3.1 Remove operator authentication, acknowledgement tool, settings and deployment examples; update the recovery runbook and verify normal authentication, tool contracts and Compose configuration tests.

## 4. Integration validation and PR

- [x] 4.1 Run full tests, Ruff, type checking and strict OpenSpec validation; inspect the public diff for sensitive data and record validation limits in the PR.
- [x] 4.2 Commit, push and open the PR; register its URL with T3 and verify the linked PR and remote checks.

## Workflow follow-up

- Archive the change after the user approves PR merge, following AGENTS.md.
- The separate live provider-validation change remains independent and incomplete.
