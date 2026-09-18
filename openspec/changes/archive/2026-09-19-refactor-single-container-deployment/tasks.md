# Tasks: Run OpenD and the MCP Server in One Supervised Container

Checked items were implemented and verified as stated. Items left unchecked
carry the reason; several cannot be done from a machine without a Docker
daemon and are marked so rather than assumed.

**Status:** implemented on `main` (PR #9, merged 2026-09-18) and verified on
the real deployment on 2026-09-18 (2.6, 6.2, 6.3 below). CI builds the image and
passes the smoke test, which runs a stub in place of OpenD; the live checks are
what cover the real binary. Every task is done, so this change is ready to
archive. Archive it before `update-container-restart-resilience`, whose
gateway-restart requirement defers to `Paired Process Supervision` below.

1. **Write the supervisor, tests first**
   - [x] 1.1 `tests/test_supervisor.py` covers the policy in `design.md` against
     fake child processes, not the real binaries: OpenD exit restarts OpenD
     alone; MCP keeps running across it; repeated OpenD failure within the
     window stops MCP and exits non-zero; MCP exit stops OpenD and exits
     non-zero; a stop request reaches both and the wait is bounded; a child that
     ignores SIGTERM is escalated to SIGKILL; an unknown pid is reaped without
     ending the run. (25 tests.)
   - [x] 1.2 `src/moomoo_mcp/supervisor.py`, with a `moomoo-api-mcp-supervisor`
     console script in `pyproject.toml`.
   - [x] 1.3 The supervisor never restarts on a health signal: it reads process
     exits only, and a test asserts it imports no application code at all, so
     there is no route by which a probe could grow into the policy.

2. **Merge the two images into one**
   - [x] 2.1 `Dockerfile` on `ubuntu:22.04`, with the OpenD download, `OPEND_*`
     build args, SHA256 verification and extraction moved across verbatim.
   - [x] 2.2 uv resolves the project against a downloaded interpreter;
     `UV_PYTHON_DOWNLOADS` is `automatic`, `UV_PYTHON=3.12.13` (the exact patch
     CI resolved, so the same commit does not build a different interpreter
     later), the base image is pinned by digest behind an overridable build arg,
     `UV_PYTHON_PREFERENCE=only-managed` so Ubuntu's 3.10 is not picked up
     silently, and `UV_PYTHON_INSTALL_DIR=/opt/uv-python` so the service user can
     execute what the root-run build installed.
   - [x] 2.3 Single unprivileged user: uid 10001, `opend`, home `/home/opend`,
     owning `/opt/moomooOpenD`, the data directory and `/app`, with
     `LD_LIBRARY_PATH` preserved.
   - [x] 2.4 `CMD` is the supervisor; `EXPOSE 8000` only.
   - [x] 2.5 `Dockerfile.opend` deleted, along with its `.dockerignore` entry and
     its CI matrix entry.
   - [x] 2.6 Verified on the VPS, 2026-09-18, with the CI-published images
     for `6d0fa91` and `9c29f6b` (identical image inputs). The real
     moomoo OpenD 10.10.7008 starts under the supervisor, logs in, reports
     `API Listening Address: 127.0.0.1:11111`, and serves both halves:
     `check_health` returns `connected` with quote and trade `ok`, and
     `get_accounts` succeeds. `API RSA Enabled: No` with a working trade call
     confirms trade over loopback needs no protocol encryption, which the
     cross-container arrangement did.

3. **Move the OpenD launch logic into the supervisor**
   - [x] 3.1 Every login branch from the compose entrypoint — interactive,
     password-MD5, remembered-token, and both loud failure paths — is Python
     with a test per branch's argv.
   - [x] 3.2 `OPEND_API_IP` retired. **Deviation:** the flag itself is kept,
     pinned to `-api_ip=127.0.0.1`, rather than dropped. Dropping it would rely
     on OpenD's default being loopback, which is exactly as unverified here as
     the flag; pinning it lands on loopback either way. See `design.md`.
   - [x] 3.3 `OPEND_INTERACTIVE=1` still works, including through
     `docker compose run --rm -it`, which is why the children stay in the
     supervisor's process group (a background group reading a tty stops on
     SIGTTIN).

