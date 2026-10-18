## MODIFIED Requirements

### Requirement: Server-enforced read-only or paper-only access

The integration SHALL start in READ_ONLY or SIMULATE, or on a REAL backend only with a distinct restricted client credential lacking live-write and gateway-control grants. The MCP service SHALL enforce this mode before gateway
dispatch. Paper writes SHALL require existing account, journal, identity and
recovery gates. The tunnel SHALL NOT receive operator credentials or gain REAL
write or unlock authority.

#### Scenario: Read-only deployment starts

- **WHEN** preflight resolves `MOOMOO_TRADING_MODE` as `READ_ONLY` and the other
  tunnel prerequisites are valid
- **THEN** the tunnel service MAY start
- **AND** authorized account, position, market-data, and health reads MAY be
  forwarded

#### Scenario: Paper-only deployment starts

- **WHEN** preflight proves `MOOMOO_TRADING_MODE=SIMULATE` and the other tunnel
  prerequisites are valid
- **THEN** the tunnel service MAY start and forward authorized real-account reads
  and supported paper mutations
- **AND** paper mutations SHALL require explicit `trd_env="SIMULATE"`, an
  allowlisted verified simulated account and the existing journal admission data
- **AND** real-account, position and market-data reads MAY be forwarded
- **AND** tool annotations and product settings SHALL NOT grant authorization

#### Scenario: Unsafe deployment is detected

- **WHEN** preflight resolves `MOOMOO_TRADING_MODE` as `REAL` without restricted-client proof, or an unusable value
- **THEN** the tunnel service SHALL refuse to start
- **AND** it SHALL NOT alter the MCP deployment to make the check pass

#### Scenario: Mutation is requested through the tunnel

- **WHEN** a caller requests any order mutation in `READ_ONLY`, a REAL order
  mutation or trade unlocking in `SIMULATE`, or trusted operator decisions using the ordinary tunnel bearer
- **THEN** the MCP service SHALL reject the request before any broker write or
  unlock method is dispatched

#### Scenario: Startup gate is not permanent credential scope

- **WHEN** an operator intends to change MCP mode to REAL
- **THEN** the operator SHALL configure a distinct restricted tunnel credential or stop and disable the tunnel first
- **AND** deployment tooling SHALL refuse unknown modes and REAL without restricted-client proof while the tunnel overlay remains selected
- **AND** documentation SHALL state that the ordinary bearer follows the server mode
- **AND** the bearer SHALL NOT be described as permanently read-only: SIMULATE
  grants supported paper-write authority subject to existing execution gates

#### Scenario: Switching between allowed modes preserves paper state

- **WHEN** a selected tunnel deployment switches among READ_ONLY, SIMULATE and safely restricted REAL
- **THEN** managed deployment SHALL retain the paper journal volume and existing OpenD state independently of which journal paths are enabled
- **AND** READ_ONLY SHALL NOT open the journal unless explicit observation is enabled
- **AND** SIMULATE SHALL require explicit paper configuration without automatically
  initializing or replacing a journal to make startup succeed

### Requirement: Gated container startup and bounded recovery

The container SHALL validate authenticated initialize, discovery and check_health proving READ_ONLY, SIMULATE, or safely restricted REAL before launching a client capable of polling or forwarding.

#### Scenario: Tunnel startup and recovery contract

- **WHEN** the optional tunnel starts or relaunches its client
- **THEN** the following detailed obligations SHALL hold:

Transient MCP startup failures SHALL be retried within a bounded deadline with bounded requests and safe diagnostics. Authentication, destination, invalid-result and mode errors SHALL fail closed. Every client relaunch SHALL repeat the gate. Process liveness, tunnel readiness and MCP availability SHALL be reported separately without raw bodies or account data. A local hung client SHALL cause bounded process termination and nonzero container exit; healthcheck status alone SHALL NOT be the restart mechanism.

#### Scenario: Delayed MCP startup

- **WHEN** MCP DNS/reachability is initially unavailable but becomes ready within the startup deadline
- **THEN** the tunnel SHALL start only after authenticated protocol results prove READ_ONLY, SIMULATE, or safely restricted REAL
- **AND** deadline exhaustion SHALL exit unsuccessfully with a safe diagnostic

#### Scenario: Invalid credentials or mode

- **WHEN** preflight receives refused authentication, an invalid MCP result, unsafe REAL access or an unknown mode
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
version and archive integrity.

#### Scenario: Official client acceptance boundaries

- **WHEN** the managed official tunnel client is evaluated for acceptance
- **THEN** the following detailed obligations SHALL hold:

Its managed runtime SHALL use the fixed
`https://api.openai.com` control plane with certificate verification, the approved
private MCP URL, a scrubbed environment and authenticated READ_ONLY, SIMULATE, or safely restricted REAL startup.
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
