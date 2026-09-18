# Change: Resolve Deploy Verification Configuration Through Compose

## Why

The deploy probe became an authenticated MCP `initialize` because a bare `GET`
answered 401 under bearer auth and read like a failure at the bottom of every
successful deploy's logs. Authenticating it, though, meant knowing the exact
`MCP_AUTH_TOKEN` bytes the container was started with — and the container is
started by Docker Compose, from two env files and two compose files, with
Compose's own dotenv grammar: quotes, `=` and `:` delimiters, `export`
prefixes, CRLF, inline comments, escape sequences, variable interpolation.

`scripts/deploy.sh` answered by re-implementing that grammar in Bash. The
history is the argument against it: `MCP_AUTH_TOKEN="abc"` probed with the
quotes included; `:`-delimited lines were skipped entirely; `$VAR` and
`'...\'...'` had to be refused one case at a time, each refusal a commit
discovered by a 401 against a healthy deploy. Every new case was a new
failure mode, because the probe and the container disagreed about the same
file, and the subset could only grow by chasing Compose's full grammar.

There is one implementation of that grammar available on the deployment
host: Compose itself. `scripts/compose-prod.sh config --format json` resolves
the exact configuration the deployment will run — same Docker context, same
env files, same compose files — and reports the service's own environment.
The refactor deletes the Bash parser and has a small helper read what Compose
already resolved.

The helper found its own bug on the way: `config` prints values as compose
input, where a literal `$` is escaped as `$$`. The container holds
`literal $X`; the JSON says `literal $$X`. The helper decodes that one
escape so the probe sends what the container received — found by the new
container-agreement test, which compares the token in a real Compose-started
container with the header the verifier actually sent it.

## What Changes

- **New `scripts/deploy_verify.py`** (host Python 3.10+, standard library
  only): reads `MCP_AUTH_TOKEN` from the service environment Compose resolves
  via `compose-prod.sh config --format json`, decodes Compose's `$$` output
  escaping, refuses values an Authorization header cannot carry, and runs the
  probe: an MCP `initialize` through curl with the token on stdin, validated
  as a JSON or SSE-framed JSON-RPC result with the matching id.
- **`scripts/deploy.sh`** keeps commit selection, checkout, image selection,
  rollback and service control, and no longer interprets any env file:
  `parse_env_token`, the `MCP_AUTH_TOKEN` precedence loop,
  `initialize_result_ok`, the Bash probe loop and the 0600 header file with
  its cleanup trap are deleted. Configuration is checked after the target
  checkout and the new `.deploy.env`, before `pull` and `up`.
- **Failure policy, preserved and restated**: a configuration error before
  startup restores the previous checkout and `.deploy.env` without restarting
  the running services; a failed verification after startup keeps the
  restart-based rollback; a refused probe (401/403) and a 200 that is not an
  initialize result fail immediately; an endpoint that is not answering yet
  is retried within `DEPLOY_VERIFY_TIMEOUT` seconds of elapsed time
  (`0` = one attempt); failed log collection never prevents rollback.
- **`--prepare`** still pulls without starting services or probing, and now
  checks the configuration first.
- **Host dependency**: `python3` 3.10 or newer is required on the host and
  checked before any deployment state changes; the helper runs on the host,
  not in the image, and uses only the standard library.
- **Self-reexec safety**: the helper is invoked from the checked-out target
  commit, never from the old checkout or the temporary file the reexecuted
  script runs from. Tests cover the hand-over from main's pre-helper
  `deploy.sh` (frozen as a test fixture) and from a checkout holding a stale
  helper.
- **Tests**: the stubbed deployment tests keep covering ordering, rollback
  and reexec; a new layer runs the real Compose CLI against the real wrapper
  and compose files, and — in the mandatory `compose-agreement` CI job —
  starts disposable stand-in containers to prove what Compose gave the
  container is what the probe sent. Dummy credentials; no OpenD, broker or
  ECR dependency.

## Impact

- Affected specs: `container-deployment` (new requirement: Deployment
  Verification).
- Affected code: `scripts/deploy.sh`, new `scripts/deploy_verify.py`,
  `tests/test_deploy_scripts.py`, new `tests/test_deploy_verify.py`, new
  `tests/test_compose_config_resolution.py`, new
  `tests/fixtures/fake_mcp_server.py`,
  `tests/fixtures/deploy_sh_before_verifier.sh` (frozen pre-helper
  `deploy.sh`), `.github/workflows/ci.yml` (new `compose-agreement` job),
  `docs/deploy-vps.md`, `pyproject.toml` (basedpyright covers `scripts/`).
- Unaffected: image selection, persistent volumes, OpenD supervision,
  broker-login behavior, container isolation, the published endpoint.
- Verification still says nothing about broker login or trading readiness;
  that boundary is restated in the docs rather than moved.
