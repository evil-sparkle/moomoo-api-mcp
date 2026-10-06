# Tasks

Current revision — 2026-10-07: the owner authorizes requirements and implementation changes for the managed official-client approach. Historical direct-client FAILs remain recorded; their repair and fork maintenance are outside scope. All 38 revised tasks are complete, with hosted evidence for implementation commit `c41345b`. Dated checkpoints below record earlier scope/progress; live milestones remain pending.

Use synthetic credentials and disposable fixtures. Preserve the official pin and historical result files. This work does not deploy, read real credentials, contact live OpenAI, enable trading, merge or promote the draft PR. Design G1–G4 define current acceptance and evidence handoff.

| Task(s) | Prerequisites | Work classification and completion boundary |
| --- | --- | --- |
| 1.1 | None | COMPLETE: existing pin integrity/source audit only |
| 1.2 | 1.1, 4.5, 6.2 | Managed official-client normal/negative/auth/rotation compatibility; direct-client limitations remain characterization |
| 1.3 | 1.1 | Independent image-input/writable-path experiment; no security approval or production pin change |
| 2.1, 2.2 | None beyond new apply authorization | Independent exact Host setting and URL/preflight implementation |
| 2.3 | 2.1, 2.2 | Independent bearer/Host/Origin/session component tests; cannot satisfy official-client gate by themselves |
| 2.4 | 2.3 | Independent mode/mutation-refusal tests with mocked broker; no trading enablement |
| 3.1 | 1.3 | Independent dedicated image build, unchanged verified official pin |
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
| 5.4 | 5.3, 2.4 | Independent simulated enable/disable/rollback and mode guard; operational startup checks remain required |
| 5.5 | 5.3, 3.1 | Independent local image identity/recreation tests |
| 6.1 | 3.1, 5.1 | Independent isolated harness with fixture-only egress and safe cleanup |
| 6.2 | 6.1, 2.3, 2.4, 3.3, 4.4; local control-plane protocol fixture | Independent actual-image/binary integration; capture normal/negative-path evidence without claiming direct-client or live acceptance |
| 6.3 | 6.1, 5.2 | Independent real supervisor plus OpenD stub network checks |
| 6.4 | 6.2, 6.3, 5.5 | Independent lifecycle/DNS recovery checks |
| 6.5 | 6.2, 3.4, 3.5 | Independent hardened-runtime delayed-start/failure/recovery checks |
| 6.6 | 6.3, 5.4 | Independent migration/rollback simulations with disposable state |
| 7.1 | 6.1; 1.2 fixture interface | Independent CI integration; managed checks remain blocking; characterization retains genuine verdicts |
| 7.2 | 6.1 | Independent existing smoke isolation improvements |
| 7.3, 7.4, 7.5 | Revised design; reconcile with 3–6 before completion | Independent documentation; document accepted trust assumptions and operator enablement; keep live milestones pending |
| 8.1 | Independent code/tests in 1.3 and groups 2–7 available | Independent broad quality verification; report gate failures separately even if other checks pass |
| 8.2 | Observations from 1.2 and 8.1 | Independent evidence report; may accurately be complete with failures/pending rows, but never clears them |
| 8.3 | 1.2; revised implementation tasks; G1–G4 | Update existing draft PR with actual evidence; hosted/live milestones remain independent |
| 9.1 | 5.3, 5.4; revised managed scope | Operational selection/legacy/auth/mode checks and disposable wrapper regressions |
| 9.2 | 3.1, 6.2; revised managed scope | Managed-profile image metadata and unchanged official-runtime verification |

Managed acceptance and direct-client diagnostic findings have distinct status. Preserve historical failures; this revision does not claim an upstream repair. Future upgrades remain separately reviewed.

Current verification: [managed-verification-20261007.md](managed-verification-20261007.md).

## 1. Establish pinned-client compatibility evidence

- [x] 1.1 Verify the existing v0.0.14 archive with `deploy/tunnel-client/install.py` and inspect its pinned configuration/source for runtime paths, health endpoints, signal behavior and static-header scope; deliver an evidence note with version/digest and exact supported settings, without unrelated upgrades.
- [x] 1.2 Review unchanged official-client provenance and fixed deployment assumptions; verify normal authenticated control-plane/MCP operation, negative authentication, manager proxy filtering and credential rotation through the actual image. Document accepted redirect/proxy/doctor limitations separately without changing historical verdicts or maintaining a fork.
- [x] 1.3 Select and record immutable slim runtime/build image digests and the minimal writable paths; verify the real binary executes and config parses under non-root read-only conditions before finalizing image settings.

## 2. Add narrow transport compatibility

