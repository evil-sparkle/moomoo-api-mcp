# Proposal

## Why

The current MCP deployment is deliberately reachable only from the host, so a
hosted ChatGPT product cannot use it without a new transport path. The repository
needs an optional, outbound-only integration that preserves its authentication,
read-only trading policy, loopback boundaries, and local-client behavior while
making ChatGPT account eligibility and native-iPad support explicit prerequisites.

## What Changes

- Add an optional host-side deployment for the official OpenAI `tunnel-client`,
  pinned and verified independently of the existing OpenD + MCP container.
- Configure the tunnel's final HTTP hop for
  `http://127.0.0.1:8000/mcp`, supplying the ordinary MCP bearer credential for
  startup discovery and runtime calls from a restrictive secret reference.
- Keep tunnel control-plane credentials separate from MCP, brokerage, trade
  unlock, operator-recovery, and OpenAI administration credentials.
- Require the integration deployment to verify that the MCP server is in
  server-enforced `READ_ONLY` mode; tool annotations and any product controls
  remain advisory only.
- Add correct MCP safety annotations and document that no supported ChatGPT
  per-connection tool allowlist was found in the reviewed official guidance.
- Add isolated verification for authentication, MCP initialization, tool
  discovery, health and account reads, mutation refusal, restart/failure
  behavior, topology, and secret handling.
- Add operator procedures for installation, version verification, supervision,
  rotation, diagnostics, preflight, acceptance, and rollback. ChatGPT web and
  native-iPad checks remain owner-operated, separately recorded acceptance
  stages.
- Do not add public ingress, native server TLS, Tailscale Funnel, firewall
  openings, OAuth, or changes to existing local-client and volume behavior.

## Capabilities

### New Capabilities

- `private-chatgpt-access`: Optional Secure MCP Tunnel access, credential and
  authorization boundaries, read-only enforcement, operational lifecycle, and
  staged ChatGPT/web/iPad acceptance.

### Modified Capabilities

- `container-deployment`: Preserve the single supervised OpenD + MCP container,
  host-loopback publication, unpublished OpenD, persistent volumes, and local
  availability when the independently supervised tunnel is added or fails.
- `transport-sessions`: Preserve stateless Streamable HTTP, bearer
  authentication, hostname protection, and restart semantics for requests
  forwarded by the loopback tunnel client.

## Impact

The implementation will add host-service configuration and verification assets,
deployment/runbook documentation, MCP tool metadata, and isolated tests. It does
not require a public listener, Python-server TLS, OAuth, a core server rewrite,
container topology changes, trading enablement, deployment to a live host, or
changes to OpenD and journal volume identities. External setup requires an
eligible OpenAI Platform organization, tunnel runtime key and tunnel ID, the
required tunnel roles and workspace association, ChatGPT developer-mode access,
and independent confirmation that the native iPad app supports the resulting
private app.
