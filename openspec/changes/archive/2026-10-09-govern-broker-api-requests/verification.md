# Verification

Verified on 2026-10-09 using mocked broker services and isolated containers.
The branch incorporates main's automatic paper recovery and its documentation
archive, preserving persisted correlation tags and the OpenSpec CI pin.

- Inventory: 30 runtime SDK operations; 22 have timed policies, including the
  previously governed option chain. Eight operations have no published numeric
  frequency policy. README and the registry link to broker documentation.
  Max-quantity sharing is explicitly a conservative interpretation.
- Combined recovery, admission, dispatch, startup, and transport checks:
  **210 passed**. The expanded broker-admission checks separately passed
  **37 tests**, including automatic recovery sharing history and position pools,
  quota errors not counting as negative evidence, successful later recovery,
  and stored outcomes consuming no new quota.
- Full parallel suite: `uv run --with pytest-xdist pytest -q -n 2 --maxfail=3`:
  **1,643 passed, 1 skipped, 106 subtests passed**. Two subprocess checks exceeded
  their 10/20-second deadlines in that run. Both passed on an isolated rerun
  (**2 passed in 17.92 seconds**) without code changes. They cover interpreter
  shutdown with a stuck connection and paper recovery across process restarts.
  Xdist is an ephemeral verification dependency, not a project dependency.
- Ruff lint/format and basedpyright: passed with zero type errors or warnings.
- Strict OpenSpec validation with the CI-pinned 1.14.1 CLI: **27 passed** after
  syncing the requirements and archiving this change. Existing Purpose and
  scenario headings were preserved; scope and cancellation contracts were
  clarified for account pools and protected mutation transactions.
- Docker build and `scripts/smoke-test.sh`: passed startup, authentication,
  gateway isolation, gateway restart/reconnection, server restart, and
  stateless MCP requests.
- `scripts/test-paper-container.sh`: **C01–C04 passed**, upgrading from an
  independently built current-main application to the updated application,
  with disposable volumes and no network. The former predates rate-limit
  changes but includes the automatic-recovery journal format required by the
  current fixture. Its 31 installed source modules match main; all 33 modules
  in the updated image match the workspace and include PyrateLimiter 4.5.0.
- The public change contains source, documentation, dependency metadata, and
  mock fixtures only; Gitleaks is checked before publishing the update.

The dispatcher coordinates one process-owned gateway/user. Independent clients
and process restarts remain outside its history. No live broker orders or
provider response-loss experiments were performed.

Requirements are synced to the main specification. This change is archived
under the user's authorization to merge after all checks pass. GitHub's full
CI run and remaining checks must pass before the PR is merged.
