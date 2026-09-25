# Independent implementation evidence — release BLOCKED

Task **1.2 remains BLOCKED**; **8.3 is incomplete**. PR #38 remains draft.
The governing planning revision `36f3f69` was pushed before implementation.
Historical `compatibility.md`, `verification.md` and the original reproduction
script are unchanged. No production or live acceptance is claimed.

## Provenance and environment

- Unchanged official **v0.0.14**, source
  `0f870e50a973fa820d4c409000059e181e8d242b`, archive SHA-256
  `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`.
- Dedicated base `python:3.12.12-slim-bookworm` at
  `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`.
- Tested final tunnel image:
  `sha256:8f37178d25dcffa9983fdc1afec4e92edaeaa8bbd86941f9898937d1c7722651`.
  Public build-input digest:
  `ea2f41f12c45444ca7f2f142dfdd2b5482da3351ec1455cb4b19743447805aca`.
  The revision label records checkpoint `a1bafc1`; the input digest additionally
  identifies the then-uncommitted final image inputs. No changed binary is used.
- Rootless Docker **29.8.1**, runtime **10002:10002**, measured host mapping
  **110001:110001**, daemon owner **1001**. Mapping is measured for each daemon;
  these observed numbers are not hard-coded deployment configuration.
- Rootful Docker is unavailable locally. The separate rootful CI lane is pending.
- A full brokerage build was canceled during export at 99% disk usage. Integration
  uses existing `moomoo-smoke-moomoo-mcp:latest`, actual supervisor, a read-only
  public copy of current source, and an OpenD TCP stub. It proves neither a newly
  built brokerage artifact nor broker login/account results. CI builds that image.
- All keys and bearer values are synthetic. Unique disposable projects use
  available loopback ports; an existing listener or a fixture dummy occupies 8000.
  Tunnel/control-plane fixtures have no external egress. Production networking
  remains an outbound-capable bridge; its internet connectivity is not inferred
  from the deliberately isolated fixture network.

## Reproducible checks and results

| Command / check | Result and limits |
| --- | --- |
| `.venv/bin/pytest -q` | **1334 passed, 1 skipped, 29 warnings, 81 subtests passed**, 171.45 seconds; the existing skip is not a compatibility-gate skip |
| `.venv/bin/ruff check .` and `ruff format --check .` | PASS; 97 files formatted |
| `NODE_OPTIONS=--max-old-space-size=1024 .venv/bin/basedpyright` | PASS: **0 errors, 0 warnings, 0 notes**; runtime directory included in normal CI type coverage |
| `bash -n` changed shell scripts; `git diff --check` | PASS |
| `openspec validate --all --strict --no-interactive` | PASS: **27 passed, 0 failed** |
| `scripts/build-tunnel-image.sh` | PASS with pinned verified binary, allowlisted context and hardened runtime; no secret inputs |
| `.venv/bin/python tests/fixtures/tunnel_container_checks.py --broker-image moomoo-smoke-moomoo-mcp:latest` | PASS (exit 0) on the final image; expanded run also passed. Added real-exit and real Host/Origin/auth probes passed separately against that same image/project; both are embedded in the harness for CI |
| Real child-exit probe embedded in that harness | PASS separately on final image with `--network none`; actual binary deliberately gets a missing fixture config, manager reaps exit and returns 1. Only this isolated exit test bypasses startup; full integration exercises the real startup gate |
| `SMOKE_REUSE_IMAGE=1 SMOKE_IMAGE=moomoo-smoke-moomoo-mcp:latest scripts/smoke-test.sh` | PASS on existing image; unique project, synthetic environment, available fixed-per-run host port; no developer stack stopped |
| `scripts/test-paper-container.sh moomoo-smoke-moomoo-mcp:latest` | C01–C04 PASS; fixture permissions corrected for restrictive host umask |
| Original real-client redirect reproduction inside pinned Python container, `--network none` | **FAIL, exit 1**: CP1 and CP2 still demonstrate OpenAI runtime-key diversion. Not xfailed, skipped or hidden by pytest |
| Official `doctor --json` with local fixtures | **FAIL, exit 2**: `oauth_metadata` probes omit ordinary MCP auth. Config/source/id/key/target/listener checks PASS. Raw output suppressed; doctor is not the authenticated startup gate |
| GitHub Actions | **NOT RUN for implementation**: push rejected because the OAuth App lacks `workflow` scope. CI definitions remain in local commits; no remote green result is claimed |

The earlier full-suite checkpoint had two failures (a stale startup mock argument
and a paper recovery subprocess timeout during concurrent verification). The mock
was updated for the explicit opt-in argument; the recovery test was not weakened.
Both passed individually, then the full final suite passed as reported above.
Initial full type checking exceeded the default Node heap; the bounded 1024 MB
run is used instead. Earlier fixture routing/user-context mistakes were corrected
and rerun; a failed identity switch is never accepted as permission-denial proof.

## Behavior observed with the unmodified real client