- [x] 2.1 Add validated `MCP_ALLOW_CHATGPT_TUNNEL_HOST` handling in settings/server construction; test absent/blank/0/1/invalid values and that only exact `moomoo-mcp:8000` is additionally accepted with opt-in.
- [x] 2.2 Extend `private_chatgpt_preflight.py` with explicit exact Compose URL opt-in, no-proxy transport, refused redirects, bounded response handling and classified safe errors; test localhost regressions, wrong ports/aliases/userinfo/query/fragment/private addresses, redirect classes and proxy traps.
- [x] 2.3 Verify initialize, discovery and calls with ordinary bearer; preserve missing/wrong/conflicting-header rejection, unexpected Host/Origin denial, stateless behavior and all tool annotations using ASGI/protocol tests.
- [x] 2.4 Retain READ_ONLY mutation-dispatch coverage for placement/modification/cancellation/unlock/operator recovery and prove SIMULATE/REAL startup is refused, including zero broker-write counters; do not modify trading policy to satisfy integration tests.

## 3. Build the independent container runtime

- [x] 3.1 Add the dedicated Dockerfile and allowlisted build-context preparation, reusing release manifest/integrity installer; verify checksum failure precedes extraction/execution and inspect final image contents for absence of broker components and synthetic secret canaries.
- [x] 3.2 Add container-only configuration using exact Docker DNS URL, file-backed runtime/discovery headers and loopback health/admin binding; verify parsing and safe doctor diagnostics with the pinned binary and synthetic files.
- [x] 3.3 Add minimal PID 1 startup gating with a 90-second deadline, bounded transient retries and immediate fatal-error refusal; test delayed MCP, exhausted deadline, invalid auth/mode/results, and that no client polling starts before READ_ONLY is proven.
- [x] 3.4 Implement child reaping, signal forwarding, bounded shutdown and liveness-triggered nonzero exit; test real child exit, SIGTERM/SIGINT, SIGSTOP/hang, and independent recovery without treating remote readiness failure alone as process death.
- [x] 3.5 Implement separate safe diagnostics for liveness, readiness and authenticated MCP availability, with a minimal scrubbed child environment; test proxy/config override removal and no credentials, Authorization headers, account results or raw bodies in output.

## 4. Provision container-readable secrets safely

- [x] 4.1 Add a no-secret identity-mapping probe and validation for the selected rootful/rootless Docker context; verify observed marker ownership against effective UID/GID maps and reject ambiguous, root or conflicting host identities.
- [x] 4.2 Narrowly extend the credential helper with atomic staging to fixed names, mapped owner 0400 and root-owned protected parents with minimal traversal ACLs; test symlink/unsafe-parent rejection, fsync/replace behavior and cleanup on failure while preserving root-only 0600 masters.
- [x] 4.3 Preserve no-echo terminal behavior and restoration on success, exceptions and interruption using PTY tests; verify provisioning never puts secret values in arguments, Compose interpolation, tracked files or diagnostic output.
- [x] 4.4 Run actual-image mounted-file permission tests under rootful and rootless Docker: UID 10002 reads config/staged secrets, unrelated and brokerage identities cannot read sources, and runtime cannot read masters or write mounts; fail tests rather than adding world-readability or a root runtime.
- [x] 4.5 Add end-to-end atomic rotation tests for both runtime key and ordinary bearer, including stale bind behavior on restart and forced recreation; simulated control plane and MCP must accept only new values, and evidence must show successful new authenticated traffic plus old-value rejection without printing either value.

## 5. Integrate explicit Compose selection and deployment

- [x] 5.1 Add `docker-compose.chatgpt.yml` with separate service, user-defined outbound-capable bridge, numeric user, read-only root, dropped capabilities, no-new-privileges, minimal tmpfs/mounts and independent restart policy; verify rendered synthetic configs have no shared namespaces, extra ports, Docker socket or brokerage/journal mounts.
- [x] 5.2 Preserve existing base/production/paper/smoke assertions and add an overlay matrix; prove default deployment has one brokerage service and needs no tunnel settings/secrets while enabled deployment adds exactly one tunnel service without changing OpenD binding or host publication.
- [x] 5.3 Extend deployment/wrapper selection explicitly and persist non-secret selection plus optional immutable image identity; test consistent context/project/files in start and verification, default-off behavior, and no orphan removal of the enabled tunnel.
- [x] 5.4 Add scoped enable/disable and rollback behavior, including refusal of non-READ_ONLY mode with the overlay selected and refusal of an unsupported older target until tunnel disablement; verify state restoration and targeted recreation retain OpenD/journal volume identities and existing local-client behavior.
- [x] 5.5 Test optional-image build/enable/update and reuse of its recorded immutable identity on restart/recreation; prove no runtime download, implicit upgrade, tunnel setup requirement for default deploy, or accidental inclusion of secret files in the build context.

## 6. Exercise disposable runtime integration

