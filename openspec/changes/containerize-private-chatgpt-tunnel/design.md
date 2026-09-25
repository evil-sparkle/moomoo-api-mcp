# Design

## Context

See `proposal.md` for motivation and baseline. OpenSpec context resolves to this isolated latest-main worktree, using schema `spec-driven` and CLI 1.13.1. The original checkout contains an unrelated untracked backup and is untouched. PR #36's archived design is historical evidence, not an artifact to reopen.

Observed baseline:

- Base Compose contains one brokerage service; production overrides its image, paper adds journal storage, and smoke swaps only the OpenD executable. The supervisor owns OpenD and MCP, pins OpenD to loopback, and has asymmetric child recovery. None of this needs redesign.
- MCP already listens on `0.0.0.0:8000` inside its container, with host publication `127.0.0.1:8000:8000`. The server enables DNS-rebinding protection with a fixed local Host list and no allowed Origins. The Docker service Host is currently rejected.
- Preflight validates initialize, tool discovery, and authenticated `check_health` READ_ONLY; full mode also validates accounts/positions. Its URL validation is loopback-only, but its standard `urlopen` currently inherits proxy and redirect behavior. Merely adding a hostname would leave a credential-routing gap.
- The release manifest pins official Linux amd64 `v0.0.14`, commit `0f870e50a973fa820d4c409000059e181e8d242b`, archive SHA-256 `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`. The installer verifies before extraction/execution and checks the reported version. Existing runtime isolation tests use a stub, not the real client.
- Systemd supplies private credential copies from root-only masters. The credential helper already uses a no-echo terminal, restoration in `finally`, restrictive temporary files, fsync and atomic replacement.
- `compose-prod.sh` selects the rootless context and fixed base/production files. `deploy.sh` uses `--remove-orphans`, rollback and an in-memory Compose verifier. Overlay selection must survive that lifecycle. Existing smoke uses a fixed project and host port 8000; the new harness must not copy that collision risk.

The [official tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), checked 2026-09-25, confirms outbound HTTPS, runtime Read + Use permissions, separate workspace eligibility, and distinct health/readiness endpoints. Its rolling latest-release advice does not override this migration's reviewed version pin. The archived design supplies version-specific behavior evidence; implementation must validate the exact binary, not infer behavior from rolling docs.

## Goals / Non-Goals

**Goals:** Keep one unchanged brokerage container with two supervised children; add one independently managed optional tunnel container; make authenticated access and secret permissions demonstrable under the actual Docker identity mapping; preserve local clients and durable state.

**Non-Goals:** Host daemon installation as a prerequisite, changing gateway supervision or trading policy, OAuth, new tools, NGINX, Funnel, public ingress, Python TLS, Kubernetes, custom tunneling, unrelated release upgrades, or owner credentialed acceptance during repository automation.

## Decisions

### 1. Explicit overlay with independent namespaces

Add `docker-compose.chatgpt.yml` with service `chatgpt-tunnel`. Declare the project-scoped default network explicitly as a user-defined bridge with normal outbound access (`internal` false). Both services use Docker DNS; the sole MCP URL in container config is `http://moomoo-mcp:8000/mcp`. Preserve the existing network identity where Compose already created the project default network. No IP pin, `extra_hosts` replacement, host networking, shared network/PID namespace, privilege, socket, brokerage volume or journal mount is permitted.

The brokerage publication and loopback OpenD binding remain unchanged. Tunnel ports are absent. Its health/admin endpoints bind `127.0.0.1:8080` inside its own namespace and are inspected with `compose exec -T chatgpt-tunnel` using a safe diagnostic command. Neither service loses outbound DNS/HTTPS connectivity. The bridge is not an authorization boundary: ordinary MCP authentication and the isolated OpenD listener remain essential.

Base/production/paper deployment without this overlay must resolve and start without any tunnel input. Use an explicit `--chatgpt` selection in deployment tooling, persisted as non-secret selection metadata separately from credentials, with no implicit autodetection from secret files. The wrapper and verifier must use identical selected files, context, project and saved image inputs. Persist and restore that selection on deployment rollback. Explicit disable stops/removes only `chatgpt-tunnel` before clearing selection; an older target lacking the overlay is refused while enabled and requires this disable step first. Prevent normal deploy from treating an enabled tunnel as an orphan. Preserve any existing paper overlay and volume identity.

