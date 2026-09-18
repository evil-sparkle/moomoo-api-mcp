# Tasks: Run OpenD and the MCP Server in One Supervised Container

1. **Write the supervisor, tests first**
   - [ ] 1.1 Add `tests/test_supervisor.py` covering the policy in `design.md`
     against fake child processes, not the real binaries: OpenD exit restarts
     OpenD alone; MCP keeps running across it; repeated OpenD failure within the
     window stops MCP and exits non-zero; MCP exit stops OpenD and exits
     non-zero; SIGTERM reaches both and the wait is bounded; a child that
     ignores SIGTERM is escalated to SIGKILL; orphaned grandchildren are reaped.
   - [ ] 1.2 Implement `src/moomoo_mcp/supervisor.py` with a
     `moomoo-api-mcp-supervisor` console script entry point in `pyproject.toml`.
   - [ ] 1.3 Assert the supervisor never restarts on a health signal — it reads
     process exits, not `check_health`.

2. **Merge the two images into one**
   - [ ] 2.1 New `Dockerfile` on `ubuntu:22.04`. Move the OpenD download,
     `OPEND_*` build args, SHA256 verification and extraction across from
     `Dockerfile.opend` **verbatim** — same version, same URL, same fallback,
     same hash.
   - [ ] 2.2 Install uv and resolve the project with a pinned downloaded
     interpreter; flip `UV_PYTHON_DOWNLOADS` from `never` to `automatic` and
     record the pinned version in the Dockerfile.
   - [ ] 2.3 Single unprivileged user: uid 10001, name `opend`, home
     `/home/opend`, owning `/opt/moomooOpenD`, `/home/opend/.com.moomoo.OpenD`
     and the installed application. Keep `LD_LIBRARY_PATH=/opt/moomooOpenD`.
   - [ ] 2.4 `CMD` is the supervisor. `EXPOSE 8000` only.
   - [ ] 2.5 Delete `Dockerfile.opend` once nothing references it.
   - [ ] 2.6 Confirm the built image runs OpenD at all: `LD_LIBRARY_PATH` set,
     shared libraries resolve, binary starts. This is the merge's real risk —
     OpenD is an Ubuntu 18.04 build.

3. **Move the OpenD launch logic into the supervisor**
   - [ ] 3.1 Port the login-mode selection currently inlined in the compose
     entrypoint — interactive, password-MD5, remembered-token, and both loud
     failure paths — into Python, with tests for each branch's argv. It is
     shell today because compose had nowhere else to put it.
   - [ ] 3.2 Drop `-api_ip` and `OPEND_API_IP`: loopback is OpenD's default and
     the flag was never verified against a real build.
   - [ ] 3.3 Keep `OPEND_INTERACTIVE=1` working for the one-time device login,
     including through `docker compose run --rm -it`.

4. **Collapse the compose files**
   - [ ] 4.1 `docker-compose.yml`: one service, `restart: unless-stopped`,
     `init: true`, the `opend-data` volume at the unchanged path, ports
     `127.0.0.1:8000:8000` only, `MOOMOO_OPEND_HOST=127.0.0.1`. Remove
     `trading-net` and `depends_on`.
   - [ ] 4.2 `docker-compose.prod.yml`: one image reference.
   - [ ] 4.3 `docker-compose.smoke.yml`: keep swapping the OpenD binary for the
     stand-in, now by pointing the supervisor at it — and nothing else.

5. **Rewrite the topology guards**
   - [ ] 5.1 `tests/test_compose_topology.py`: keep every assertion that still
     has meaning (11111 published nowhere, 8000 on host loopback only, overlays
     do not move the topology) and replace the bridge assertions with
     single-service ones. Preserve the docstrings explaining the original
     outage; that history is why the file exists.
   - [ ] 5.2 Assert `MOOMOO_OPEND_HOST` is `127.0.0.1` and that no service
     declares a network that would expose 11111.
   - [ ] 5.3 `scripts/smoke-test.sh`: same shape, one container. Open a real MCP
     session first (the lifespan runs per request), then kill the OpenD process
     inside the container and assert the client's session survives and health
     recovers — this replaces `docker compose restart opend` and is the
     assertion that proves the asymmetric policy works.
   - [ ] 5.4 Add a smoke assertion that killing the MCP process exits the
     container and Docker brings the whole unit back.
   - [ ] 5.5 Verify from a second container on a user-defined network that
     11111 is unreachable.

6. **Deploy path**
   - [ ] 6.1 `scripts/deploy.sh` and `.github/workflows/ci.yml`: one image build,
     one ECR repository, one tag. Decide and record what happens to the now-unused
     `moomoo-opend` repository.
   - [ ] 6.2 Confirm on a real deployment that an existing `opend-data` volume is
     read without a new device authorization. Do this before decommissioning the
     two-container stack, and have the interactive re-login path ready.
   - [ ] 6.3 Measure the stop time. `moomoo-mcp` takes ~10s to stop in CI today
     for unexplained reasons; record whether the supervisor changes that rather
     than assuming either way.

7. **Documentation**
   - [ ] 7.1 `docs/state-and-restarts.md`: redraw the topology, rewrite "what each
     restart costs" for one container, and state the new cost plainly — an MCP
     image update now also restarts OpenD, so every deploy pays a ~30s broker
     re-login.
   - [ ] 7.2 `docs/deploy-vps.md` and `README.md`: new commands for logs,
     restart and the one-time interactive login.
   - [ ] 7.3 `.env.example`: remove `OPEND_API_IP`, update `MOOMOO_OPEND_HOST`.
   - [ ] 7.4 Update the known-gaps list: `-api_ip` is retired rather than still
     unverified; the gateway-restart assertion now covers a process, not a
     container.

8. **Verify**
   - [ ] 8.1 `uv run pytest` passes.
   - [ ] 8.2 `uv run ruff check` and `ruff format --check` add no new findings
     (the repository has pre-existing ones; diff against a clean worktree).
   - [ ] 8.3 `scripts/smoke-test.sh` passes locally and in CI.
   - [ ] 8.4 `openspec validate refactor-single-container-deployment --strict
     --no-interactive` passes.
