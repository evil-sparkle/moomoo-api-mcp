# Design

Current decision — 2026-10-07: use the unmodified official client and accept documented conditional direct-client limitations for the fixed managed deployment. This revision supersedes the all-scenarios release gate. Historical observations remain unchanged; no client fork is maintained.

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

Build from the reviewed checkout with a narrow build context; stage only enumerated public source inputs so deployment secrets cannot enter the build context. GitHub Actions builds both images and publishes only on main pushes through the existing AWS OIDC/ECR configuration. Store the tunnel image in the existing `moomoo-api-mcp` ECR repository under `tunnel-<commit>` tags, keeping application tags unchanged and caches independent. Change detection covers every tunnel build input; unchanged images are retagged for each main commit with a source-build fallback when the baseline image is unavailable. PRs build without publishing. No additional ECR repository or IAM change is required.

Production operators do not build the tunnel image. When the tunnel is explicitly selected, `deploy.sh` confirms the matching CI commit tag in ECR, resolves its immutable digest, updates the saved selection and pulls both selected services. Missing or failed tunnel-image lookup aborts before checkout or service changes. Restart/recreation reuses the saved digest; rollback restores the previous selection and image as well as application deployment state. Application image cleanup excludes tunnel tags. The local build helper remains for disposable tests and development only. Default deployment checks and pulls only the application image.

Run UID/GID `10002:10002` (distinct from brokerage 10001), read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, umask 0077, and size-bounded tmpfs for `/run/moomoo-chatgpt-tunnel`, a private HOME/state directory, and `/tmp` only if required by the verified client. Own writable paths by 10002, use restrictive modes and nosuid/nodev; prefer noexec when compatible. Configuration and secret mounts are read-only. Persist no tunnel state unless binary testing demonstrates a specific need requiring review.

The small manager owns one client child, reaps children and forwards SIGTERM/SIGINT with a bounded 10-second grace then kill; Compose stop grace exceeds this. It is process management, not a protocol proxy or custom tunnel. It runs preflight before launching the client. Use `restart: unless-stopped` independently of the brokerage container. Child exit makes PID 1 exit nonzero. Check child liveness via bounded loopback `/healthz` probes every 10 seconds, allowing 30 seconds startup grace; three consecutive local liveness failures terminate the child and exit nonzero. A hung child must therefore trigger Docker restart without relying on healthcheck status. `/readyz` failures due solely to control-plane outages report unavailable but do not cause perpetual restart loops. Keep MCP availability separately visible; repeated MCP loss can stop the child and return to gated startup, never replay requests.

Retry transient DNS/refusal/timeouts/5xx during startup within a 90-second monotonic deadline, with per-request timeouts bounded by remaining time and a three-second retry interval. Wrong credentials, Host/Origin refusal, malformed results and non-READ_ONLY mode fail immediately. Require initialize, tools/list and check_health READ_ONLY before any polling/forwarding process starts. Degraded OpenD can pass startup-safe mode. Do not run full account acceptance on every restart. Before any child relaunch, repeat the gate. Fixed bounds should be tested with an injectable clock; do not add a large tuning surface.

### 3. Exact opt-ins and credential destination confinement

Add `MCP_ALLOW_CHATGPT_TUNNEL_HOST` to validated settings: absent, blank or `0` is disabled, `1` enabled, every other value fails startup. The overlay sets `1` only on `moomoo-mcp`. Enabling appends exactly `moomoo-mcp:8000` to the existing Host list; it adds no Origin, wildcard, arbitrary hostname or CIDR trust. Apply settings consistently before constructing/serving the transport, including test app factories. Existing localhost behavior, HTTP authentication and stateless responses remain intact.

Preflight adds `--allow-compose-mcp` as a separate explicit opt-in; default loopback URLs stay valid. With it, allow exactly `http://moomoo-mcp:8000/mcp` in addition to current loopback behavior. Reject other ports, aliases, userinfo, queries, fragments, suffixes and arbitrary private IPs before reading credentials or opening a connection. Use an explicit no-proxy opener and refuse redirects (including 301/302/303/307/308), with bounded response handling and safe error classifications.

