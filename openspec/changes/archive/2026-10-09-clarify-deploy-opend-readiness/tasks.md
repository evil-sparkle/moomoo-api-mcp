## 1. Readiness reporting

- [x] 1.1 Add bounded authenticated health probing with JSON/SSE validation and explicit quote-login evidence, preserving MCP initialization verification; verify with real-curl and stateless FastMCP tests.
- [x] 1.2 Integrate separate readiness reporting after deployment verification; verify deployment fixtures preserve rollback, gateway warning exit success, cancellation, and prepare behavior.

## 2. Operator guidance

- [x] 2.1 Clarify initial interactive login in `.env.example` and the deployment runbook; review matching safe recovery commands and documented readiness timeout behavior.

## 3. Validation

- [x] 3.1 Verify tests cover healthy login, startup recovery, missing/false login, degraded or disconnected health, malformed/refused/partial responses, token privacy, bounded timeout, and deployment success without rollback on gateway warnings.
- [x] 3.2 Run deployment tests, repository lint/format/types and CI-pinned strict OpenSpec validation; record the commands and distinguish synthetic checks from live acceptance.

## Validation evidence

- With temporary GNU Bash 5.2 and the isolated Python 3.12 environment on PATH,
  `.venv/bin/python -m pytest -q --maxfail=3`: 1,387 passed, 1 skipped,
  117 subtests passed. The stock macOS Bash 3.2 does not support the existing
  production Compose wrapper's empty-array handling under `set -u`.
- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed.
- `.venv/bin/basedpyright`: zero errors and warnings.
- GNU Bash 5.2 `bash -n scripts/deploy.sh` and `git diff --check`: passed.
- CI-pinned `@fission-ai/openspec@1.13.1 validate --all --strict --no-interactive`:
  27 passed, zero failed.
- HTTP checks use real curl and synthetic local servers, including actual
  stateless FastMCP JSON and SSE output. Broker connections are mocked. No live
  deployment, broker login, or trading operation was performed.
