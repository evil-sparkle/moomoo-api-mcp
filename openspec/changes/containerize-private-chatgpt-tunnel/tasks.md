# Tasks

This revision is planning-only. Present it for review and wait for a new apply request before resuming code changes. Only task 1.1 is complete (1/36); no checkbox changes are made by this revision. PR #38 remains draft, the feature default-off and the production pin unchanged. No real credentials, real OpenAI traffic, VPS deployment, trading, merge/release or live acceptance is authorized.

The dependency table is authoritative; section order is organizational, not a blanket dependency on task 1.2. **Independent** means safe to implement after a new apply request using only synthetic credentials and disposable local/container fixtures. It does not mean released, production-enabled or security-gate-passed. Any test involving the actual official binary still records its own pass/fail/untested result in design.md's matrix. Keep failed gates failing; do not replace them with a stub, xfail, skipped test or an unrelated passing job.

| Task(s) | Prerequisites | Work classification and completion boundary |
| --- | --- | --- |
| 1.1 | None | COMPLETE: existing pin integrity/source audit only |
| 1.2 | 1.1; local protocol fixtures; 4.5 and 6.2 for completed rotation/forwarding evidence (fixture development can start earlier) | MANDATORY GATE: fixture development and synthetic real-binary runs can proceed; completion BLOCKED by CP1/CP2 and all untested CP/MCP cases |
| 1.3 | 1.1 | Independent image-input/writable-path experiment; no security approval or production pin change |
| 2.1, 2.2 | None beyond new apply authorization | Independent exact Host setting and URL/preflight implementation |
| 2.3 | 2.1, 2.2 | Independent bearer/Host/Origin/session component tests; cannot satisfy official-client gate by themselves |
| 2.4 | 2.3 | Independent mode/mutation-refusal tests with mocked broker; no trading enablement |
| 3.1 | 1.3 | Independent dedicated image build, known-failing official pin retained for test use |
| 3.2 | 3.1 | Independent container config validation against local fixtures only |
| 3.3 | 2.2, 3.1, 3.2 | Independent authenticated startup gate |
| 3.4, 3.5 | 3.3 | Independent signals, liveness and safe diagnostics |
| 4.1 | 3.1 | Independent rootful/rootless mapping probe in disposable resources |
| 4.2 | 4.1 | Independent synthetic master/staging permissions and atomic writes |
| 4.3 | 4.2 | Independent PTY/credential-helper tests |
| 4.4 | 3.2, 4.2, 4.3 | Independent actual-image permissions tests, no real secret sources |
| 4.5 | 4.4, 6.1; authenticated local control-plane/MCP fixtures | Independent real-client rotation tests; failures remain recorded and cannot be waived |
| 5.1 | 2.1, 3.1, 3.2 | Independent default-off Compose overlay |
| 5.2 | 5.1 | Independent default/enabled synthetic config matrix |
| 5.3 | 5.2 | Independent wrapper selection persistence, fixture-only deployment paths |
| 5.4 | 5.3, 2.4 | Independent simulated enable/disable/rollback and mode guard; production enablement remains blocked |
| 5.5 | 5.3, 3.1 | Independent local image identity/recreation tests |
| 6.1 | 3.1, 5.1 | Independent isolated harness with fixture-only egress and safe cleanup |
| 6.2 | 6.1, 2.3, 2.4, 3.3, 4.4; local control-plane protocol fixture | Independent actual-image/binary integration; capture normal/negative-path evidence without claiming overall gate pass |
| 6.3 | 6.1, 5.2 | Independent real supervisor plus OpenD stub network checks |
| 6.4 | 6.2, 6.3, 5.5 | Independent lifecycle/DNS recovery checks |
| 6.5 | 6.2, 3.4, 3.5 | Independent hardened-runtime delayed-start/failure/recovery checks |
| 6.6 | 6.3, 5.4 | Independent migration/rollback simulations with disposable state |
| 7.1 | 6.1; 1.2 fixture interface | Independent CI integration; mandatory security job must remain failing/incomplete until gate actually passes |
| 7.2 | 6.1 | Independent existing smoke isolation improvements |
| 7.3, 7.4, 7.5 | Revised design; reconcile with 3–6 before completion | Independent documentation; retain blocker prominently and describe enablement as future, unauthorized procedure |
| 8.1 | Independent code/tests in 1.3 and groups 2–7 available | Independent broad quality verification; report gate failures separately even if other checks pass |
| 8.2 | Observations from 1.2 and 8.1 | Independent evidence report; may accurately be complete with failures/pending rows, but never clears them |
| 8.3 | 1.2 PASS; all other implementation tasks; design G1–G4 | RELEASE HANDOFF BLOCKED: maintain existing PR #38 as draft until all evidence and explicit review/authorization requirements are met |

Task 1.2 need not pass before any Independent row. Its unresolved result prevents task 8.3 and release/production enablement. Independent tests may use unmodified v0.0.14 with synthetic secrets to characterize behavior, never with a live endpoint. Runtime-key and ordinary-MCP-bearer observations are separate; the preserved historical reproduction proves only the runtime-key cases stated in design.md. Any future change of official release requires separate approval and a full rerun on the final artifact.

