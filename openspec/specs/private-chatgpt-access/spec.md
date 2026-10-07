# private-chatgpt-access Specification

## Purpose

Defines a private, optional, read-only path from eligible OpenAI products to the
existing loopback MCP deployment, including security boundaries and staged
acceptance for ChatGPT web and the native iPad application.

## Requirements

### Requirement: Optional outbound-only tunnel

The system SHALL provide an optional official OpenAI Secure MCP Tunnel client as
a separate optional Compose container selected through an explicit overlay. It
SHALL connect outbound to the OpenAI tunnel control plane and through Docker DNS
to exactly `http://moomoo-mcp:8000/mcp` on a user-defined bridge. Enabling it SHALL NOT publish a new inbound port,
change the MCP host publication from loopback, publish OpenD, add native TLS to the
Python server, enable Tailscale Funnel, or require a firewall opening. Its health
and administration endpoints SHALL listen only on its own container loopback
, with no tunnel ports published.

#### Scenario: Tunnel is enabled

- **WHEN** the optional tunnel service is started
- **THEN** it SHALL initiate outbound HTTPS to the OpenAI control plane
- **AND** its only containerized MCP destination SHALL be `http://moomoo-mcp:8000/mcp`
- **AND** no new host/public listener SHALL exist and OpenD SHALL remain container-loopback-only

#### Scenario: Tunnel is absent or stopped

- **WHEN** the optional tunnel service is not installed, disabled, stopped, or
  unable to reach OpenAI
- **THEN** the existing local MCP endpoint and local clients SHALL continue to
  operate independently

#### Scenario: Default deployment needs no tunnel inputs

- **WHEN** the explicit tunnel overlay is not selected
- **THEN** normal deployment SHALL resolve and start without tunnel credentials, tunnel configuration or a host tunnel daemon

### Requirement: Separate least-privilege identities and credentials

The tunnel daemon SHALL run as a dedicated numeric non-root container UID/GID with no
brokerage, trade-unlock, operator-recovery, container-control, or OpenAI
administration credential. It SHALL receive only a tunnel runtime credential,
the selected tunnel identifier, and an ordinary MCP bearer credential. Secret
values SHALL be supplied through explicit Compose environment injection of the limited OpenAI runtime key, tunnel identifier and existing `MCP_AUTH_TOKEN`,
SHALL NOT be stored in tracked configuration or process arguments, and SHALL NOT be included
in diagnostics or support output. The intended tunnel principals SHALL be
limited to the owner-selected Platform organization and ChatGPT workspace.

#### Scenario: Runtime credential scope

- **WHEN** the tunnel service authenticates to the OpenAI control plane
- **THEN** it SHALL use a runtime key authorized for tunnel Read and Use
- **AND** it SHALL NOT use an OpenAI admin key or tunnel Manage permission

#### Scenario: Local MCP credential scope

- **WHEN** the managed client performs normal discovery, its startup initialize
  probe, or a forwarded MCP request against the fixed private MCP deployment
- **THEN** it SHALL supply the ordinary MCP bearer credential only to the
  explicitly approved MCP origin
- **AND** it SHALL never supply `MCP_OPERATOR_TOKEN`, a brokerage credential, or
  a trade-unlock credential

#### Scenario: Reuse the existing deployment token

- **WHEN** the Compose tunnel is enabled
- **THEN** it SHALL receive the same `MCP_AUTH_TOKEN` as the MCP service without requiring a separately provisioned bearer file
- **AND** the launcher SHALL reject missing, blank or invalid header values before starting the official client
- **AND** it SHALL derive `Bearer <token>` for both ordinary and discovery headers through the client's supported environment references
- **AND** the ordinary bearer MAY be visible in the container environment to trusted Docker/host administrators; other brokerage settings and credentials SHALL NOT be injected

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

#### Scenario: Trusted environment credentials