| Boundary | Evidence |
| --- | --- |
| Startup | Delayed MCP produces zero control-plane polls. Authenticated initialize/list/READ_ONLY health then permits actual client polling. Actual-image SIMULATE/REAL response fixtures, wrong auth and malformed results exit before polling; unavailable MCP exhausts the 90-second deadline. No brokerage trading mode is enabled by these response fixtures |
| Normal paths | Simulated control plane accepts the **OpenAI runtime key** for metadata/polling/responses. The actual client separately sends the **ordinary MCP bearer** for initialize, tools/list and READ_ONLY health; all three valid results return |
| MCP auth/transport | Official forwarded wrong and conflicting Authorization cases are refused. An additional probe against the actual MCP container passes approved Host and rejects unexpected Host/Origin and missing/wrong/conflicting bearer headers. Component tests additionally cover non-ASCII credentials, default-off Host/URL, stateless HTTP, redirects, proxy traps and unchanged annotations. This does not prove official discovery/forwarding redirect confinement |
| Mutation policy | Existing READ_ONLY placement/modification/cancellation/unlock/recovery tests retain zero-write-dispatch assertions; trading policy unchanged. Component evidence is distinguished from actual gateway acceptance |
| Network | Actual supervisor's OpenD stub positively accepts brokerage loopback:11111, while sidecar bridge:11111 is refused. Docker DNS MCP:8000 works, PID/network namespaces differ, admin:8080 is inaccessible from peer container, and tunnel publishes no port |
| Filesystem | Actual runtime UID reads mounted config/secrets; root filesystem and secret mounts reject writes. Real unrelated numeric users created with `setpriv` cannot read protected sources. Runtime cannot read root-only masters. Symlinks, unsafe parents and injected atomic-write failure are rejected with cleanup |
| Runtime-key rotation | Atomic master/staged replacement leaves the running file bind on its old inode. Old key is rejected; recreated real client authenticates with the new key. Docker 29.8.1 also refreshed mounts on container restart here; supported procedure still requires force recreation and actual new authenticated traffic |
| Ordinary bearer rotation | Stop tunnel, rotate server/staged bearer, recreate MCP on a changed IP, reject old host bearer, accept new host bearer, recreate tunnel and observe new authenticated forwarding. No credential values printed |
| Independence/recovery | Tunnel stop/restart/recreation leaves authenticated host MCP usable. Hung actual client triggers manager failure and Docker restart. Running tunnel recovers after MCP IP replacement without tunnel recreation. SIGTERM/SIGINT exit cleanly. Control-plane network outage does not cause a liveness restart loop; authenticated forwarding recovers after reconnection |
| Readiness limits | `/healthz` is liveness; `/readyz` is local startup readiness, not proof of control-plane forwarding. During the expanded outage run liveness stayed true and readiness became false. Source allows readiness to remain green in some control-plane failures; diagnostics explicitly avoid claiming end-to-end readiness |
| State/rollback | Actual OpenD state marker and host access survive fixture recreations; paper C01–C04 separately preserve journals. Unit deployment tests cover unsupported rollback refusal, legacy active/enabled/unknown refusal, and tunnel-only stop/remove. A combined final-image migration with journal identity markers remains pending |

## Mandatory matrix: no aggregate pass

Design section 8 contains the scenario list and per-version statuses. New local
observations do not clear untested members:

- I1 PASS. **CP1/CP2 FAIL** on unchanged v0.0.14.
- CP3–CP7 UNTESTED at the required official-client runtime breadth. Scrubbed child
  environment/component proxy tests do not close these rows.
- CP8 PARTIAL: normal authenticated local paths PASS; comprehensive initial
  missing/wrong/revoked keys per endpoint/method still UNTESTED.
- CP9 local simulated rotation PASS; final reviewed-release rerun required.
- MD1 PARTIAL: initialize/list/health PASS; doctor OAuth metadata FAIL; complete
  discovery coverage pending. MD2 official discovery redirect matrix UNTESTED.
- MF1 normal local forwarding PASS. MF2 official forwarding redirects UNTESTED.
- MA1 PARTIAL: official wrong/conflicting forwarding refused; full missing/auth
  ambiguity/no-retry coverage still incomplete. Component tests are separate.
- MP1 official cross-credential/proxy matrix UNTESTED; preflight/manager tests PASS.
- MR1 local ordinary-bearer rotation PASS; final reviewed-release rerun required.
- CT1–CT6 have the local evidence and limitations above; rootful, newly built
  brokerage artifact, production-egress and combined final migration gaps remain.
- L1 VPS, real OpenAI, actual account/positions, ChatGPT web and native iPad:
  **PENDING / unauthorized**.

## Remaining blockers and handoff

The production wrapper rejects tunnel start/restart while the release gate is
false. Default deployment remains independent of tunnel credentials. PR #38 must
stay draft even after independent CI passes. Required release closure remains:
reviewed official source/release integrity, **every** required redirect/proxy
confinement case passing, normal authenticated operation and negative controls for
both credential paths, rotation, final real-client/container integration and G1–G4.
No patched client, credential proxy, relaxed policy or production pin change is
shipped. The upstream remediation candidate remains review-only. No upstream
issue/report or additional public security disclosure was sent.

Publication additionally requires the repository credential's GitHub `workflow`
scope. Do not remove CI files to bypass that rejection. Rootful tests and the fresh
brokerage build remain pending external capacity/CI; local results are not a waiver.
