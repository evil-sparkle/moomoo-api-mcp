# Proposal

## Why

PR #36 made private ChatGPT access depend on a host-installed daemon and systemd unit. An explicitly selected Compose overlay will make this optional path reproducible alongside the existing deployment while preserving the brokerage container and its security boundaries.

## What Changes

- Recommend a separate `chatgpt-tunnel` container using the official pinned client; retain systemd assets only for legacy migration and rollback.
- Connect through Docker DNS to exactly `http://moomoo-mcp:8000/mcp` on a user-defined bridge, with independent namespaces, outbound access, no tunnel port publication, and container-loopback diagnostics.
- Add narrow opt-ins for that preflight destination and server Host, preserving bearer authentication, Origin rejection, stateless transport, annotations and READ_ONLY enforcement.
- Build a small dedicated image, verify the existing release, run a numeric non-root identity with restrictive filesystem/capability settings, and gate forwarding on bounded authenticated startup checks.
- Provision container-readable secret copies with measured rootless/rootful identity mappings while retaining root-only masters. Specify atomic rotation and forced recreation, with behavioral evidence of new credentials being used.
- Integrate disposable container tests into CI and document installation, diagnostics, migration, rotation, disablement and rollback.
- Separate independent synthetic-fixture implementation from mandatory official-client compatibility and release gates. The demonstrated OpenAI runtime-key redirect failure remains a hard release/production-enablement blocker; ordinary MCP bearer paths require separate evidence.

## Capabilities

### New Capabilities

None; extend the existing security and deployment contracts.

### Modified Capabilities

- `private-chatgpt-access`: Compose-first optional runtime, restricted container credentials, startup/recovery and rotation procedures.
- `container-deployment`: distinguish the unchanged two-process brokerage container from the additional optional tunnel container; preserve network and state isolation.
- `transport-sessions`: explicitly opted-in Docker service Host and approved destination without relaxed authentication or Origin protection.
- `configuration`: exact, default-off integration setting rather than configurable wildcard/private-network trust.

## Impact

Expected implementation touches `deploy/tunnel-client/`, a new `docker-compose.chatgpt.yml`, deployment wrappers, preflight and credential helpers, `server.py`/`settings.py`, CI, focused tests and deployment documentation. The design lists concrete paths and verification gates. No gateway redesign, OAuth, public ingress, trading enablement, tool expansion, release upgrade, or live deployment is included.

Baseline: `origin/main` at `cd6bb2d821550d82b90dfba0214e9891a3c319f5` (merged PR #36). The archived `add-private-chatgpt-mcp-access` remains unchanged. PR #37 was approved at `96b82bed`; the separate implementation PR #38 is a blocked draft. This revision authorizes planning changes only and must be presented for review before implementation resumes. The production client pin remains v0.0.14; the feature remains default-off. No merge, VPS deployment, real credentials, real OpenAI traffic, trading enablement or live ChatGPT/iPad acceptance is authorized. See design.md for the current gate matrix and tasks.md for the revised dependency graph. A local build or unrelated test success cannot make the migration ready.