Alternative: profiles in the base file still resolve tunnel-only substitutions and complicate the no-credentials path. Shared namespace would expose loopback OpenD and is prohibited. A separate Compose project complicates DNS and lifecycle selection without improving this boundary.

### 2. Dedicated verified image and minimal runtime manager

Add a dedicated Dockerfile under `deploy/tunnel-client/` using a small Python 3.12 slim runtime for the existing standard-library preflight and a minimal PID 1 manager. Do not copy the broker SDK, OpenD, source tree, or production environment. Pin the selected runtime and build-stage images by immutable digest in implementation; record the exact tags/digests in review evidence rather than inventing digests here. Reuse `release.json` and `install.py` during build. Verify archive integrity before executing any extracted binary, check version, and copy only the verified client and required scripts/config into the final image. No runtime download or `latest` input. Keep the production pin at v0.0.14. Any future official release candidate and production pin change require separate review/authorization; no such change is authorized by this planning revision.

Build from the reviewed checkout with a narrow build context; stage only enumerated public source inputs so deployment secrets cannot enter the build context. Initially build the optional image explicitly on enablement and record its resulting immutable image ID alongside the source commit; restart/recreation reuses it. CI builds the same Dockerfile. No new image registry or publishing infrastructure is necessary for this migration; a future distribution change is separate. Base brokerage production image handling stays intact.

Run UID/GID `10002:10002` (distinct from brokerage 10001), read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, umask 0077, and size-bounded tmpfs for `/run/moomoo-chatgpt-tunnel`, a private HOME/state directory, and `/tmp` only if required by the verified client. Own writable paths by 10002, use restrictive modes and nosuid/nodev; prefer noexec when compatible. Configuration and secret mounts are read-only. Persist no tunnel state unless binary testing demonstrates a specific need requiring review.

The small manager owns one client child, reaps children and forwards SIGTERM/SIGINT with a bounded 10-second grace then kill; Compose stop grace exceeds this. It is process management, not a protocol proxy or custom tunnel. It runs preflight before launching the client. Use `restart: unless-stopped` independently of the brokerage container. Child exit makes PID 1 exit nonzero. Check child liveness via bounded loopback `/healthz` probes every 10 seconds, allowing 30 seconds startup grace; three consecutive local liveness failures terminate the child and exit nonzero. A hung child must therefore trigger Docker restart without relying on healthcheck status. `/readyz` failures due solely to control-plane outages report unavailable but do not cause perpetual restart loops. Keep MCP availability separately visible; repeated MCP loss can stop the child and return to gated startup, never replay requests.

Retry transient DNS/refusal/timeouts/5xx during startup within a 90-second monotonic deadline, with per-request timeouts bounded by remaining time and a three-second retry interval. Wrong credentials, Host/Origin refusal, malformed results and non-READ_ONLY mode fail immediately. Require initialize, tools/list and check_health READ_ONLY before any polling/forwarding process starts. Degraded OpenD can pass startup-safe mode. Do not run full account acceptance on every restart. Before any child relaunch, repeat the gate. Fixed bounds should be tested with an injectable clock; do not add a large tuning surface.

### 3. Exact opt-ins and credential destination confinement

Add `MCP_ALLOW_CHATGPT_TUNNEL_HOST` to validated settings: absent, blank or `0` is disabled, `1` enabled, every other value fails startup. The overlay sets `1` only on `moomoo-mcp`. Enabling appends exactly `moomoo-mcp:8000` to the existing Host list; it adds no Origin, wildcard, arbitrary hostname or CIDR trust. Apply settings consistently before constructing/serving the transport, including test app factories. Existing localhost behavior, HTTP authentication and stateless responses remain intact.

Preflight adds `--allow-compose-mcp` as a separate explicit opt-in; default loopback URLs stay valid. With it, allow exactly `http://moomoo-mcp:8000/mcp` in addition to current loopback behavior. Reject other ports, aliases, userinfo, queries, fragments, suffixes and arbitrary private IPs before reading credentials or opening a connection. Use an explicit no-proxy opener and refuse redirects (including 301/302/303/307/308), with bounded response handling and safe error classifications.

