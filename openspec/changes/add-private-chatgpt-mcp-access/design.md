# Design

## Context

See `proposal.md` for motivation. The production Compose stack is one container:
OpenD listens on container loopback at `127.0.0.1:11111`, the MCP process listens
inside the container, and Docker publishes only `127.0.0.1:8000` on the host.
Streamable HTTP is stateless, bearer authenticated, and protected by FastMCP's
Host and Origin checks. ZeroClaw and other local clients already use that endpoint.

The official sources were reviewed on 2026-09-24:

- OpenAI's current, unversioned **Secure MCP Tunnel** guide and **Connect and
  test your plugin** guide.
- `openai/tunnel-client` stable release `v0.0.14`, published 2026-09-01, tag
  commit `0f870e50a973fa820d4c409000059e181e8d242b`.
- The release's `configuration.md`, `architecture.md`, `connectors.md`, and
  `deployment/systemd-vm.md` documentation.
- The repository's newer `master` documentation at
  `cce7a8226654c432ce53c7e05e17a9825a34b6e3` was inspected to detect drift, but
  the implementation will use only behavior present in `v0.0.14`.

Release `v0.0.14` supports file- or environment-backed MCP static headers for
both runtime traffic and discovery/startup probes. Those headers are scoped to
the configured MCP origin; connector-forwarded headers apply later and can
override them. The stable client blocks a connector-supplied `Host`, so the Go
HTTP client derives `127.0.0.1:8000` from the configured URL. It does not list
`Origin` among its blocked forwarded headers. FastMCP rejects every nonempty
Origin unless it exactly matches `allowed_origins`, which is empty today.

OpenAI's current plugin documentation requires accurate tool annotations but
states that they are hints, not authorization. No documented ChatGPT
per-connection tool allowlist was found. Developer mode can expose write tools,
so the server's existing trading policy remains the relevant boundary.

## Goals / Non-Goals

**Goals:**

- Make the private route optional, outbound-only, reproducible, and independently
  supervised.
- Keep both the tunnel control-plane key and the MCP bearer token out of tracked
  files, argv, logs, and OpenAI administration flows.
- Fail closed unless the server itself reports `READ_ONLY` through an
  authenticated MCP `check_health` result.
- Produce local automated evidence for protocol results, authentication,
  read-only dispatch prevention, topology, failure isolation, and restart
  behavior before owner-operated product acceptance.

**Non-Goals:**

- Publishing a plugin, introducing OAuth, enabling trade operations, or granting
  tunnel Manage/admin credentials to the daemon.
- Proving ChatGPT account eligibility, workspace association, or native-iPad
  availability in automated repository tests.
- Making Tailscale part of the product data path or changing ZeroClaw's path.

## Decisions

### 1. Pin the official full tunnel-client release on the host

Install the official Linux amd64 archive for `v0.0.14` and pin its release digest
(`15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`).
The installer will verify the archive before extraction and verify
`tunnel-client --version` afterwards. The full client is selected because its
`doctor`, `/healthz`, `/readyz`, `/metrics`, and loopback admin UI are useful for
operations; the bundled Cloudflare mode will not be enabled.

The files will live under a new `deploy/tunnel-client/` directory: a version and
digest manifest, a non-secret YAML configuration template, a hardened systemd
unit, and an installation/preflight helper. The binary remains a host dependency
and is not committed or added to the MCP image.

Alternative considered: a sidecar or second Compose service. That would give the
tunnel lifecycle access to the container network and alter the carefully tested
one-service topology. A host service can already reach the existing loopback
publication and fails independently.

### 2. Use two local secret references with a dedicated service identity

The systemd unit will use a dedicated unprivileged identity and systemd credential
files. One credential contains only the tunnel runtime API key. The other contains
the complete local header value `Bearer <MCP_AUTH_TOKEN>` so tunnel-client can use
the supported `file:` reference for both `mcp.extra_headers.Authorization` and
`mcp.discovery_extra_headers.Authorization`. The same ordinary MCP secret must
therefore be rotated in the container configuration and the tunnel credential as
one operator procedure.

The daemon receives no `MCP_OPERATOR_TOKEN`, broker login, trade-unlock secret,
Docker socket, OpenAI admin key, or writable access to the repository. The unit
will use systemd hardening, an explicit writable runtime/state directory if the
binary requires one, loopback-only health binding, and a restricted address-family
set. Configuration and credential source files will be root-owned and inaccessible
to other users; systemd exposes credential copies only to the service.

Alternative considered: ask ChatGPT to supply the static MCP bearer. Official
ChatGPT documentation says custom API keys are not a supported client-auth mode,
and tunnel-client already provides a narrower local backend-credential mechanism.
OAuth would be much larger and is unnecessary for this single-owner deployment.

### 3. Keep authentication and READ_ONLY checks in protocol preflight

A standard-library verification helper will speak MCP over the loopback HTTP
endpoint. It will consume the local Authorization header through a private input
channel, never argv, and validate JSON-RPC results for `initialize`, `tools/list`,
`check_health`, and a configured account/positions read. Its startup-safe mode
will require initialize, tool discovery, and a `check_health` result whose
`trading_mode` is exactly `READ_ONLY`; gateway health may be degraded. The full
operator acceptance mode will additionally require the owner-selected account and
positions request to return a valid MCP result.