- [x] 6.1 Add `scripts/test-tunnel-container.sh` with unique project names, synthetic inputs, scrubbed environment, random available loopback host ports and project-scoped cleanup; enforce fixture-only endpoints/egress including accidental fallback; run alongside a dummy occupied port 8000 and prove no unrelated container or volume is touched.
- [x] 6.2 Run the actual tunnel image/entrypoint and pinned official binary against the simulated control plane and authenticated real MCP endpoint; verify Docker DNS forwarding, runtime permissions, READ_ONLY gate, Host/Origin/auth failures and no tunnel-published ports.
- [x] 6.3 With the actual brokerage supervisor and loopback OpenD stub, prove tunnel-origin MCP:8000 succeeds while OpenD:11111 fails, namespace isolation holds and both services retain outbound connectivity; record stub limitations rather than claiming broker login.
- [x] 6.4 Stop/restart/recreate the tunnel while continuously probing host-local MCP, then recreate MCP with a deliberately changed disposable IP; prove local independence and DNS recovery without session reuse or request replay.
- [x] 6.5 Exercise delayed startup, control-plane outage, readiness recovery, hung-client restart and bounded termination under actual image hardening; distinguish liveness, readiness, MCP reachability and broker-backed availability in the evidence matrix.
- [x] 6.6 Exercise disablement, migration sequencing and legacy rollback with disposable state markers and simulated systemd lifecycle assertions; verify mutually exclusive tunnel mechanisms, persistent OpenD/journal identities and authenticated local access without Compose down or volume deletion.

## 7. Integrate CI and operator documentation

- [x] 7.1 Require real managed image integration in rootful/rootless CI and retain sanitized direct-client characterization reports. Verify upstream FAILs stay visible while malformed/missing/duplicate cases, unexpected exits and fixture errors fail reporting; retain summaries/artifacts.
- [x] 7.2 Remove fixed project/host-port assumptions from reused smoke harness paths and their CI image references as needed; verify existing brokerage/paper tests still run without taking port 8000 from a developer stack.
- [x] 7.3 Update the Compose-first runbook for managed official-client scope, conditional upstream limitations and operational startup checks; retain secret mapping/rotation, auth/READ_ONLY, migration and scoped rollback, with external acceptance pending.
- [x] 7.4 Reconcile README, deployment/rootless/restart docs and OpenSpec context with accepted official-client requirements, preserving topology, authentication, mode controls and historical evidence.
- [x] 7.5 Document required MCP recreation, preserved project/volumes, stop-and-disable legacy-before-Compose sequence and reverse rollback sequence; explicitly state ordinary bearer mode dependence and stop tunnel before SIMULATE/REAL.

## 8. Managed verification and draft handoff

- [x] 8.1 Run Ruff lint/format, basedpyright, full pytest, revised reporting/deployment regressions, final-image rootful/rootless tunnel checks and strict OpenSpec validation. Record exact outcomes/limits; hosted/live runs retain separate status.
- [x] 8.2 Record revision, exact tested official binary/image identities, managed checks and characterization results. Preserve historical files unchanged; keep VPS/OpenAI/ChatGPT/iPad milestones pending.
- [x] 8.3 Update existing draft PR #38 with official-client scope and actual verification evidence, link it to the thread and report hosted CI separately. Leave merge/promotion and production/live acceptance to subsequent work; do not claim upstream flaws repaired.

## 9. Apply the accepted scope revision

- [x] 9.1 Replace unconditional deployment refusal with valid selection and legacy-service checks; retain bearer/READ_ONLY validation and authenticated container startup. Verify allowed READ_ONLY and refusal of invalid selection, missing auth, SIMULATE/REAL and active/enabled/unknown legacy states using disposable command fixtures.
- [x] 9.2 Replace blocked image labels with managed official-client profile metadata; build the final image from unchanged verified official/public inputs and verify provenance/runtime behavior.

## Independent implementation checkpoint — 2026-09-30

34/36 tasks are complete. Only **1.2** and **8.3** remain incomplete. Rootful and
rootless hosted integration passed at `89cac04`, including freshly built brokerage
images, real mapped permissions, both rotations, journal/OpenD volume identity
preservation, actual scoped disablement, local MCP independence and credential-free
outbound DNS/TLS probes. Mode refusal before a future approved direct wrapper start
has additional synthetic regression coverage. Expanded compatibility fixtures keep
all failures visible; successful independent jobs do not close task 1.2.

The GitHub workflow permission was refreshed and publication is working. Main's
original proposal was integrated without reverting this approved dependency graph
or changing historical evidence. See `independent-verification.md` and
`continuation-verification.md` for exact hosted run links, per-case observations,
remaining compatibility coverage and the next head's verification status.

## Synthetic matrix follow-up — 2026-10-02

The owner authorized completion of the remaining synthetic cases and preparation
of a private upstream report. The expanded fixture explicitly enumerates 515
cases, including same-origin/loop redirects, actual endpoint methods and bodies,
individual proxy variables, missing keys and authentication/retry ambiguities.
See [current evidence](synthetic-compatibility-20261002.md) for exact runs and
fixture corrections. No checkbox changes: 34/36 complete, 1.2 BLOCKED and 8.3
incomplete. The private report is outside tracked files and has not been sent.