- **WHEN** the optional service is deployed
- **THEN** credentials SHALL come from the deployment environment without separate staging, root provisioning or bearer files
- **AND** visibility to trusted Docker/host administrators through container/process environment SHALL be an explicitly accepted deployment trade-off
- **AND** only the limited tunnel key, tunnel ID and ordinary MCP token SHALL be injected; brokerage/operator credentials SHALL NOT be copied

#### Scenario: Credentials remain out of public channels

- **WHEN** credentials are provisioned, built, injected, rotated or diagnosed
- **THEN** values SHALL NOT enter image layers, build arguments, Compose YAML, process arguments, tracked files or diagnostic output
- **AND** the tunnel SHALL NOT inherit the brokerage environment

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

#### Scenario: Startup gate is not permanent credential scope

- **WHEN** an operator intends to change MCP mode to SIMULATE or REAL
- **THEN** the operator SHALL stop and disable the tunnel first
- **AND** deployment tooling SHALL refuse a non-READ_ONLY deployment while the tunnel overlay remains selected
- **AND** documentation SHALL state that the ordinary bearer does not itself enforce permanently read-only privileges

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
define single-script deployment, independent supervision, environment configuration, credential rotation, diagnostics and rollback, and preserve the existing local deployment throughout. Rollback SHALL
disable and remove only the optional tunnel service;
it SHALL NOT delete or replace OpenD or execution-journal state.

#### Scenario: Tunnel client is installed

- **WHEN** an operator installs the optional client
- **THEN** the binary SHALL come from an official release pinned to a reviewed
  version and verified integrity value
- **AND** the installed version SHALL be checked before service enablement

#### Scenario: Secrets are rotated

- **WHEN** the tunnel runtime key or ordinary MCP bearer credential is rotated
- **THEN** the Compose tunnel SHALL be force-recreated after updating its deployment environment credentials
- **AND** actual authenticated traffic SHALL prove the new credentials are used and old
  credentials rejected without printing either value

#### Scenario: Integration is rolled back

- **WHEN** the operator follows rollback
- **THEN** the tunnel daemon SHALL stop and cease outbound polling
- **AND** the MCP container, local-client behavior, Tailscale administration,
  OpenD state volume, and execution-journal volume SHALL remain intact

#### Scenario: Restart retains the old environment

- **WHEN** a deployment environment credential changes while an existing container still has its previous value
- **THEN** rotation SHALL recreate the affected containers and verify actual authenticated traffic
- **AND** process/container restart alone SHALL NOT count as rotation evidence

#### Scenario: Coordinated ordinary bearer rotation

- **WHEN** the ordinary MCP bearer is rotated
- **THEN** the tunnel SHALL be stopped while the shared deployment token and authorized local clients are updated
- **AND** both MCP and tunnel containers SHALL be recreated to receive the new environment value; a restart alone SHALL NOT count as rotation
- **AND** local authentication SHALL be verified before tunnel recreation and forwarded authentication verified afterward

#### Scenario: Tunnel-only disablement

- **WHEN** the tunnel is disabled or rolled back
- **THEN** operations SHALL target only the optional tunnel service without requiring Compose down or volume deletion
- **AND** any necessary brokerage recreation SHALL retain its project and all persistent volume identities

### Requirement: Gated container startup and bounded recovery

The container SHALL validate authenticated initialize, discovery and check_health proving READ_ONLY before launching a client capable of polling or forwarding. Transient MCP startup failures SHALL be retried within a bounded deadline with bounded requests and safe diagnostics. Authentication, destination, invalid-result and mode errors SHALL fail closed. Every client relaunch SHALL repeat the gate. Process liveness, tunnel readiness and MCP availability SHALL be reported separately without raw bodies or account data. A local hung client SHALL cause bounded process termination and nonzero container exit; healthcheck status alone SHALL NOT be the restart mechanism.

#### Scenario: Delayed MCP startup

- **WHEN** MCP DNS/reachability is initially unavailable but becomes ready within the startup deadline
- **THEN** the tunnel SHALL start only after authenticated protocol results prove READ_ONLY
- **AND** deadline exhaustion SHALL exit unsuccessfully with a safe diagnostic