Missing and wrong credentials will be exercised against the same app with mocked
services, proving authentication rejects before lazy services or broker methods are
created. Mutation calls will be exercised in `READ_ONLY` with isolated trade
contexts and will assert that placement, modification, cancellation, unlock, and
operator recovery dispatch methods remain untouched.

The systemd service will run the startup-safe preflight before tunnel-client. A
server reporting `SIMULATE` or `REAL` prevents the remote path from starting even
though local MCP behavior remains under the server's existing policy.

Alternative considered: infer the mode from an env file. That would require the
integration to parse a protected credential file and could diverge from the
running server. The authenticated health result reports the effective process
mode without exposing secrets.

### 4. Preserve hostname protection and fail closed on Origin

No Host or Origin wildcard will be added. Automated compatibility tests will
exercise tunnel-client's released local proxy/dev fixture or an equivalent
isolated forwarding fixture to prove that the final Host is
`127.0.0.1:8000`. Requests without Origin continue to work; an unexpected
forwarded Origin must receive 403.

Whether the actual ChatGPT tunnel path sends an Origin is not established by the
official product documentation and cannot be proved without an eligible account.
The owner-operated web milestone must record this result. If the product supplies
a stable, documented or observed Origin and the server rejects it, the smallest
secure follow-up is an exact allowlist entry covered by a regression test. A
wildcard, disabling DNS-rebinding protection, stripping arbitrary headers in a
new proxy, or allowing unauthenticated HTTP is not an acceptable workaround.

### 5. Annotate the full tool surface, but rely on policy

All tools will receive explicit MCP `ToolAnnotations`. Pure reads, including
health, account, position, order/deal queries, previews, and market-data reads
that do not auto-subscribe, will use `readOnlyHint: true`. Auto-subscribing quote
and order-book reads, subscription changes, and every trade, lock/unlock, or
operator action will use `readOnlyHint: false`; consequential trading actions
will also be marked destructive where their effects can be difficult to reverse.
The bounded broker/account surface will use `openWorldHint: false`. Tests will
enumerate the registered tools so a future unannotated tool fails the suite.

The first integration still discovers mutation tools because no supported
ChatGPT tool allowlist is documented. `READ_ONLY` policy is tested at the service
dispatch boundary and remains authoritative. Hiding or conditionally registering
tools was rejected because it would introduce a separate surface and risk changing
existing clients.

### 6. Separate automated, tunnel, web, and iPad evidence

The runbook will use a five-layer acceptance record:

1. Local MCP: authentication plus concrete MCP results.
2. Tunnel daemon: `doctor`, liveness, readiness, and reconnect behavior.
3. OpenAI eligibility: tunnel roles, organization/workspace association, and
   ChatGPT developer-mode availability.
4. ChatGPT web: actual discovery plus a read-only tool invocation.
5. Native iPad: the same private app is discoverable and invokes a tool in the
   native app.

Layers 3-5 are owner-operated and remain `PENDING` in repository evidence unless
executed with explicit authorization and recorded. A successful web check never
closes the iPad item.

### 7. Test failure isolation without live credentials

Automated tests will use mock broker services, isolated ASGI/HTTP fixtures, and
temporary process fixtures. They will cover malformed success responses, missing,
wrong, and valid auth, read-only account/position results, mutation refusal, MCP
restart, tunnel-process restart, unavailable OpenD, and unavailable simulated
control-plane connectivity. Static deployment tests will assert loopback bindings,
unit hardening, absence of privileged credentials and public-ingress features,
and unchanged Compose volumes/topology.

The actual OpenAI control plane and ChatGPT will not be contacted in CI. Their
absence is reported as pending product acceptance, not simulated success.

## Risks / Trade-offs

- **A connector Authorization header overrides the locally injected bearer.** →
  Such a request fails 401; do not retry anonymously. Confirm the intended app is
  configured without a conflicting user-auth flow.
- **ChatGPT may forward a nonempty Origin not accepted today.** → Keep the
  default fail-closed behavior and use the exact-origin follow-up described above
  only after real evidence.
- **The daemon holds access to private account reads.** → Limit it to the owner
  workspace, a tunnel runtime key with Read + Use, `READ_ONLY`, a dedicated Unix
  user, loopback target, restrictive credentials, and no container/broker/operator
  secrets.
- **Release behavior may drift.** → Pin version and digest, validate config with
  that binary, and treat upgrades as reviewed dependency changes with repeated
  compatibility and acceptance checks.
- **OpenD unavailability makes the account/positions acceptance step fail.** →
  Keep daemon readiness separate from broker-backed acceptance and report the real
  gateway state rather than weakening checks.
- **Tool annotations expose that write tools exist.** → They improve client
  safety behavior but cannot authorize calls; server policy continues to deny them.

## Migration Plan

1. Merge repository assets and automated tests without installing or starting a
   tunnel service.
2. Owner verifies account/workspace prerequisites, creates or selects the tunnel,
   and provisions only the runtime key, tunnel ID, and local MCP header credential.
3. Install the pinned verified binary, configuration, credentials, and systemd
   unit; run `doctor` and the local startup-safe preflight before enablement.
4. Start the independent service and verify `/healthz` and `/readyz`, then run the
   full local account/positions acceptance check.
5. Perform and record ChatGPT web acceptance, followed separately by native-iPad
   acceptance if that surface supports the private app.
6. To roll back, disable the tunnel unit, remove its binary/config/credentials
   after preserving diagnostics, and leave Compose, Tailscale, OpenD state, the
   execution journal, and local clients untouched.