For the official client, pass a minimal environment, clearing all upper/lower-case HTTP/HTTPS/ALL proxy variables, `NO_PROXY` surprises and client configuration overrides; copy no brokerage environment. Compose injects only the existing `MCP_AUTH_TOKEN` from deployment configuration. The launcher validates it and derives `MCP_AUTHORIZATION=Bearer <token>`; both discovery/runtime headers use the official client's supported `env:MCP_AUTHORIZATION` reference. The OpenAI runtime key remains file-backed, and the validated tunnel ID uses its documented environment setting. This deliberately makes the ordinary MCP bearer visible to trusted Docker/host administrators through container/process environment; it avoids maintaining another bearer file. Validate exact production endpoints/config before spawn. Never expose values in argv, rendered Compose output, debug logs, doctor output, or raw MCP results.

Managed acceptance requires the final official image/entrypoint to pass normal authenticated control-plane/MCP operation, negative authentication, proxy filtering, rotation, permissions, isolation and recovery. Direct-client redirect/proxy/doctor cases characterize upstream behavior. Their FAIL/INCONCLUSIVE verdicts do not become managed-runtime PASS results. The fixed OpenAI HTTPS endpoint is trusted; redirection by that trusted endpoint remains a conditional risk accepted by the owner. No fork, proxy workaround or release-pin change is part of this revision.

Retain all read-only mutation-dispatch tests and tool annotations. The ordinary bearer is not a permanently read-only credential: the startup check establishes current deployment mode, not ongoing token scope. Operators must stop/disable the tunnel before changing MCP to SIMULATE or REAL; automated deployment with the selected overlay must refuse such a mode before making changes.

### 4. Root-only masters and mapped runtime secret copies

Keep `/etc/moomoo-chatgpt-tunnel/` master keys `root:root` 0600 and the existing legacy permissions contract. Do not mount masters directly. Container staging handles only the OpenAI runtime key and tunnel-ID metadata; the legacy helper still supports its separate MCP bearer file for rollback. Staging is a root-run provisioning operation, never a root tunnel daemon. File secrets remain outside the repository, build context and Compose interpolation. The ordinary MCP bearer reuses the existing deployment token through explicit Compose environment injection, without copying the whole environment file.

Use a dedicated host staging directory, for example `/var/lib/moomoo-chatgpt-tunnel/compose-secrets/<deployment-id>`, and file-backed read-only mounts to `/run/secrets/control-plane-api-key` and `/run/secrets/tunnel-id`. The non-secret config is baked into the image and uses those file paths plus `env:MCP_AUTHORIZATION`, not `/run/credentials/<unit>/...`.

Determine the host UID and GID corresponding to container 10002 by launching a disposable no-secret probe in the exact Docker context/user namespace and observing a marker's numeric ownership in a temporary bind directory. Cross-check the active UID/GID maps; do not assume host 10002, a fixed subuid offset, or that rootless and rootful mappings match. The root provisioning helper validates the selected deployment/context metadata, rejects unexpected/ambiguous mappings and refuses host UID 0 or a conflicting identity. Reprobe after daemon/user-namespace changes.

Staged files: owner mapped UID/GID, mode 0400, no additional read ACLs. Root-owned parent directories: mode 0700 before ACLs, explicit traverse-only access for the rootless daemon owner, and traverse access for the intended mapped runtime identity where needed. No other-user permissions, default inheritable ACLs or writable non-root parents. Avoid listing access where only traversal is required. The daemon/controller and host root remain trusted authorities; this does not claim to defend against someone who controls Docker and can mount the files elsewhere. Test unrelated UIDs including brokerage's mapped 10001 cannot read. File-backed Compose `uid/gid/mode` declarations are not relied on: actual ownership must be correct before mount. Verify the daemon can mount each file and the actual non-root runtime can read it; never fix a failure using world-readable files or root runtime.

Use fixed credential names, reject symlinks and unsafe parent components, use directory-relative no-follow operations, restrictive temporary files, fchown/fchmod before exposure, fsync and atomic rename. Retain terminal restoration tests for success, errors and interrupts. Configuration validation can report file readability/ownership status, never content. On remote Docker/VM contexts, staging must occur on the daemon host; local client paths are not assumed to exist there. Initially support Linux amd64 with rootful and rootless engines and POSIX ownership/ACL support; document unsupported mappings as fail-closed prerequisites.

Atomic replacement changes the source inode, so an existing file bind may keep the old inode. Runtime-key rotation therefore requires **force recreation**, not process or container restart. Stop the tunnel, replace the OpenAI key master via no-echo prompt, atomically restage its mapped copy, then `up -d --no-deps --force-recreate chatgpt-tunnel` using the saved overlay/context/project/image. Test that a restart can retain the old mount and recreation actually authenticates with the new value. MCP rotation updates the single deployment `MCP_AUTH_TOKEN` and authorized local clients, recreates MCP, proves old bearer 401/new bearer succeeds, and force-recreates the tunnel. Restart alone retains its old environment and must fail authentication without starting the official client. Revoke old real control-plane credentials after successful cutover where supported. Never print either value or compare by emitting a secret hash.

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

