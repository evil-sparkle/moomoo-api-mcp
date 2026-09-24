# Spec Delta

## Purpose

Defines a private, optional, read-only path from eligible OpenAI products to the
existing loopback MCP deployment, including security boundaries and staged
acceptance for ChatGPT web and the native iPad application.

## ADDED Requirements

### Requirement: Optional outbound-only tunnel

The system SHALL provide an optional official OpenAI Secure MCP Tunnel client as
a separately installed and supervised host service. It SHALL connect outbound to
the OpenAI tunnel control plane and locally to
`http://127.0.0.1:8000/mcp`. Enabling it SHALL NOT publish a new inbound port,
change the MCP listener from host loopback, publish OpenD, add native TLS to the
Python server, enable Tailscale Funnel, or require a firewall opening. Its health
and administration endpoints SHALL listen on host loopback only.

#### Scenario: Tunnel is enabled
- **WHEN** the optional tunnel service is started
- **THEN** it SHALL initiate outbound HTTPS to the OpenAI control plane
- **AND** its only MCP destination SHALL be `http://127.0.0.1:8000/mcp`
- **AND** no new public or non-loopback listener SHALL exist

#### Scenario: Tunnel is absent or stopped
- **WHEN** the optional tunnel service is not installed, disabled, stopped, or
  unable to reach OpenAI
- **THEN** the existing local MCP endpoint and local clients SHALL continue to
  operate independently

### Requirement: Separate least-privilege identities and credentials

The tunnel daemon SHALL run as a dedicated unprivileged host identity with no
brokerage, trade-unlock, operator-recovery, container-control, or OpenAI
administration credential. It SHALL receive only a tunnel runtime credential,
the selected tunnel identifier, and an ordinary MCP bearer credential. Secret
values SHALL be supplied through restrictive secret references, SHALL NOT be
stored in tracked configuration or process arguments, and SHALL NOT be included
in diagnostics or support output. The intended tunnel principals SHALL be
limited to the owner-selected Platform organization and ChatGPT workspace.

#### Scenario: Runtime credential scope
- **WHEN** the tunnel service authenticates to the OpenAI control plane
- **THEN** it SHALL use a runtime key authorized for tunnel Read and Use
- **AND** it SHALL NOT use an OpenAI admin key or tunnel Manage permission

#### Scenario: Local MCP credential scope
- **WHEN** the tunnel client performs discovery, its startup initialize probe,
  or a forwarded MCP request
- **THEN** it SHALL supply the ordinary MCP bearer credential only to the
  configured loopback MCP origin
- **AND** it SHALL never supply `MCP_OPERATOR_TOKEN`, a brokerage credential, or
  a trade-unlock credential

#### Scenario: Missing or wrong MCP credential
- **WHEN** discovery or a runtime request reaches the MCP endpoint without the
  configured ordinary bearer credential or with a different credential
- **THEN** the server SHALL reject it as unauthorized
- **AND** no broker service method SHALL be invoked

#### Scenario: Connector supplies a conflicting authorization header
- **WHEN** a connector-forwarded Authorization header overrides the tunnel
  client's local static header with an invalid credential
- **THEN** the MCP server SHALL reject the request
- **AND** the integration SHALL NOT retry without authentication or enable
  unauthenticated HTTP

### Requirement: Server-enforced read-only access

The integration SHALL be startable only when the resolved MCP deployment mode is
`READ_ONLY`, and the MCP service SHALL remain the authority that rejects order
submission, modification, cancellation, trade unlocking, and operator recovery.
Tool annotations and product settings SHALL NOT grant authorization. Enabling
paper or real writes for this integration SHALL require a separately reviewed
change that updates its threat model, credentials, acceptance tests, and
operations documentation.

#### Scenario: Read-only deployment starts
- **WHEN** preflight resolves `MOOMOO_TRADING_MODE` as `READ_ONLY` and the other
  tunnel prerequisites are valid
- **THEN** the tunnel service MAY start
- **AND** authorized account, position, market-data, and health reads MAY be
  forwarded

#### Scenario: Non-read-only deployment is detected
- **WHEN** preflight resolves `MOOMOO_TRADING_MODE` as `SIMULATE`, `REAL`, or an
  unusable value
- **THEN** the tunnel service SHALL refuse to start
- **AND** it SHALL NOT alter the MCP deployment to make the check pass

#### Scenario: Mutation is requested through the tunnel
- **WHEN** a caller requests order placement, modification, cancellation, trade
  unlocking, or operator recovery
- **THEN** the MCP service SHALL reject the request before any broker write or
  unlock method is dispatched

### Requirement: Accurate tool safety metadata

