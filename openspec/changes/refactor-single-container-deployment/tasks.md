# Tasks: Run OpenD and the MCP Server in One Supervised Container

Checked items were implemented and verified as stated. Items left unchecked
carry the reason; several cannot be done from a machine without a Docker
daemon and are marked so rather than assumed.

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
     `UV_PYTHON_DOWNLOADS` is `automatic`, `UV_PYTHON=3.12`,
     `UV_PYTHON_PREFERENCE=only-managed` so Ubuntu's 3.10 is not picked up
     silently, and `UV_PYTHON_INSTALL_DIR=/opt/uv-python` so the service user can
     execute what the root-run build installed.
   - [x] 2.3 Single unprivileged user: uid 10001, `opend`, home `/home/opend`,
     owning `/opt/moomooOpenD`, the data directory and `/app`, with
     `LD_LIBRARY_PATH` preserved.
   - [x] 2.4 `CMD` is the supervisor; `EXPOSE 8000` only.
   - [x] 2.5 `Dockerfile.opend` deleted, along with its `.dockerignore` entry and
     its CI matrix entry.
   - [ ] 2.6 **Not done — needs a Docker daemon.** Confirm the built image
     actually runs OpenD: shared libraries resolve and the binary starts. This
     is the merge's real risk, it is not covered by CI (which stands the binary
     in with a stub), and it should be checked before 6.2.

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
   - [x] 4.1 `docker-compose.yml`: one service, `restart: unless-stopped`,
     `init: true`, the volume at its unchanged path, `127.0.0.1:8000:8000` only,
     `MOOMOO_OPEND_HOST=127.0.0.1`. No networks, no `depends_on`.
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
     the container itself was never replaced.
   - [x] 5.4 Also kills the MCP process and asserts the container is replaced
     and comes back serving.
   - [x] 5.5 Also probes from a second container on the same network: port 8000
     reachable (the control), 11111 refused.
   - [ ] 5.6 **Not done — needs a Docker daemon.** Run the rewritten smoke test.
     It has never been executed; CI is the first thing that will run it.

6. **Deploy path**
   - [x] 6.1 `scripts/deploy.sh` and `.github/workflows/ci.yml` build, tag and
     verify one image. The `moomoo-opend` ECR repository is left in place until
     the last two-container image stops being a rollback target, then deleted —
     recorded in `docs/deploy-vps.md`.
   - [ ] 6.2 **Not done — needs the real deployment.** Confirm an existing
     `opend-data` volume is read without a new device authorization, with the
     interactive re-login path ready. Do 2.6 first.
   - [ ] 6.3 **Not done — needs a Docker daemon.** Measure the stop time against
     the ~10s the server takes today, rather than assuming this changed it
     either way.

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
   - [x] 8.1 `uv run pytest` passes: 557 passed, 1 skipped.
   - [x] 8.2 `uv run ruff check` and `ruff format --check` are clean, and
     `basedpyright` reports 0 errors.
   - [ ] 8.3 **Not done — needs a Docker daemon.** `scripts/smoke-test.sh`. Same
     item as 5.6.
   - [ ] 8.4 **Not done — no CLI available.** `openspec validate
     refactor-single-container-deployment --strict --no-interactive`; the
     `openspec` binary is not installed here and is not on npm under that name.
     The delta was checked by hand: 4 requirements, 15 scenarios, every
     requirement carrying at least one.