Permission/rotation jobs: exercise rootful and rootless Linux mappings, reject unrelated UIDs and misowned mounts, verify source masters unreadable to runtime, and use a PTY for no-echo/interrupt handling. Atomically rotate the synthetic control-plane key and update the shared synthetic MCP token; assert upstream/downstream requests succeed only with the new credentials after recreation and reject old values without exposing them. Scan captured output internally for synthetic canaries, printing only pass/fail. Test disable/rollback with marker state in OpenD/journal volumes and continued authenticated host access.

All jobs use unique Compose projects, explicit synthetic input files, scrubbed environments, removed fixed container names and Docker-assigned available loopback host ports. Keep production's fixed 8000 assertion in isolated config tests; runtime test overrides use random published ports. Do not stop any developer stack to free 8000. Clean only resources carrying the test project identity; never broad prune or production `down`. Update CI path filters for Dockerfile/config/helper/overlay inputs and require runtime tests on affected PRs. Record the actual binary version/digest, image IDs, Docker mode and behavior matrix.

Live VPS deployment, actual OpenAI eligibility/control-plane authentication, full broker account/positions results, ChatGPT web discovery/invocation, and native iPad discovery/invocation remain separate **PENDING** milestones until genuinely authorized and tested. Simulated control-plane success is not live OpenAI acceptance.

### 7. Managed trust boundary and implementation scope

The owner authorized this revision and implementation on 2026-10-07. Continue on `feat/containerize-private-chatgpt-tunnel` and PR #38 with the verified official v0.0.14 binary. The separate development fork experiment is discontinued; its results are not this branch's acceptance evidence.

Trust the authenticated OpenAI HTTPS control plane and controlled Docker host. Production configuration remains `https://api.openai.com`, default certificate verification and exactly `http://moomoo-mcp:8000/mcp`. The image config digest rejects changed configuration; the launcher supplies only HOME, PATH, a validated tunnel ID and the derived ordinary MCP authorization header, excluding inherited proxies, endpoint/configuration overrides, custom CAs and brokerage settings. Preflight retains exact destination checks and redirect refusal. The official client's redirect behavior is unchanged.

The local redirect reproduction deliberately substitutes a redirecting fixture for OpenAI. It establishes an upstream credential-routing weakness, but no malicious redirect or exposure at the actual OpenAI endpoint. Redirection by a trusted endpoint remains a residual risk; proxy filtering does not repair it. Direct-client proxy findings are separate from the manager's protected path. Doctor and redirected MCP authentication omissions remain upstream limitations; authenticated preflight and normal managed forwarding are the acceptance checks.

Replace unconditional release refusal with operational checks: valid explicit immutable-image selection, legacy service inactive/disabled, existing resolved authentication/READ_ONLY validation, then authenticated bounded container startup. Default deployment remains tunnel-free. Automated tests use synthetic credentials, disposable resources and fixture-only destinations. This work performs no production migration, real credential read, live OpenAI call, PR promotion, merge or trading enablement.

### 8. Current acceptance

