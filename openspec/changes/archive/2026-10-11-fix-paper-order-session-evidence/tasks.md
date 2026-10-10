# Tasks

## 1. Initial session compatibility (historical completed work)

- [x] 1.1 Add the exact RTH/legacy-N/A eligibility path and regression tests for
  modification/cancellation; verify accepted legacy combinations, rejected
  missing/contradictory/extended fields, and durable NOT_SENT marker-zero outcomes.
- [x] 1.2 Verify immutable refused intents, duplicate suppression and delayed
  modification visibility with N/A broker fields, including after restart; run
  the stateful SQLite paper tests and retain the existing guards.

## 2. Local verification and handoff

- [x] 2.1 Run the full mock-backed suite, Ruff, formatting, type checks and strict
  OpenSpec validation; record actual results and the installed SDK sentinel
  evidence in verification.md without claiming live provider acceptance.
- [x] 2.2 Update the existing validation record and prepared continuation prompt
  to link the completed local fix while leaving live tasks unchecked; verify
  the running pinned image and live journal were not modified by development.

## 3. Documented session migration and local activation

- [x] 3.1 Use `Session.RTH` exclusively for placement and fresh NORMAL/DAY target
  eligibility; remove the explicit deprecated SDK argument and all application
  reads/fallbacks to the legacy flag.
- [x] 3.2 Update regressions for missing/arbitrary legacy data, rejected unknown
  sessions, exactly-once dispatch, immutable refusals and modification visibility
  across restart, keeping all other paper controls intact.
- [x] 3.3 Run focused/full suites, Ruff, formatting, type and strict OpenSpec
  checks; record the migration's actual results separately from earlier evidence.
- [x] 3.4 Build a distinct local linux/amd64 image from a secrets-free staged
  context; record source/patch/file/image identity and verify the packaged module.
- [x] 3.5 Replace only the Mac test container and verify original journal/volumes,
  unchanged operation/evidence hashes, agent/operator MCP authentication, readiness,
  SDK/OpenD versions and account orders/positions without provider mutations.
  Update the durable continuation route to the actual activated image.

## Workflow follow-up

- The operator has authorized this local image replacement and standing paper
  orders/fault injection. Record conservative test bounds and coordinate one
  executor for future validation; preserve old refused/uncertain intents.
- The operator now authorizes a dedicated fix branch/PR, merge to main and
  archive of this completed implementation. Provider validation remains separate.