For the official client, pass a minimal environment, clearing all upper/lower-case HTTP/HTTPS/ALL proxy variables, `NO_PROXY` surprises and client configuration overrides; copy no brokerage environment. Config uses file references for the runtime key and both discovery/runtime Authorization headers. Only the non-secret selected tunnel ID may be converted to the client's documented environment setting. Validate exact production endpoints/config before spawn. Never expose values in argv, rendered Compose output, debug logs, doctor output, or raw MCP results.

The official-client compatibility matrix is a mandatory release gate, separate from implementation dependencies. The verified v0.0.14 and investigated v0.0.15 binaries failed control-plane redirect confinement of the **OpenAI runtime key**. This does not establish failure or success for the distinct **ordinary MCP bearer** used by preflight, discovery, startup initialize and forwarded calls. Keep separate synthetic credentials, observations and results for each path. No release, production enablement or ready-for-review transition is permitted while a required scenario fails or remains untested. After a new apply authorization, independent work and additional real-binary tests may proceed only with synthetic credentials and disposable fixtures; success on those tasks does not clear the gate. No patched client, proxy workaround, requirement relaxation or production pin change is authorized. See Decisions 7–9 for the matrix, closure evidence and remediation candidate.

Retain all read-only mutation-dispatch tests and tool annotations. The ordinary bearer is not a permanently read-only credential: the startup check establishes current deployment mode, not ongoing token scope. Operators must stop/disable the tunnel before changing MCP to SIMULATE or REAL; automated deployment with the selected overlay must refuse such a mode before making changes.

### 4. Root-only masters and mapped runtime secret copies

Keep `/etc/moomoo-chatgpt-tunnel/` master keys `root:root` 0600 and the existing legacy permissions contract. Do not mount masters directly. Narrowly extend the no-echo helper with explicit container staging for the two known credential names and tunnel-ID metadata. Staging is a root-run provisioning operation, never a root tunnel daemon. Secrets remain outside the repository, build context and Compose interpolation.

Use a dedicated host staging directory, for example `/var/lib/moomoo-chatgpt-tunnel/compose-secrets/<deployment-id>`, and file-backed read-only mounts to `/run/secrets/control-plane-api-key`, `/run/secrets/mcp-authorization`, and `/run/secrets/tunnel-id`. The non-secret config is baked into the image and refers to those paths, not `/run/credentials/<unit>/...`.

Determine the host UID and GID corresponding to container 10002 by launching a disposable no-secret probe in the exact Docker context/user namespace and observing a marker's numeric ownership in a temporary bind directory. Cross-check the active UID/GID maps; do not assume host 10002, a fixed subuid offset, or that rootless and rootful mappings match. The root provisioning helper validates the selected deployment/context metadata, rejects unexpected/ambiguous mappings and refuses host UID 0 or a conflicting identity. Reprobe after daemon/user-namespace changes.

Staged files: owner mapped UID/GID, mode 0400, no additional read ACLs. Root-owned parent directories: mode 0700 before ACLs, explicit traverse-only access for the rootless daemon owner, and traverse access for the intended mapped runtime identity where needed. No other-user permissions, default inheritable ACLs or writable non-root parents. Avoid listing access where only traversal is required. The daemon/controller and host root remain trusted authorities; this does not claim to defend against someone who controls Docker and can mount the files elsewhere. Test unrelated UIDs including brokerage's mapped 10001 cannot read. File-backed Compose `uid/gid/mode` declarations are not relied on: actual ownership must be correct before mount. Verify the daemon can mount each file and the actual non-root runtime can read it; never fix a failure using world-readable files or root runtime.

Use fixed credential names, reject symlinks and unsafe parent components, use directory-relative no-follow operations, restrictive temporary files, fchown/fchmod before exposure, fsync and atomic rename. Retain terminal restoration tests for success, errors and interrupts. Configuration validation can report file readability/ownership status, never content. On remote Docker/VM contexts, staging must occur on the daemon host; local client paths are not assumed to exist there. Initially support Linux amd64 with rootful and rootless engines and POSIX ownership/ACL support; document unsupported mappings as fail-closed prerequisites.

