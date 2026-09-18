# Tasks: Resolve Deploy Verification Configuration Through Compose

All items are implemented on this branch and verified as stated. The
real-Compose container agreement was first exercised against a live local
daemon (which is how the `$$` escaping was found) and is now owned by the
mandatory `compose-agreement` CI job, which never skips.

1. **Add the helper**
   - [x] 1.1 `scripts/deploy_verify.py`: `check-config` and `verify`
     operations; standard library only; host Python 3.10+.
   - [x] 1.2 Resolution: `compose-prod.sh config --format json`, read
     `services.moomoo-mcp.environment.MCP_AUTH_TOKEN`; nonempty used exactly
     as resolved; explicit empty means no authentication; anything else a
     configuration error, never a fallback to reading env files; Compose's
     `$$` output escaping decoded; header-unsafe values refused.
   - [x] 1.3 Probe: authenticated MCP `initialize` through curl, token on
     stdin (`--header @-`), `.curlrc` disabled, no redirects, no proxies;
     structured validation of a JSON or SSE-framed JSON-RPC initialize
     result with the matching string id; elapsed-time deadline with
     per-attempt `--max-time`; `0` = one attempt; signal handling.
   - [x] 1.4 `tests/test_deploy_verify.py`: resolution cases, HTTP
     classification, JSON/SSE validation, deadline behavior, secret
     handling, command-line behavior — against real curl and a scripted
     local server, not a stubbed transport.

2. **Wire deployment to the helper, delete the parser**
   - [x] 2.1 `scripts/deploy.sh`: check-config after checkout and
     `.deploy.env` rewrite; pull; up; verify; `--prepare` validates
     configuration and pulls without starting or probing.
   - [x] 2.2 Deleted: `parse_env_token`, the `MCP_AUTH_TOKEN` precedence
     loop, `initialize_result_ok`, the Bash probe loop, the 0600 header file
     and its cleanup trap.
   - [x] 2.3 `python3` >= 3.10 checked with the other host tools, before any
     state changes.
   - [x] 2.4 Helper invoked from the target commit's checkout, not the
     reexecuted script's directory; log collection failures cannot prevent
     rollback.
   - [x] 2.5 `tests/test_deploy_scripts.py`: configuration failure before
     startup restores state without restart; failed verification keeps the
     restart rollback; `--prepare`; too-old Python; hand-over from
     `tests/fixtures/deploy_sh_before_verifier.sh` (main's pre-helper
     script) and from a stale-helper checkout; empty resolved token probes
     unauthenticated.

3. **Real-Compose coverage**
   - [x] 3.1 `tests/test_compose_config_resolution.py`, config layer: the
     service environment is read rather than an identically named
     interpolation variable; later env file wins; exported variables win,
     including an explicitly empty export; what Compose refuses is a
     configuration error; the resolved configuration is never printed.
   - [x] 3.2 Deploy layer: the probe sends the target commit's token mapping,
     not the old checkout's, with an old `.deploy.env` present.
   - [x] 3.3 Container agreement: a disposable stand-in
     (`tests/fixtures/fake_mcp_server.py`, dummy credentials, no OpenD,
     broker or ECR) started by real Compose for each env-file case; the
     container's actual `MCP_AUTH_TOKEN` compared with the Authorization
     header the verifier sent. No expected values written down — whatever
     Compose resolves is, by definition, right. This is what found the `$$`
     escaping.
   - [x] 3.4 Mandatory `compose-agreement` CI job: never skipped in CI (a
     missing prerequisite is a failure there), records the Compose version,
     runs the resolution and agreement tests on every non-docs change.
   - [x] 3.5 `pyproject.toml`: basedpyright covers `scripts/`.

4. **Docs and spec**
   - [x] 4.1 `docs/deploy-vps.md`: Compose resolves deployment configuration,
     the verifier reads the resolved MCP service environment and never parses
     dotenv files; host Python dependency; stdin header transport; error
     categories; verified = accepted initialize, not broker login.
   - [x] 4.2 `openspec/changes/refactor-deploy-compose-verification/`: this
     proposal, design, tasks, and the `container-deployment` delta adding the
     Deployment Verification requirement.