The retired strict release gate and upstream-client diagnostic matrix are historical investigation, not maintained CI. Preserve their original findings under [the earlier Git commit](https://github.com/evil-sparkle/moomoo-api-mcp/tree/76470318f3d9dbe3eca51ee10902580d50dfad1c/openspec/changes/containerize-private-chatgpt-tunnel). Current acceptance requires:

- **G1 — Official provenance:** retain the reviewed v0.0.14 source/version/archive pin and verify it before execution. Use the unmodified official binary. A future upgrade needs its own provenance and managed-operation review.
- **G2 — Managed operation:** actual image/entrypoint checks pass normal polling/response delivery, authenticated MCP initialize/discovery/forwarding, missing/wrong/conflicting credentials, manager proxy filtering and both credential rotations.
- **G3 — Container and quality checks:** required rootful/rootless checks cover mapping/permissions, network and state isolation, DNS recovery, liveness, signals, mode refusal and scoped disablement. Lint, formatting, types, unit tests, image publication and deployment regressions validate the current implementation. No direct-client matrix/report job is required.
- **G4 — Evidence handoff:** update PR #38 with current scope, tested provenance/results and limitations. Keep VPS/OpenAI/ChatGPT/iPad milestones pending until tested. Repository implementation performs no production deployment, merge or PR promotion.

Explicit immutable selection and startup checks permit operator-controlled enablement under this accepted profile. Local results do not represent hosted CI completion or live acceptance.

### 9. Historical investigation

Remove prior compatibility reports and synthetic result snapshots from the current tree, along with the obsolete reproduction script, 515-case matrix fixture, report generator, report-only tests and their CI job. Their original contents and source remain available in Git commit `7647031`; no additional history folder is tracked. This project maintains no tunnel-client fork. Existing official provenance, authenticated preflight, proxy filtering, READ_ONLY enforcement, credential permissions/rotation and actual managed-container checks remain current deployment controls.

## Risks / Trade-offs

- Rootless mappings, remote daemon paths and host ACL support vary → measured mapping plus actual mounted-file tests; no permissive fallback.
- Official-client conditional redirect/proxy/doctor limitations → preserve evidence, explain accepted trusted-endpoint assumptions and validate the protected managed path. No upstream repair, zero risk or real OpenAI exposure is claimed.
- Ordinary bearer privileges follow deployment mode → stop tunnel before mode changes; deployment guard plus server policy, never annotations as authorization.
- Local MCP briefly interrupts when applying new settings/image/network → documented targeted recreation under the same project and volumes; no promise of zero downtime.
- A runtime manager adds code → keep it limited to startup/liveness/signals; test hangs and do not restart on remote readiness failures alone.
- Rolling product documentation cannot prove owner eligibility or iPad support → keep independent pending milestones.

## Migration Plan

This is an operator migration procedure, not a migration executed by this implementation request. Review managed evidence G1–G4 above and retain the official pin, existing project and volumes. Automated testing uses disposable resources and synthetic credentials.

1. After implementation review, install the reviewed checkout/image inputs and verify default local deployment first. Record non-secret Docker context, Compose project, image ID, network and volume identities. Confirm READ_ONLY and Linux/mapping prerequisites without displaying resolved deployment configuration.
2. Explicitly `systemctl disable --now moomoo-chatgpt-tunnel.service` if installed; verify inactive before starting Compose. Keep its reviewed assets and protected masters for rollback. Never run both mechanisms for one tunnel.
3. Pull the dedicated CI-published image for the reviewed main commit; record its ECR digest and local image ID. Run the no-secret mapping probe and root provisioning helper on the daemon host using that local ID. Provision only runtime key, selected tunnel ID and ordinary bearer. Test readability as the actual runtime identity and denials for unrelated identities.
4. Explicitly select/save the overlay. Apply the brokerage image/settings with `up -d --no-deps moomoo-mcp` as needed, preserving existing project and OpenD/journal mounts. This recreation is necessary for a new Host setting and potentially network attachment; retain prior image/selection for rollback. Verify localhost MCP and state before enabling the tunnel.
5. Start only `chatgpt-tunnel` after preflight, then inspect liveness/readiness from inside it. Run separate full local acceptance only with owner authorization. Record later product milestones independently.
6. Disable using selected Compose `stop chatgpt-tunnel`, optionally `rm -f chatgpt-tunnel`; no `down`, volume deletion or brokerage restart is required just to stop it. Clear saved selection only after removal. Remove only its staging copies when retiring the integration.
7. For the OpenAI key, stop → no-echo master replacement → atomic mapped staging → force-recreate tunnel. For the MCP bearer, stop → update shared deployment token/local clients → recreate MCP → local verification → force-recreate tunnel. Verify new authentication and old rejection; do not trust a restart alone.
8. Roll back by stopping/removing the tunnel first. Leave local MCP and all volumes intact; remove the optional Host setting by targeted brokerage recreation only if desired or restoring the prior image. Before re-enabling legacy systemd, verify the Compose tunnel is absent, legacy credential copies/config point to host loopback, and READ_ONLY preflight passes. Never reset OpenD authorization or journal storage to repair this migration.

## Assumptions and External Prerequisites

Docker Compose must support the repository's existing reset/override syntax; implementation records its tested minimum version. Provisioning needs host root for ownership/ACL operations, while runtime stays non-root and the production daemon stays rootless. An approved immutable slim-image digest and available official v0.0.14 archive are build prerequisites, not claims of a build already performed. Live use requires owner-selected tunnel, limited Read + Use key, permitted outbound OpenAI HTTPS, and eligible linked organization/workspace. The proposal creates no account resources and inspects no real credentials.