Atomic replacement changes the source inode, so an existing file bind may keep the old inode. Rotation therefore requires **force recreation**, not process or container restart. Stop the tunnel, replace master via no-echo prompt, atomically restage its mapped copy, then `up -d --no-deps --force-recreate chatgpt-tunnel` using the saved overlay/context/project/image. Test that a restart can retain the old mount and that recreation actually authenticates with the new value. Runtime-key fixture rejects old key; MCP rotation updates server and local clients as well, proves old bearer 401/new bearer succeeds, then restarts tunnel forwarding. Revoke old real control-plane credentials after successful cutover where supported. Never print either value or compare by emitting a secret hash.

### 5. Concrete implementation surface

| Files/components | Expected work |
| --- | --- |
| `deploy/tunnel-client/Dockerfile`, new container config, entrypoint/diagnostic scripts | Dedicated verified image, narrow build context, runtime manager and container file references |
| `deploy/tunnel-client/release.json`, `install.py`, existing YAML/unit | Reuse reviewed release/integrity logic; retain and label legacy configuration/unit; upgrade only for demonstrated blocker |
| New `docker-compose.chatgpt.yml` | Explicit optional service, bridge, hardening, mounts, restart policy and exact MCP Host opt-in |
| `scripts/compose-prod.sh`, `scripts/deploy.sh`, `scripts/deploy_verify.py` | Explicit/persisted overlay selection, same effective model for verification, safe orphan handling and rollback; scoped enable/disable operations |
| `scripts/private_chatgpt_preflight.py` | Exact Compose opt-in, no redirects/proxies, bounded retries and safe failure categories |
| `scripts/install_private_chatgpt_credential.py`, new staging/mapping helper if separation is clearer | Retain no-echo master writer; measured identity mapping and atomic narrow staging |
| `src/moomoo_mcp/settings.py`, `server.py` | Validate default-off exact Host opt-in; retain Origin/auth/session behavior |
| Existing topology, deployment, preflight, security, credential, failure-isolation and settings tests | Preserve baseline assertions and add overlay-specific behavioral contracts |
| New `scripts/test-tunnel-container.sh` and isolated fixtures | Real image/client, Docker DNS, runtime permissions, rotation, failure/recovery and redirect/proxy sinks |
| `.github/workflows/ci.yml`, `scripts/smoke-test.sh` | Add relevant build/path triggers and disposable runtime jobs; remove fixed-port/project collision assumptions where reused |
| `README.md`, `docs/private-chatgpt-mcp.md`, `docs/deploy-vps.md`, `docs/rootless-docker.md`, `docs/state-and-restarts.md`, `openspec/config.yaml` | Compose-first operations and accurate brokerage-versus-tunnel topology language; legacy rollback and acceptance record |

Brokerage Dockerfile, supervisor logic, trade service and tool registration should not change unless a narrowly necessary integration defect is demonstrated. Do not erase older isolation tests merely because they assert one service: keep them for default/production deployment and separately assert two services only with the selected tunnel overlay.

### 6. CI and acceptance evidence

Unit/ASGI coverage: settings absent/0/1/invalid; exact Host accepted only with opt-in; other Hosts and nonempty unexpected Origins rejected; initialize/discovery/calls require ordinary bearer; missing/wrong/conflicting bearer and duplicate-header ambiguity fail closed before service construction; READ_ONLY passes, SIMULATE/REAL/malformed mode fail; placement/modification/cancellation/unlock/operator recovery dispatch counters stay zero; annotations and stateless requests unchanged. Redirect/proxy traps test destination validation before credential use and no protected header reaches a sink. Run lint, formatting, types, full relevant pytest suite and strict OpenSpec checks.