## 1. Establish pinned-client compatibility evidence

- [x] 1.1 Verify the existing v0.0.14 archive with `deploy/tunnel-client/install.py` and inspect its pinned configuration/source for runtime paths, health endpoints, signal behavior and static-header scope; deliver an evidence note with version/digest and exact supported settings, without unrelated upgrades.
- [ ] 1.2 Complete the mandatory real official-client matrix in design.md (CP1–CP9, MD1–MD2, MF1–MF2, MA1, MP1, MR1): use distinct synthetic OpenAI runtime key and ordinary MCP bearer values, normal authenticated control-plane/MCP operation, negative auth, redirect/proxy sinks and rotation. Record every case as PASS, FAIL or UNTESTED with exact artifact provenance. Fixture work can proceed independently; this checkbox stays unchecked while any required case fails or is untested. Preserve the reproduction; review the explicit control-plane redirect policy and destination-scoped credential-injection remediation candidate without shipping a patched client/proxy, changing the production pin or posting upstream. Closure requires reviewed official provenance and passing evidence, not merely proposing an upgrade.
- [ ] 1.3 Select and record immutable slim runtime/build image digests and the minimal writable paths; verify the real binary executes and config parses under non-root read-only conditions before finalizing image settings.

## 2. Add narrow transport compatibility

- [ ] 2.1 Add validated `MCP_ALLOW_CHATGPT_TUNNEL_HOST` handling in settings/server construction; test absent/blank/0/1/invalid values and that only exact `moomoo-mcp:8000` is additionally accepted with opt-in.
- [ ] 2.2 Extend `private_chatgpt_preflight.py` with explicit exact Compose URL opt-in, no-proxy transport, refused redirects, bounded response handling and classified safe errors; test localhost regressions, wrong ports/aliases/userinfo/query/fragment/private addresses, redirect classes and proxy traps.
- [ ] 2.3 Verify initialize, discovery and calls with ordinary bearer; preserve missing/wrong/conflicting-header rejection, unexpected Host/Origin denial, stateless behavior and all tool annotations using ASGI/protocol tests.
- [ ] 2.4 Retain READ_ONLY mutation-dispatch coverage for placement/modification/cancellation/unlock/operator recovery and prove SIMULATE/REAL startup is refused, including zero broker-write counters; do not modify trading policy to satisfy integration tests.

## 3. Build the independent container runtime

- [ ] 3.1 Add the dedicated Dockerfile and allowlisted build-context preparation, reusing release manifest/integrity installer; verify checksum failure precedes extraction/execution and inspect final image contents for absence of broker components and synthetic secret canaries.
- [ ] 3.2 Add container-only configuration using exact Docker DNS URL, file-backed runtime/discovery headers and loopback health/admin binding; verify parsing and safe doctor diagnostics with the pinned binary and synthetic files.
- [ ] 3.3 Add minimal PID 1 startup gating with a 90-second deadline, bounded transient retries and immediate fatal-error refusal; test delayed MCP, exhausted deadline, invalid auth/mode/results, and that no client polling starts before READ_ONLY is proven.
- [ ] 3.4 Implement child reaping, signal forwarding, bounded shutdown and liveness-triggered nonzero exit; test real child exit, SIGTERM/SIGINT, SIGSTOP/hang, and independent recovery without treating remote readiness failure alone as process death.
- [ ] 3.5 Implement separate safe diagnostics for liveness, readiness and authenticated MCP availability, with a minimal scrubbed child environment; test proxy/config override removal and no credentials, Authorization headers, account results or raw bodies in output.

## 4. Provision container-readable secrets safely

- [ ] 4.1 Add a no-secret identity-mapping probe and validation for the selected rootful/rootless Docker context; verify observed marker ownership against effective UID/GID maps and reject ambiguous, root or conflicting host identities.
- [ ] 4.2 Narrowly extend the credential helper with atomic staging to fixed names, mapped owner 0400 and root-owned protected parents with minimal traversal ACLs; test symlink/unsafe-parent rejection, fsync/replace behavior and cleanup on failure while preserving root-only 0600 masters.
- [ ] 4.3 Preserve no-echo terminal behavior and restoration on success, exceptions and interruption using PTY tests; verify provisioning never puts secret values in arguments, Compose interpolation, tracked files or diagnostic output.
- [ ] 4.4 Run actual-image mounted-file permission tests under rootful and rootless Docker: UID 10002 reads config/staged secrets, unrelated and brokerage identities cannot read sources, and runtime cannot read masters or write mounts; fail tests rather than adding world-readability or a root runtime.
- [ ] 4.5 Add end-to-end atomic rotation tests for both runtime key and ordinary bearer, including stale bind behavior on restart and forced recreation; simulated control plane and MCP must accept only new values, and evidence must show successful new authenticated traffic plus old-value rejection without printing either value.

## 5. Integrate explicit Compose selection and deployment

