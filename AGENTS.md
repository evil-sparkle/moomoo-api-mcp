# Repository guidance

- Do not use regex in code; use string methods or conditionals.
- Regenerate managed OpenSpec integrations with `./scripts/update-openspec.sh`;
  never hand-edit generated files.
- This repo is public on github, when open pr, upload release or artifacts, make sure no secrets or sensitive info was there.

## Completion policy

- Establish acceptance criteria and run the applicable
  [checks](README.md#checks-and-completion). Fix failures and repeat affected
  checks; never weaken tests, patch dependencies or count skipped checks as passing.
- For a selected OpenSpec change, run `openspec-verify-change` after checks pass,
  fix actionable findings and repeat affected checks and verification. Otherwise
  verify the task directly; do not select an unrelated change. Run strict OpenSpec
  validation whenever changing OpenSpec files or working on a selected change.
- Continue until verified or blocked by permissions, an external dependency or a
  decision requiring human judgment; continue independent work while blocked.
  Routine failures and completed task lists are not handoff points, regardless
  of generated apply instructions.
- Report commands, results and precise blockers. Distinguish local, mocked,
  container and live acceptance; never claim unavailable checks passed or archive
  with required acceptance outstanding. Keep evidence in PRs or CI, not per-change
  `verification.md` files.
