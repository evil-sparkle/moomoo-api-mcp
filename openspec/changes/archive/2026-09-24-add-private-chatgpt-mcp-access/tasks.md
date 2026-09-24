# Tasks

## 1. Tool safety metadata

- [x] 1.1 Add explicit `ToolAnnotations` to every registered account, market-data,
  system, and trading tool; verify a tool-list test proves all tools carry the
  expected read-only, destructive, idempotent, and open-world hints.
- [x] 1.2 Classify subscription, order, lock/unlock, and operator actions as
  mutations while keeping pure queries and previews read-only; verify existing
  tool tests and the new annotation matrix pass without changing tool names,
  schemas, or ZeroClaw-visible behavior.
- [x] 1.3 Document that annotations and ChatGPT UI behavior are advisory and that
  no supported per-connection ChatGPT tool allowlist was found; verify the
  runbook points to server-enforced `READ_ONLY` as the authorization boundary.

## 2. Local MCP preflight and authorization evidence

- [x] 2.1 Implement a standard-library local preflight helper that consumes the
  Authorization header through a private channel and validates JSON-RPC results
  for initialize, `tools/list`, `check_health`, and optional account/positions
  calls; verify unit tests reject HTTP 200 with malformed, error, mismatched-id,
  or wrong-tool results.
- [x] 2.2 Add startup-safe preflight mode that requires an authenticated
  `check_health` result with `trading_mode=READ_ONLY` while tolerating a reported
  unavailable OpenD; verify SIMULATE, REAL, missing mode, unauthorized, and
  unreachable endpoints all fail closed without printing credentials or response
  bodies that may contain private account data.
- [x] 2.3 Add isolated HTTP/ASGI tests for missing, wrong, valid, and conflicting
  bearer credentials; verify rejected requests do not construct lazy services or
  call any mocked broker method and valid requests return real MCP results.
- [x] 2.4 Add mocked read-only acceptance coverage for `check_health`, account
  discovery, and positions, plus mutation attempts for placement, modification,
  cancellation, trade unlocking, and operator recovery; verify every mutation is
  rejected before broker write/unlock dispatch.
- [x] 2.5 Document local preflight inputs, safe output, account-id handling, and
  the difference between startup-safe and full account/positions acceptance;
  verify every documented example runs against an isolated fixture.

## 3. Optional tunnel-client host service

- [x] 3.1 Add a reviewed release manifest and installer for official
  tunnel-client `v0.0.14` Linux amd64, pinning SHA-256
  `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`;
  verify tests reject a digest mismatch and accept the expected archive/version
  without installing or starting a service during the test.
- [x] 3.2 Add a non-secret tunnel-client YAML template targeting only
  `http://127.0.0.1:8000/mcp`, with loopback health/admin binding and file-backed
  Authorization for both runtime and discovery probes; verify a configuration
  contract test finds no literal secret, public listener, Cloudflare companion,
  proxy target, OAuth expansion, or alternate MCP origin.
- [x] 3.3 Add a hardened systemd unit for a dedicated unprivileged identity using
  separate runtime-key and MCP-header credentials, startup-safe preflight,
  independent restart policy, and no Docker/container/broker/operator/admin
  access; verify static unit tests cover credential paths, argv, permissions,
  address families, writable paths, loopback diagnostics, and failure isolation.
- [x] 3.4 Add forwarding compatibility tests that prove the configured final Host
  is loopback, absent Origin succeeds, and an unexpected Origin fails with no
  wildcard or disabled DNS-rebinding protection; verify connector Authorization
  override produces 401 rather than an unauthenticated retry.
- [x] 3.5 Extend Compose topology tests to assert the tunnel assets do not add a
  service, port, network, or volume and that OpenD remains unpublished while MCP
  remains `127.0.0.1:8000`; verify all existing topology, supervisor, paper-volume,
  and local-client tests still pass.

## 4. Restart and degraded-state behavior

- [x] 4.1 Add isolated process tests for an MCP outage and restart; verify calls
  fail during the outage, recover after readiness without an old session id, and
  never produce a fabricated success or auth downgrade.