4. **Collapse the compose files**
   - [x] 4.1 `docker-compose.yml`: one service, `restart: unless-stopped`, the
     volume at its unchanged path, `127.0.0.1:8000:8000` only,
     `MOOMOO_OPEND_HOST=127.0.0.1`. No networks, no `depends_on`, and
     deliberately no `init: true` — see 9.2.
   - [x] 4.2 `docker-compose.prod.yml`: one image reference.
   - [x] 4.3 `docker-compose.smoke.yml` points `OPEND_BINARY` at a stub and
     changes nothing else. The stub binds whatever `-api_ip` it is handed, so a
     supervisor that widened the listener fails the smoke test.

5. **Rewrite the topology guards**
   - [x] 5.1 `tests/test_compose_topology.py` keeps every assertion that still
     has meaning and replaces the bridge ones with single-service checks; the
     docstrings explaining both the original outage and the exposure are kept.
     Runs against real `docker compose config` (no daemon needed): 12 tests.
   - [x] 5.2 Asserts `MOOMOO_OPEND_HOST=127.0.0.1`, that 11111 is published
     nowhere, and that `OPEND_API_IP` is not reintroduced as a setting.
   - [x] 5.3 `scripts/smoke-test.sh` rewritten: opens a real MCP session first,
     then kills the gateway *process* inside the container and asserts the
     session survives, the endpoint keeps answering, the gateway comes back and
     the container itself was never restarted.
   - [x] 5.4 Also kills the MCP process and asserts the container restarts
     (a new `StartedAt`; the same container id passes) and comes back serving.
   - [x] 5.5 Also probes from a second container on the same network: port 8000
     reachable (the control), 11111 refused.
   - [x] 5.6 Run the rewritten smoke test. Not runnable locally (no Docker
     daemon); CI runs it. **Passing** as of 6e1d97f, having taken four runs and
     found four real bugs on the way (9.1, 9.7, 9.8, 9.9). Every assertion now
     executes, including the two that had never run before that commit: the
     container restarting when the server dies, and a client calling
     successfully afterwards.
   - [x] 5.7 Assert credentials never reach the container log: the overlay hands
     the gateway a fake PIN hash and the smoke test fails if it appears in
     `docker logs`.

6. **Deploy path**
   - [x] 6.1 `scripts/deploy.sh` and `.github/workflows/ci.yml` build, tag and
     verify one image. The `moomoo-opend` ECR repository is left in place until
     the last two-container image stops being a rollback target, then deleted —
     recorded in `docs/deploy-vps.md`.
   - [x] 6.2 Verified on the VPS, 2026-09-18. The `opend-data` volume was
     created on 2026-09-11 by the two-container stack; the first
     single-container start read it and logged in with the remembered password,
     no device-verification prompt. The same held for every later start: a
     recreation from a new image (`deploy.sh` to `9c29f6b`), a `compose
     restart`, and three `stop`/`up` cycles. The interactive path was not
     needed.
   - [x] 6.3 Measured on the VPS, 2026-09-18, with OpenD logged in: `compose
     stop` took 0.82s, 0.93s and 1.06s, exit code 0 each time. The supervisor
     signalled both children within 5ms of SIGTERM and none needed SIGKILL, so
     the 10s supervisor bound and Docker's 10s grace period were never
     approached. The ~10s stop the two-container server showed in CI does not
     reproduce here; its cause is still unexplained (`docs/state-and-restarts.md`
     § Known gaps). Measured, not assumed.

7. **Documentation**
   - [x] 7.1 `docs/state-and-restarts.md`: topology redrawn, the state table and
     "what each restart costs" rewritten for one container, and the new cost
     stated plainly — every deploy now pays OpenD's ~30s re-login.
   - [x] 7.2 `docs/deploy-vps.md` and `README.md`: new commands for logs,
     restart and the one-time interactive login, plus an upgrade note for
     deployments coming from the two-container stack.
   - [x] 7.3 `.env.example`: `OPEND_API_IP` removed with a note on why it is not
     a setting any more; the supervision knobs documented.
   - [x] 7.4 Known gaps updated: `-api_ip` now fails safe rather than being
     load-bearing, and the merged image never having run the real OpenD is
     recorded as the open risk it is.