Container job: build the actual pinned image with integrity failure coverage, run its real entrypoint as 10002 under read-only rootfs, dropped capabilities and tmpfs; verify `--version`, configuration parsing/doctor and file access. A disposable simulated OpenAI control plane matching the pinned protocol drives the **real official binary** through discovery, polling, forwarding, auth conflict, readiness and rotation. Test-only endpoint/CA overrides are fixture mounts, never production environment escape hatches. Keep stub-process tests only for deterministic unit fault injection. If the actual binary/control-plane fixture cannot exercise a behavior, mark the limitation explicitly and do not report full runtime acceptance.

Network/lifecycle job: real brokerage image and supervisor with the existing loopback OpenD stub; real MCP endpoint using synthetic bearer. Run tunnel-origin initialize/tools/list/check_health over Docker DNS and prove port 8000 succeeds while 11111 fails. Inspect namespaces, mounts and published ports; assert no extra host port. Stop, restart and recreate tunnel while repeatedly probing local MCP. Recreate MCP, deliberately reserve its previous disposable IP in the test project, and verify subsequent traffic resolves the service's changed address. Test delayed MCP, timeout exhaustion, child exit, SIGSTOP/hung child, signals, OpenAI outage and recovered readiness independently. Do not count degraded broker health as proof of account access.

Permission/rotation jobs: exercise rootful and rootless Linux mappings, reject unrelated UIDs and misowned mounts, verify source masters unreadable to runtime, and use a PTY for no-echo/interrupt handling. Atomically rotate synthetic control-plane and bearer files and assert upstream/downstream requests succeed only with the new credentials after recreation; reject old values without exposing them. Scan captured output internally for synthetic canaries, printing only pass/fail. Test disable/rollback with marker state in OpenD/journal volumes and continued authenticated host access.

All jobs use unique Compose projects, explicit synthetic input files, scrubbed environments, removed fixed container names and Docker-assigned available loopback host ports. Keep production's fixed 8000 assertion in isolated config tests; runtime test overrides use random published ports. Do not stop any developer stack to free 8000. Clean only resources carrying the test project identity; never broad prune or production `down`. Update CI path filters for Dockerfile/config/helper/overlay inputs and require runtime tests on affected PRs. Record the actual binary version/digest, image IDs, Docker mode and behavior matrix.

Live VPS deployment, actual OpenAI eligibility/control-plane authentication, full broker account/positions results, ChatGPT web discovery/invocation, and native iPad discovery/invocation remain separate **PENDING** milestones until genuinely authorized and tested. Simulated control-plane success is not live OpenAI acceptance.

### 7. Independent work and current authorization boundary

This planning revision supersedes the blanket pause on all dependent implementation described in the historical `compatibility.md` and `verification.md` records. It does **not** supersede their observations, change any recorded result, or authorize implementation in this turn. Present these artifacts and wait for a new apply request. Task 1.1 remains the sole completed task (1/36); no implementation completion is inferred from this edit.

After that request, the tasks.md dependency table permits image hardening, exact Host/URL opt-ins, entrypoint/signals, Compose integration, secret ownership/rotation, lifecycle fixtures, CI wiring and documentation without first passing task 1.2. These tasks are local-only: synthetic keys and MCP bearer values, disposable master/staging directories, temporary images/projects/volumes and simulated control-plane/MCP/broker fixtures. Never provision real credential sources, attach a fixture to the developer brokerage network, stop its containers or consume its ports. Production endpoint defaults must not be contacted during tests: the harness must enforce fixture-only routing/egress, including failure paths and accidental fallback, while leaving the proposed production bridge's outbound connectivity intact. No real OpenAI requests are authorized, even with a synthetic key.

The existing pin can be built and exercised as a **known-failing test input**, not an approved production artifact. Test-only endpoint overrides cannot become production bypasses. A test using a simulated control plane and the unmodified official binary is real-client coverage; a stub child or ASGI-only test is component coverage. Both are useful, but only the former can satisfy the corresponding official-client matrix rows. Use separate CI results for component/container checks and the mandatory security gate; no xfail, skipped test, continue-on-error, allow-failure or unrelated green job may satisfy the gate. A blocked gate must continue blocking release even when independent jobs pass. New CI code is future implementation, not part of this revision.

