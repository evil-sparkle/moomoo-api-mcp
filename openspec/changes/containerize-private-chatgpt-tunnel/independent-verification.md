# Independent implementation evidence — release BLOCKED

**Current status (2026-09-30): 34/36 tasks complete. Tasks 1.2 and 8.3 remain
incomplete.** PR #38 remains draft. The GitHub workflow-permission problem is
resolved, implementation is pushed, and hosted CI is running. Historical
`compatibility.md`, `verification.md`, the original reproduction and production
release manifest are unchanged.

## Current evidence

[Continuation evidence](continuation-verification.md) records new heads, hosted
runs, expanded matrix results and remaining coverage. At `89cac04`:

- [General CI](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36652723276)
  **PASSED**: lint, formatting, full pytest/type checks, OpenSpec, fresh brokerage
  image build and container smoke.
- [Tunnel workflow](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36652723262)
  **FAILED at the mandatory confinement gate**, as required. Its independent
  **rootful and rootless container jobs both PASSED** on fresh images.
- Both real runtime identities read the intended config/staged secret mounts;
  unrelated/brokerage identities cannot read protected sources, runtime cannot
  read master credentials, and read-only mounts/root filesystem reject writes.
- Actual runtime-key and ordinary-MCP-bearer rotations prove new authenticated
  traffic and old-value rejection separately. Running bind mounts retain the old
  inode; forced recreation is the supported rotation procedure.
- Actual supervisor/OpenD stub accepts brokerage loopback:11111 and rejects
  tunnel bridge:11111. MCP over Docker DNS, Host/Origin/auth negatives, namespace
  separation, private admin listener and no tunnel-published ports all pass.
- A real journal row, OpenD marker and persistent volume identities survive MCP
  recreation. The real tunnel-only disable helper preserves those records and
  authenticated host MCP. Legacy active/enabled/unknown refusal and unsupported
  rollback are separately simulated with deployment tests; no host service changed.
- Both images pass credential-free outbound DNS and verified TLS to example.com
  on the normal bridge. These probes launch no tunnel daemon, mount no secrets,
  and contact no OpenAI endpoint. Client/control-plane fixtures remain internal-only.
- Delayed startup, invalid auth/results/modes, bounded deadline, SIGINT/SIGTERM,
  real child exit/hang, independent Docker restart, control-plane outage/recovery
  and changed-IP DNS recovery pass. No trading mode is enabled.

The expanded matrix additionally tests the real official binary's control-plane
and ordinary-MCP paths with distinct synthetic credentials, loopback HTTP/TLS,
redirects and proxy sinks in a no-egress container. Each case has its own result;
those results do not imply the untested members of a group passed.

## Preserved provenance and earlier local checks

- Official v0.0.14, source `0f870e50a973fa820d4c409000059e181e8d242b`, archive
  SHA-256 `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`.
- Dedicated base `python:3.12.12-slim-bookworm` at
  `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`.
- Earlier local image `sha256:8f37178d25dcffa9983fdc1afec4e92edaeaa8bbd86941f9898937d1c7722651`,
  build-input digest `ea2f41f12c45444ca7f2f142dfdd2b5482da3351ec1455cb4b19743447805aca`.
- Local Docker 29.8.1 rootless runtime 10002 mapped to host 110001, daemon owner
  1001. Helpers measure each daemon; these numbers are not hard-coded configuration.
- Earlier local full pytest: **1334 passed, 1 existing skip, 29 warnings,
  81 subtests passed** (171.45 seconds). Ruff passed; full basedpyright returned
  **0 errors/warnings/notes** using a 1024 MB Node heap. Strict OpenSpec: **27 passed**.
- Local brokerage smoke and paper C01–C04 passed on the existing brokerage image.
  Disk pressure prevented a fresh local brokerage export; that limitation is now
  supplemented by actual fresh hosted builds, not reclassified as a local pass.
- Official doctor: **FAIL, exit 2**, because OAuth metadata probes omit ordinary
  MCP authentication; configuration/source/key/id/target/listener checks pass.
  Doctor is not the authenticated READ_ONLY startup gate.
- Local `/readyz` can stay green during a control-plane outage. It is startup
  readiness, not proof of forwarding. Diagnostics explicitly state that limit.

## Release boundary

CP1/CP2 still fail. Task 1.2 requires every mandatory credential-confinement,
normal-operation, negative-authentication, rotation and final-container case to
pass on the reviewed final official artifact. Task 8.3 also requires explicit
review/release authorization. No patched client, credential proxy, relaxed policy
or pin change is shipped. General CI and independent integration success do not
clear the failed security jobs.

VPS, real OpenAI credentials/traffic, actual account/positions, ChatGPT web and
native iPad acceptance remain **PENDING / unauthorized**. No upstream report was
submitted. No merge, deployment, trading enablement or draft promotion occurred.