8. **Verify**
   - [x] 8.1 `uv run pytest` passes: 575 passed, 1 skipped.
   - [x] 8.2 `uv run ruff check` and `ruff format --check` are clean, and
     `basedpyright` reports 0 errors.
   - [x] 8.3 `scripts/smoke-test.sh` passes in CI (not runnable locally — no
     Docker daemon). Same item as 5.6.
   - [x] 8.4 `openspec validate --all --strict --no-interactive` passes: 24
     passed, 0 failed. The CLI is the npm package `@fission-ai/openspec` (the
     unscoped `openspec` name is not it), pinned at 1.13.1 and run by CI's
     `openspec` job. The first real run failed this change: the MODIFIED
     `Isolated OpenD Gateway Network` block had renamed the canonical scenario
     `MCP server reaches OpenD internally`, and archive refuses to drop one. The
     title is kept and its body rewritten for loopback.

9. **From CI and review** (found after the first push)
   - [x] 9.1 **CI, container smoke test.** A missing gateway login was fatal:
     `main` exited, Docker restarted the container, and it crash-looped without
     ever serving the MCP endpoint. That also broke an existing `system-health`
     requirement — MCP must remain available for health requests after a
     downstream startup failure. An unstartable gateway is now logged and the
     server runs without one. The smoke overlay supplies a fake login so the
     gateway path is still the one under test.
   - [x] 9.2 **Review: `init: true` contradicted the PID-1 design.** docker-init
     would have taken PID 1 and made the supervisor PID 2, so its signal
     handling and orphan reaping described a job it did not hold. Removed, so
     the supervisor is genuinely PID 1; `tests/test_compose_topology.py` asserts
     nothing is inserted in front of it, and the spec now says so explicitly.
   - [x] 9.3 **Review: the supervisor logged the trade PIN hash.** `_spawn`
     logged the whole OpenD command line, which carries `-login_pwd_md5` and
     `-login_account`. Both are redacted now, with a unit test and a smoke-test
     tripwire.
   - [x] 9.4 **Review: "costs a client nothing" was too strong.** Reworded
     everywhere to the claim that actually holds — no reconnection or
     re-initialization is required, and calls needing the gateway fail until it
     returns. The spec scenario says the same.
   - [x] 9.5 **Review: tunables were unvalidated.** `nan`, `inf`, negatives,
     zero and typo'd values now refuse to start and name the setting instead of
     silently falling back. Note the limit honestly: this validates *values*, so
     a misspelled variable name (`OPEND_MAX_RESTARST=10`) is still simply unseen
     — nothing here can detect that.
   - [x] 9.6 **Review: interpreter reproducibility.** Pinned to 3.12.13 and the
     base image to its digest. The apt layer is still resolved at build time, so
     this is base reproducibility, not a hermetic image.
   - [x] 9.7 **CI, second run: the container served zero tools.** The supervisor
     launched the server as `python -m moomoo_mcp.server`, which loads server.py
     a second time as `__main__` with its own FastMCP instance, while the tool
     modules it imports at the bottom register against the instance under the
     real module name. The served one had none. Nothing looked wrong — endpoint
     up, sessions opening, `tools/list` returning `[]` — and no unit test would
     have seen it. It runs the console script now, as the image it replaces did.
     Verified locally by launching the resolved argv and counting what the
     server advertises: 0 tools before, 32 after.
   - [x] 9.8 **CI, second run: the gateway stub interleaved its output.** The
     stand-in printed from per-connection threads without a lock, so CI logged
     `CONNECT 127.0.0.1CONNECT` / ` 127.0.0.1`. That corrupts the smoke test's
     line counting, which would have undercounted reconnections and passed for
     the wrong reason. One lock, one write; verified locally with 40 concurrent
     connections producing 40 whole lines.
   - [x] 9.9 **CI, third run: the MCP-server kill matched nothing.** Moving the
     server onto its console script (9.7) changed its `/proc` cmdline, and the
     smoke test still hunted for `moomoo_mcp.server` — so nothing was signalled,
     the container never went down, and a `|| true` turned that into a 120s
     mystery timeout two assertions later. Finding and killing are separate
     steps now: a pattern that matches nothing fails immediately and says so.
     The pattern is `moomoo-api-mcp` excluding `supervisor`, because the former
     is a substring of `moomoo-api-mcp-supervisor`. Verified against a fake
     `/proc` carrying the container's real cmdlines.
     Everything before this point passed, including the whole gateway half of
     the policy: the process killed, restarted in place, the client's session
     surviving, and the container untouched.