Feature selection remains explicit/default-off. PR #38 stays draft; neither a passing local build nor completion of all independent tasks permits promotion, merge, release publication, real credential provisioning or production enablement. Documentation may describe future enablement but must prominently identify the blocker. Simulation of READ_ONLY/SIMULATE/REAL responses tests refusal only; it does not authorize enabling trading.

### 8. Mandatory official-client evidence matrix and closure

Status legend: **PASS** = observed evidence limited to that row; **FAIL** = observed violation; **UNTESTED** = no qualifying runtime evidence; **PENDING / unauthorized** = owner milestone outside current authorization. Historical observations below were already made before this revision; no new test is claimed. `compatibility.md` and the unchanged reproduction retain exact commands, binary provenance and limitations.

| ID | Credential/path and required scenario | v0.0.14 | Investigated v0.0.15 |
| --- | --- | --- | --- |
| I1 | Official archive digest and reported version | PASS | PASS (candidate only; not production pin) |
| CP1 | OpenAI runtime key; control-plane HTTP 302 to changed port | FAIL | FAIL |
| CP2 | OpenAI runtime key; control-plane HTTP 302 to changed host + port | FAIL | FAIL |
| CP3 | OpenAI runtime key; subdomain redirect | UNTESTED | UNTESTED |
| CP4 | OpenAI runtime key; other 301/303/307/308 codes, same-origin redirects, multihop/loop cases | UNTESTED for every listed case | UNTESTED for every listed case |
| CP5 | OpenAI runtime key; fixture HTTPS-to-HTTPS and HTTPS downgrade redirects with verified synthetic CA | UNTESTED for both cases | UNTESTED for both cases |
| CP6 | OpenAI runtime key; complete polling/response-posting/metadata redirect matrix per method and protected endpoint, including no credential injection on an unapproved initial destination | UNTESTED; CP1/CP2 do not establish all methods/endpoints | UNTESTED; CP1/CP2 do not establish all methods/endpoints |
| CP7 | OpenAI runtime key; upper/lower-case inherited proxy poisoning and overrides under the actual entrypoint | UNTESTED | UNTESTED |
| CP8 | OpenAI runtime key; normal authenticated poll, metadata and response delivery; missing/wrong/revoked key refusal without anonymous retry | UNTESTED for every listed case | UNTESTED for every listed case |
| CP9 | OpenAI runtime key; atomic rotation, new value used after recreation and old value refused | UNTESTED | UNTESTED |
| MD1 | Ordinary MCP bearer; normal authenticated discovery, startup initialize, tools/list and health through the official binary | UNTESTED for every listed case | UNTESTED for every listed case |
| MD2 | Ordinary MCP bearer; discovery/startup redirects: changed host, subdomain, port, scheme, 301/302/303/307/308 and multihop | UNTESTED for every listed case; source inspection is not PASS | UNTESTED for every listed case |
| MF1 | Ordinary MCP bearer; normal forwarded read-only tool call and valid returned result through simulated control plane | UNTESTED | UNTESTED |
| MF2 | Ordinary MCP bearer; forwarded-call redirects: changed host, subdomain, port, scheme, 301/302/303/307/308 and multihop | UNTESTED for every listed case | UNTESTED for every listed case |
| MA1 | Ordinary MCP bearer; missing, wrong, conflicting/ambiguous forwarded Authorization refused without broker dispatch or anonymous retry | UNTESTED with official binary; existing ASGI tests are separate | UNTESTED with official binary |
| MP1 | Ordinary MCP bearer; inherited proxy poisoning on both discovery/startup and forwarding, no exposure to control-plane/sinks | UNTESTED for both paths | UNTESTED for both paths |
| MR1 | Ordinary MCP bearer; coordinated server/local-client/tunnel rotation and new-value forwarding after recreation, old value refused | UNTESTED | UNTESTED |
| CT1 | Actual final image/entrypoint, numeric UID, rootful/rootless mapped secrets, read-only filesystem/capabilities and no secret logging | UNTESTED for each property | UNTESTED for each property |
| CT2 | Docker DNS MCP success, OpenD:11111 refusal, namespaces/mounts/ports and default deployment without tunnel inputs | UNTESTED for each property | UNTESTED for each property |
| CT3 | Exact Host/Origin and authenticated READ_ONLY startup/mutation refusal through actual container integration | UNTESTED | UNTESTED |
| CT4 | Delayed startup, SIGINT/SIGTERM, exit/hang, liveness/readiness separation, upstream outage/recovery under hardening | UNTESTED for each container case; prior host SIGTERM observations are limited | UNTESTED for each container case |
| CT5 | Tunnel stop/restart/recreation leaves local MCP usable; MCP recreation with changed IP recovers through DNS | UNTESTED for each case | UNTESTED for each case |
| CT6 | Migration/rollback and preserved OpenD/journal identities in disposable projects, no developer port/resource disruption | UNTESTED for each case | UNTESTED for each case |
| L1 | VPS deployment; real OpenAI authentication; actual broker account/positions; ChatGPT web; native iPad | PENDING / unauthorized for every milestone | PENDING / unauthorized for every milestone |

