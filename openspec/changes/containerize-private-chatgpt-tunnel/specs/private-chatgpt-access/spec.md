# Spec Delta

## MODIFIED Requirements

### Requirement: Optional outbound-only tunnel

The system SHALL provide an optional official OpenAI Secure MCP Tunnel client as
a separate optional Compose container selected through an explicit overlay. It
SHALL connect outbound to the OpenAI tunnel control plane and through Docker DNS
to exactly `http://moomoo-mcp:8000/mcp` on a user-defined bridge. Legacy systemd
assets SHALL remain available only for migration or rollback using
`http://127.0.0.1:8000/mcp`; both mechanisms SHALL NOT run for the same tunnel. Enabling it SHALL NOT publish a new inbound port,
change the MCP host publication from loopback, publish OpenD, add native TLS to the
Python server, enable Tailscale Funnel, or require a firewall opening. Its health
and administration endpoints SHALL listen only on its own container loopback
(or host loopback for the legacy service), with no tunnel ports published.

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

#### Scenario: Legacy migration avoids competing consumers

- **WHEN** the Compose tunnel replaces the legacy service
- **THEN** the legacy unit SHALL be stopped and disabled before Compose starts the tunnel
- **AND** rollback SHALL stop the Compose tunnel before re-enabling the legacy unit

### Requirement: Separate least-privilege identities and credentials

The tunnel daemon SHALL run as a dedicated numeric non-root container UID/GID (or dedicated unprivileged host identity for legacy rollback) with no
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
  explicitly approved MCP origin
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

#### Scenario: Actual runtime identity reads mounted files

- **WHEN** container secrets are provisioned for the selected Docker context
- **THEN** host ownership SHALL be derived from a verified runtime UID/GID mapping
- **AND** the actual non-root image process SHALL read the intended config and secret files through read-only mounts
- **AND** file-backed secret permission overrides SHALL NOT be assumed to change host ownership

#### Scenario: Protected master and staged copies

- **WHEN** an unrelated host identity or the brokerage runtime identity attempts to read tunnel secret sources
- **THEN** access SHALL be denied
- **AND** root-only master files SHALL remain root-owned mode 0600 and unavailable to the tunnel runtime
- **AND** staged copies SHALL be readable only by the intended mapped runtime identity apart from trusted host/Docker administrators
- **AND** unsafe mappings or ownership SHALL fail closed without world-readable files or a root tunnel daemon

#### Scenario: Credentials remain out of public channels

- **WHEN** credentials are provisioned, built, mounted, rotated or diagnosed
- **THEN** values SHALL NOT enter image layers, build arguments, Compose YAML, process arguments, tracked files or diagnostic output
- **AND** provisioning SHALL preserve no-echo input, terminal restoration, restrictive ownership and atomic-write protections
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

### Requirement: Reproducible operations and rollback

The operator documentation SHALL identify the official documentation and stable
tunnel-client release consulted, require version and integrity verification,
recommend Compose installation and independent supervision, retain legacy migration/rollback instructions, and define secret rotation, diagnostics, and
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
- **THEN** the Compose tunnel SHALL be force-recreated after atomic replacement of its
  restrictive staged secret files, or the legacy service restarted when that path is selected
- **AND** actual authenticated traffic SHALL prove the new credentials are used and old
  credentials rejected without printing either value

#### Scenario: Integration is rolled back

- **WHEN** the operator follows rollback
- **THEN** the tunnel daemon SHALL stop and cease outbound polling
- **AND** the MCP container, local-client behavior, Tailscale administration,
  OpenD state volume, and execution-journal volume SHALL remain intact

#### Scenario: Atomic replacement does not rely on stale bind mounts

- **WHEN** a host secret is atomically replaced while its previous inode is mounted
- **THEN** the rotation procedure SHALL recreate the tunnel container to remount the replacement
- **AND** a process or container restart alone SHALL NOT be considered rotation evidence

#### Scenario: Coordinated ordinary bearer rotation

- **WHEN** the ordinary MCP bearer is rotated
- **THEN** the tunnel SHALL be stopped while the server, authorized local clients and staged tunnel header are updated
- **AND** local authentication SHALL be verified before tunnel recreation and forwarded authentication verified afterward

#### Scenario: Tunnel-only disablement

- **WHEN** the tunnel is disabled or rolled back
- **THEN** operations SHALL target only the optional tunnel service without requiring Compose down or volume deletion
- **AND** any necessary brokerage recreation SHALL retain its project and all persistent volume identities

## ADDED Requirements

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