#### Scenario: Invalid credentials or mode

- **WHEN** preflight receives refused authentication, an invalid MCP result, SIMULATE or REAL
- **THEN** no forwarding client SHALL start and the integration SHALL NOT fall back to anonymous access

#### Scenario: Client hangs while container remains running

- **WHEN** the client ceases responding to bounded local liveness checks
- **THEN** the runtime manager SHALL terminate it and exit unsuccessfully so Docker can restart the tunnel independently

#### Scenario: Remote outage is not local process death

- **WHEN** OpenAI is unavailable but the local client remains live
- **THEN** readiness SHALL report unavailable independently of liveness
- **AND** remote readiness failure alone SHALL NOT restart the brokerage container or trigger continuous client restart loops

#### Scenario: MCP returns at a new Docker address

- **WHEN** the brokerage container is recreated and its IP changes
- **THEN** subsequent requests SHALL recover through service DNS without hard-coded addresses or old MCP session state
- **AND** requests lost during the outage SHALL NOT be replayed as recovery

### Requirement: Managed official-client acceptance

The integration SHALL use an unmodified official release with reviewed source,
version and archive integrity. Its managed runtime SHALL use the fixed
`https://api.openai.com` control plane with certificate verification, the approved
private MCP URL, a scrubbed environment and authenticated READ_ONLY startup.
Acceptance SHALL require passing actual-image tests of normal authenticated
polling, discovery and forwarding, negative authentication, rotation, runtime restrictions,
isolation and recovery. A client fork SHALL NOT be required or shipped.

Prior direct-client redirect, inherited-proxy and doctor findings SHALL retain
their original results in earlier Git commits, without dated reports or a history
folder in the current tree. They
SHALL NOT be relabeled as passing managed-runtime tests or retained as recurring
CI requirements. The owner accepts the documented conditional upstream
limitations within this fixed deployment. The retired diagnostic runners and
their unused fixtures/reporting code SHALL be removed. Current tests SHALL use
synthetic credentials and disposable resources. Live product acceptance SHALL
remain separate from implementation and CI completion.

#### Scenario: Managed runtime passes with an upstream limitation

- **WHEN** the verified official image passes the required managed-runtime checks with a documented historical redirect or proxy limitation
- **THEN** managed acceptance MAY pass with the upstream failure and its conditions explicitly recorded
- **AND** default deployment SHALL remain tunnel-free and explicit selection SHALL still enforce all authentication, mode and isolation checks

#### Scenario: Distinct credentials have distinct coverage

- **WHEN** historical evidence records a control-plane redirect exposing a synthetic OpenAI runtime key in a fixture
- **THEN** that failure SHALL be recorded against the control-plane path
- **AND** MCP bearer discovery and forwarding cases SHALL retain their independently observed status, including UNTESTED where no runtime evidence exists

#### Scenario: Incomplete managed-runtime evidence cannot satisfy acceptance

- **WHEN** a required managed-runtime check fails, is skipped, is inconclusive or has not run
- **THEN** unrelated successful tests SHALL NOT make managed acceptance pass
- **AND** every missing or failing check SHALL remain explicit in the verification evidence

#### Scenario: Required evidence for technical closure

- **WHEN** managed implementation is considered complete
- **THEN** source/release integrity and exact binary/image provenance SHALL be reviewed
- **AND** the managed normal-operation, negative-authentication, proxy-filtering, rotation and container checks SHALL pass on the final official image
- **AND** historical upstream findings SHALL retain their original results and documented limitations without a recurring report job
- **AND** VPS, real OpenAI, ChatGPT web and native iPad milestones SHALL remain pending until each is actually tested

#### Scenario: Retired release-gate tooling is not current CI

- **WHEN** the managed integration workflow runs
- **THEN** it SHALL exercise the current rootful/rootless deployment checks
- **AND** it SHALL NOT run the retired direct-client matrix or generate its diagnostic report
