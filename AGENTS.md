# Security Constraints
- **Secrets & Credentials**: Never read, view, grep, or output contents of `.env`, `.env.*`, shell configuration files (`~/.zshrc`, `~/.bashrc`, `~/.zshenv`, `~/.zprofile`, `~/.bash_profile`), or private credential files. They contain sensitive private credentials. `.env.example` and example templates is explicitly permitted.

# Development contract

- Do not use regex in code; use explicit string methods or conditional logic.
- Load only relevant references from [docs/README.md](docs/README.md).
- After implementing or applying a change, autonomously run relevant tests and
  checks, inspect failures, fix them, and repeat until they pass. Follow the check
  selection in [docs/development.md](docs/development.md).
- Then execute the installed `openspec-verify-change` skill for that change
  (`$openspec-verify-change` in Codex, `/openspec-verify-change` where slash-invoked
  skills are supported), and run strict OpenSpec validation.
  Fix actionable findings and repeat affected checks and verification. A checked
  task list or successful structural validation alone is not verification.
- Return control only when verified, genuinely blocked by an external dependency
  or permissions, or a decision requires human judgment. Continue independent
  work while blocked; report evidence and the precise remaining blocker. Routine
  test failures and repairable implementation issues are not reasons to stop.
- This repository contract takes precedence over generated apply instructions
  suggesting a pause for routine errors or a handoff after the task list is done.
- Report commands/results and distinguish local, mocked, container, and live
  acceptance. Never claim unavailable live checks passed or archive a change
  with required acceptance outstanding.
- Keep this contract outside generated OpenSpec files. Regenerate those files
  with `./scripts/update-openspec.sh`; do not hand-edit them.