Expand grouped rows into per-case results when executed; a group stays incomplete if any member lacks evidence. Record source commit, archive digest, binary version, image ID, test command, environment and expected/actual outcome. Re-run all required rows on the final reviewed official candidate and actual final image, rather than carrying a passing result across changed binaries. The local HTTP reproduction proves client behavior only: it does not establish that the actual OpenAI HTTPS endpoint redirects maliciously, that real credentials were exposed, or that MCP bearer confinement failed.

The release block closes only when **all** of the following are documented and reviewed:

- **G1 — Official provenance and review:** a suitable official release/source is reviewed, archive integrity verified before execution, version recorded, and any proposed production pin change separately authorized. Current v0.0.14 remains unchanged and v0.0.15 is not recommended. Source review must cover control-plane redirect and injection boundaries, not merely a release-note claim.
- **G2 — Credential confinement and normal operation:** CP1–CP9, MD1–MD2, MF1–MF2, MA1, MP1 and MR1 pass for that exact binary, including normal authenticated operations and negative controls. Both OpenAI runtime-key and MCP bearer paths need independent evidence. No FAIL, UNTESTED, expected failure or inconclusive result counts as closed.
- **G3 — Final-container and integration evidence:** CT1–CT6 and all approved tests in task groups 2–7 pass on the real final image/entrypoint with the official binary, synthetic secrets and disposable resources. Full required lint/type/unit/container/paper checks pass; independent test success cannot substitute for G2.
- **G4 — Explicit review and release authorization:** the gate matrix and remaining limitations receive owner review; ready-for-review promotion, merge/release and production enablement require subsequent explicit authorization. Even G1–G3 passing does not authorize real credentials, real OpenAI traffic, VPS deployment, trading or live acceptance. L1 stays pending until each milestone is separately authorized and genuinely tested.

Task 1.2 tracks the mandatory official-client gate; task 8.3 cannot complete before G1–G4. Real OpenAI/ChatGPT/iPad tests are not secretly required to pass a synthetic CI gate, nor may CI claim they passed. They remain separate acceptance/authorization boundaries.

### 9. Minimal upstream remediation candidate and disclosure handling

Candidate **for review only**: the official client should install an explicit control-plane redirect policy on every credential-bearing control-plane HTTP client, including secondary/secret clients. Prefer rejecting redirects; if an official protocol requires any redirect, permit only explicitly reviewed exact destinations with scheme/host/effective-port checks at every hop, no downgrade and bounded hops. Do not treat subdomain relationships or host-only equality as origin equality. Reject before any transport can send headers or bodies to an unapproved destination.

Independently, scope runtime-key injection to the validated intended control-plane destination, and refuse an unapproved request before injecting or retaining protected Authorization. This second boundary covers initial requests and secondary clients as well as redirects; it must not rely solely on Go's automatic sensitive-header stripping. Keep ordinary MCP bearer injection separate and retain its own discovery/forwarding confinement. Review normal poll/response/metadata behavior and the existing official protocol before choosing rejection versus a documented exact redirect allowlist. This is a remediation design candidate, not a claim of an upstream fix or an implementation instruction to patch the client locally.

