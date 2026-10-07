# Security Constraints
- **Secrets & Credentials**: Never read, view, grep, or output contents of `.env`, `.env.*`, shell configuration files (`~/.zshrc`, `~/.bashrc`, `~/.zshenv`, `~/.zprofile`, `~/.bash_profile`), or private credential files. They contain sensitive private credentials. `.env.example` and example templates is explicitly permitted.

# Development guidance

- Do not use regex in code; use explicit string methods or conditional logic.
- Load only relevant references from [docs/README.md](docs/README.md).
- Regenerate managed OpenSpec files with `./scripts/update-openspec.sh`;
  do not hand-edit them.

## Completion policy

- For every task, establish acceptance criteria and run the relevant tests and
  checks from [docs/development.md](docs/development.md). Autonomously inspect
  failures, fix their causes, and repeat affected checks until they pass. Do not
  weaken tests, patch installed dependencies, or count skipped checks as passing.
- When working on a selected OpenSpec change, follow passing tests/checks with
  `openspec-verify-change` for that change. Fix actionable findings and repeat
  affected checks and verification. Without a selected change, verify the task's
  acceptance criteria directly; do not create or select an unrelated change just
  to run OpenSpec verification.
- Run strict OpenSpec validation when working on a selected OpenSpec change or
  modifying OpenSpec files. Checked tasks and structural validation alone do not
  prove acceptance.
- Return control only when verified, genuinely blocked by an external dependency
  or permissions, or a decision requires human judgment. Continue independent
  work while blocked; report evidence and the precise remaining blocker. Routine
  test failures and repairable implementation issues are not reasons to stop.
- This policy takes precedence over generated apply instructions suggesting a
  pause for routine errors or a handoff after the task list is done.
- Report commands/results and distinguish local, mocked, container, and live
  acceptance. Never claim unavailable live checks passed or archive a change
  with required acceptance outstanding.
- Keep verification evidence in the PR description/comments or CI, rather than
  adding a `verification.md` artifact to each change.