Every MCP tool SHALL declare safety annotations that match its intrinsic
behavior. Read-only tools SHALL advertise `readOnlyHint: true`; mutation and
operator tools SHALL advertise `readOnlyHint: false`; and destructive,
idempotent, and open-world hints SHALL be set according to actual effects.
Annotations SHALL remain advisory and SHALL NOT replace server-side policy.

#### Scenario: Tools are discovered
- **WHEN** an authorized client calls `tools/list`
- **THEN** every returned tool SHALL include accurate safety annotations
- **AND** denied mutation tools SHALL remain denied regardless of their metadata

### Requirement: Layered preflight and acceptance

The integration SHALL define distinct checks for local MCP reachability, tunnel
daemon liveness/readiness, OpenAI account and workspace eligibility, actual
ChatGPT tool discovery and invocation, and native iPad application support. A
passing earlier layer SHALL NOT be reported as proof that a later layer passed.
Credentialed OpenAI and ChatGPT checks SHALL be owner-operated and recorded as
pending unless genuinely executed with authorization.

#### Scenario: Local MCP protocol preflight
- **WHEN** local preflight runs with the configured ordinary bearer credential
- **THEN** it SHALL validate a successful MCP initialize result, `tools/list`,
  `check_health`, and an authorized read-only account/positions request
- **AND** it SHALL validate the MCP results rather than accepting HTTP 200 alone

#### Scenario: Tunnel runtime preflight
- **WHEN** the tunnel daemon is running
- **THEN** its liveness and readiness SHALL be checked separately
- **AND** liveness alone SHALL NOT count as readiness or ChatGPT success

#### Scenario: ChatGPT web milestone
- **WHEN** an eligible owner connects the tunnel from ChatGPT web
- **THEN** acceptance SHALL require actual tool discovery and a successful
  read-only invocation
- **AND** the result SHALL be recorded only as the web milestone

#### Scenario: Native iPad acceptance
- **WHEN** native iPad support has not been independently confirmed and tested
- **THEN** the native-app goal SHALL remain explicitly unresolved
- **AND** local, tunnel, API, or ChatGPT web success SHALL NOT mark it complete

### Requirement: Failure and restart behavior

The integration SHALL expose failures as errors or degraded status and SHALL
never fabricate a successful MCP result, disable authentication, widen network
exposure, or dispatch a mutation as recovery. Recovery SHALL preserve stateless
Streamable HTTP semantics.

#### Scenario: MCP server restarts
- **WHEN** the MCP server becomes unavailable and later returns
- **THEN** tunneled requests during the outage SHALL fail or remain unavailable
- **AND** subsequent requests SHALL recover without relying on an old MCP session

#### Scenario: Tunnel client restarts
- **WHEN** the tunnel daemon restarts
- **THEN** the local MCP service SHALL remain available to existing local clients
- **AND** the daemon SHALL re-establish its outbound tunnel using the same
  reviewed configuration

#### Scenario: OpenD is unavailable
- **WHEN** the MCP endpoint is available but OpenD is unavailable
- **THEN** `check_health` SHALL report the real degraded or disconnected state
- **AND** broker-backed requests SHALL fail without a fabricated success

#### Scenario: OpenAI tunnel connectivity is unavailable
- **WHEN** the daemon cannot reach or authenticate to the tunnel control plane
- **THEN** readiness SHALL fail and remote requests SHALL remain unavailable
- **AND** local MCP authentication and availability SHALL remain unchanged

### Requirement: Reproducible operations and rollback

The operator documentation SHALL identify the official documentation and stable
tunnel-client release consulted, require version and integrity verification,
define service installation and supervision, secret rotation, diagnostics, and
rollback, and preserve the existing local deployment throughout. Rollback SHALL
disable and remove only the optional tunnel service and its non-shared secrets;
it SHALL NOT delete or replace OpenD or execution-journal state.

#### Scenario: Tunnel client is installed
- **WHEN** an operator installs the optional client
- **THEN** the binary SHALL come from an official release pinned to a reviewed
  version and verified integrity value
- **AND** the installed version SHALL be checked before service enablement

#### Scenario: Secrets are rotated
- **WHEN** the tunnel runtime key or ordinary MCP bearer credential is rotated
- **THEN** the service SHALL be restarted with updated restrictive secret
  references
- **AND** old credentials SHALL fail without being printed

#### Scenario: Integration is rolled back
- **WHEN** the operator follows rollback
- **THEN** the tunnel daemon SHALL stop and cease outbound polling
- **AND** the MCP container, local-client behavior, Tailscale administration,
  OpenD state volume, and execution-journal volume SHALL remain intact