- [x] 4.2 Add isolated process tests for tunnel-daemon exit, restart, and simulated
  control-plane unavailability; verify readiness fails appropriately while the
  loopback MCP endpoint and local-client path remain available and authenticated.
- [x] 4.3 Extend unavailable-OpenD coverage through the new preflight path; verify
  `check_health` reports disconnected/degraded, startup-safe mode still proves
  READ_ONLY, full account/positions acceptance fails honestly, and no restart path
  dispatches a write.
- [x] 4.4 Document the restart matrix and diagnostic sequence for MCP, tunnel,
  OpenD, and control-plane failures; verify each documented status maps to an
  automated fixture or is explicitly marked owner-operated.

## 5. Installation, acceptance, rotation, and rollback runbook

- [x] 5.1 Add an operator runbook recording the documentation date, official
  source URLs, stable release/tag commit, required outbound destination, intended
  Platform organization and ChatGPT workspace associations, and least-privilege
  roles; verify it never asks for or names an admin, broker, unlock, or operator
  token as a daemon input.
- [x] 5.2 Document version/integrity verification, dedicated-user installation,
  restrictive credential creation, `doctor`, service supervision, liveness versus
  readiness, diagnostics, and coordinated runtime/MCP secret rotation; verify
  commands use placeholders or secret references and never expose credentials in
  argv or output. Behavioral regressions prove the service identity can read the
  non-secret YAML but not credential sources, and that credential entry does not
  echo on success or interruption and leaves root-only destination modes.
- [x] 5.3 Add a staged acceptance checklist that separately records local MCP,
  tunnel runtime, OpenAI eligibility/workspace association, ChatGPT web discovery
  and read-only invocation, and native-iPad discovery/invocation; verify
  credentialed product and iPad stages default to `PENDING` and no earlier stage
  can mark them complete.
- [x] 5.4 Document rollback that disables and removes only the optional tunnel
  service and its credentials; verify the procedure contains no Tailscale,
  firewall, Compose-volume, OpenD-state, execution-journal, merge, archive, or live
  deployment mutation.

## 6. Integration verification and security review

- [x] 6.1 Run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run basedpyright`, and the complete `uv run pytest`; record passing evidence
  or resolve every failure attributable to this change.
- [x] 6.2 Run the repository container smoke test and paper-container test when a
  Docker daemon is available; otherwise record the exact environmental blocker
  and verify their topology and persistence contracts through the corresponding
  isolated tests.
- [x] 6.3 Run strict OpenSpec validation for all specs and changes with the
  repository-pinned CLI version; verify zero validation errors and preserve this
  change unarchived.
- [x] 6.4 Review the final diff for authentication bypass, secret leakage, public
  or wildcard exposure, accidental trading enablement, OpenD/journal volume
  changes, ZeroClaw regression, and restart coupling; verify searches and focused
  tests find no violation and leave credentialed web/iPad checks pending unless
  the owner actually ran them.

## Verification evidence (2026-09-24)

- Ruff lint and format checks passed. Basedpyright passed with zero findings;
  this host required `NODE_OPTIONS=--max-old-space-size=2048` because its default
  512 MiB Node heap exited out of memory.
- The complete test suite passed with 1,239 tests passed and one existing
  environment-dependent skip after review fixes.
- The official v0.0.14 archive passed pinned digest and binary-version
  verification in `--verify-only` mode.
- The container image built. The full smoke stack could not start because an
  existing local `moomoo-api-mcp` container already owned protected host port
  `127.0.0.1:8000`; that running service was left untouched. Compose topology,
  supervision, and restart contracts passed in the complete isolated suite.
- The disposable network-isolated paper-container check passed C01-C04 against
  the newly built image.
- A disposable network-isolated container check passed with a real unprivileged
  UID/GID: the service identity read the installed non-secret YAML and could not
  read either root-owned credential source. Pseudo-terminal regressions also
  proved synthetic credential input was never echoed and terminal echo was
  restored after interruption.
- OpenSpec CLI 1.13.1 strict validation passed all 27 specs and changes with zero
  failures. This change remains unarchived.
- No credentialed OpenAI control-plane, ChatGPT web, or native-iPad acceptance
  was run. Those owner-operated stages remain `PENDING` in the runbook.