No forked/patched client or proxy is shipped, no upstream issue/report is posted, and no production pin changes under this plan. Preserve the existing reproduction and historical compatibility/verification records byte-for-byte. Any further disclosure draft, detailed security report or additional sensitive exploit material must be prepared privately outside tracked/public files, with a restrictive private directory and files, excluded from build/CI artifacts and logs. No such additional disclosure material is needed or created for this planning revision. Upstream/private reporting requires explicit authorization. This tracked section contains only the requested high-level remediation candidate and release requirements.

## Risks / Trade-offs

- Rootless mappings, remote daemon paths and host ACL support vary → measured mapping plus actual mounted-file tests; no permissive fallback.
- v0.0.14 and investigated v0.0.15 fail OpenAI runtime-key redirect confinement → retain hard release/production-enablement block, test independent work only with synthetic fixtures, and require reviewed official remediation plus complete gate evidence. Writable-path compatibility remains untested.
- Ordinary bearer privileges follow deployment mode → stop tunnel before mode changes; deployment guard plus server policy, never annotations as authorization.
- Local MCP briefly interrupts when applying new settings/image/network → documented targeted recreation under the same project and volumes; no promise of zero downtime.
- A runtime manager adds code → keep it limited to startup/liveness/signals; test hangs and do not restart on remote readiness failures alone.
- Rolling product documentation cannot prove owner eligibility or iPad support → keep independent pending milestones.

## Migration Plan

The following is a future operator procedure, not permission to execute it. Release gates G1–G4 below and separate operator authorization are prerequisites. PR #38 remains draft, production enablement is blocked and the production pin is unchanged. Independent testing substitutes disposable resources and synthetic credentials throughout.

1. After implementation review, install the reviewed checkout/image inputs and verify default local deployment first. Record non-secret Docker context, Compose project, image ID, network and volume identities. Confirm READ_ONLY and Linux/mapping prerequisites without displaying resolved deployment configuration.
2. Explicitly `systemctl disable --now moomoo-chatgpt-tunnel.service` if installed; verify inactive before starting Compose. Keep its reviewed assets and protected masters for rollback. Never run both mechanisms for one tunnel.
3. Build the dedicated verified image; run the no-secret mapping probe and root provisioning helper on the daemon host. Provision only runtime key, selected tunnel ID and ordinary bearer. Test readability as the actual runtime identity and denials for unrelated identities.
4. Explicitly select/save the overlay. Apply the brokerage image/settings with `up -d --no-deps moomoo-mcp` as needed, preserving existing project and OpenD/journal mounts. This recreation is necessary for a new Host setting and potentially network attachment; retain prior image/selection for rollback. Verify localhost MCP and state before enabling the tunnel.
5. Start only `chatgpt-tunnel` after preflight, then inspect liveness/readiness from inside it. Run separate full local acceptance only with owner authorization. Record later product milestones independently.
6. Disable using selected Compose `stop chatgpt-tunnel`, optionally `rm -f chatgpt-tunnel`; no `down`, volume deletion or brokerage restart is required just to stop it. Clear saved selection only after removal. Remove only its staging copies when retiring the integration.
7. Rotate with stop → no-echo master replacement → atomic mapped staging → coordinated MCP/client update for bearer rotation → local verification → force-recreate tunnel → verify new authentication and old rejection. Do not trust a restart alone.
8. Roll back by stopping/removing the tunnel first. Leave local MCP and all volumes intact; remove the optional Host setting by targeted brokerage recreation only if desired or restoring the prior image. Before re-enabling legacy systemd, verify the Compose tunnel is absent, legacy credential copies/config point to host loopback, and READ_ONLY preflight passes. Never reset OpenD authorization or journal storage to repair this migration.

## Assumptions and External Prerequisites

Docker Compose must support the repository's existing reset/override syntax; implementation records its tested minimum version. Provisioning needs host root for ownership/ACL operations, while runtime stays non-root and the production daemon stays rootless. An approved immutable slim-image digest and available official v0.0.14 archive are build prerequisites, not claims of a build already performed. Live use requires owner-selected tunnel, limited Read + Use key, permitted outbound OpenAI HTTPS, and eligible linked organization/workspace. The proposal creates no account resources and inspects no real credentials.