- [ ] 5.1 Add `docker-compose.chatgpt.yml` with separate service, user-defined outbound-capable bridge, numeric user, read-only root, dropped capabilities, no-new-privileges, minimal tmpfs/mounts and independent restart policy; verify rendered synthetic configs have no shared namespaces, extra ports, Docker socket or brokerage/journal mounts.
- [ ] 5.2 Preserve existing base/production/paper/smoke assertions and add an overlay matrix; prove default deployment has one brokerage service and needs no tunnel settings/secrets while enabled deployment adds exactly one tunnel service without changing OpenD binding or host publication.
- [ ] 5.3 Extend deployment/wrapper selection explicitly and persist non-secret selection plus optional immutable image identity; test consistent context/project/files in start and verification, default-off behavior, and no orphan removal of the enabled tunnel.
- [ ] 5.4 Add scoped enable/disable and rollback behavior, including refusal of non-READ_ONLY mode with the overlay selected and refusal of an unsupported older target until tunnel disablement; verify state restoration and targeted recreation retain OpenD/journal volume identities and existing local-client behavior.
- [ ] 5.5 Test optional-image build/enable/update and reuse of its recorded immutable identity on restart/recreation; prove no runtime download, implicit upgrade, tunnel setup requirement for default deploy, or accidental inclusion of secret files in the build context.

## 6. Exercise disposable runtime integration

- [ ] 6.1 Add `scripts/test-tunnel-container.sh` with unique project names, synthetic inputs, scrubbed environment, random available loopback host ports and project-scoped cleanup; enforce fixture-only endpoints/egress including accidental fallback; run alongside a dummy occupied port 8000 and prove no unrelated container or volume is touched.
- [ ] 6.2 Run the actual tunnel image/entrypoint and pinned official binary against the simulated control plane and authenticated real MCP endpoint; verify Docker DNS forwarding, runtime permissions, READ_ONLY gate, Host/Origin/auth failures and no tunnel-published ports.
- [ ] 6.3 With the actual brokerage supervisor and loopback OpenD stub, prove tunnel-origin MCP:8000 succeeds while OpenD:11111 fails, namespace isolation holds and both services retain outbound connectivity; record stub limitations rather than claiming broker login.
- [ ] 6.4 Stop/restart/recreate the tunnel while continuously probing host-local MCP, then recreate MCP with a deliberately changed disposable IP; prove local independence and DNS recovery without session reuse or request replay.
- [ ] 6.5 Exercise delayed startup, control-plane outage, readiness recovery, hung-client restart and bounded termination under actual image hardening; distinguish liveness, readiness, MCP reachability and broker-backed availability in the evidence matrix.
- [ ] 6.6 Exercise disablement, migration sequencing and legacy rollback with disposable state markers and simulated systemd lifecycle assertions; verify mutually exclusive tunnel mechanisms, persistent OpenD/journal identities and authenticated local access without Compose down or volume deletion.

## 7. Integrate CI and operator documentation

- [ ] 7.1 Update CI path triggers and required jobs for optional image/config/helper/overlay inputs, real-client container integration and rootful/rootless permissions; verify fixture-only egress and disposable resources, and separately report component/container results versus the mandatory official-client gate. Prove a failed, skipped or untested gate cannot be overridden by unrelated green jobs; label simulated versus live acceptance.
- [ ] 7.2 Remove fixed project/host-port assumptions from reused smoke harness paths and their CI image references as needed; verify existing brokerage/paper tests still run without taking port 8000 from a developer stack.
- [ ] 7.3 Rewrite the private tunnel runbook with Compose-first installation, exact opt-ins, daemon-host staging/mapping, in-container diagnostics, forced-recreation rotation and tunnel-only disable commands; retain labelled legacy systemd migration/rollback instructions and verify command sequencing with synthetic fixtures.
- [ ] 7.4 Update README, deployment/rootless/restart docs and OpenSpec context to distinguish one two-process brokerage container from one optional tunnel container; review that no previous security assertion or acceptance limitation is silently removed.
- [ ] 7.5 Document required MCP recreation, preserved project/volumes, stop-and-disable legacy-before-Compose sequence and reverse rollback sequence; explicitly state ordinary bearer mode dependence and stop tunnel before SIMULATE/REAL.

## 8. Independent verification and blocked release handoff

- [ ] 8.1 Run Ruff lint/format, basedpyright, relevant/full pytest, disposable brokerage/tunnel/paper checks and `openspec validate --all --strict --no-interactive`; record exact results and environment limitations without claiming unrun checks passed.
- [ ] 8.2 Produce a verification matrix linking every requested security/network/rotation/recovery behavior to automated evidence; separately record VPS, live OpenAI, full account/positions, ChatGPT web and native iPad milestones as PENDING unless genuinely tested with owner authorization.
- [ ] 8.3 Keep the already-open separate implementation PR #38 in draft while any required gate case is failing/untested or owner authorization is absent. Only after task 1.2 and the remaining final official-client/container tests pass, record reviewed source/release integrity, exact final image provenance and G1–G4 closure; obtain explicit authorization before any ready-for-review transition or release action. Verify no known-failing pin is enabled in production. Do not create a substitute PR to bypass the gate, merge, deploy, enable trading, reopen PR #36, edit its archive or mark live acceptance complete.
