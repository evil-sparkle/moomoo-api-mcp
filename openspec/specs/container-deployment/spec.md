# container-deployment Specification

## Purpose

Provides secure, isolated container deployment specifications for running the OpenD gateway alongside the moomoo-api-mcp server using Docker Compose, enforcing network isolation, credential compartmentalization, and persistent session storage.

## Requirements

### Requirement: Isolated OpenD Gateway Network

The system SHALL run the OpenD gateway and the MCP server inside a single
container in which the OpenD API listens on the container's loopback interface
only. The OpenD API port SHALL NOT be published to the host and SHALL NOT be
reachable from any other container or network.

#### Scenario: OpenD port unreachable from host

- **GIVEN** the deployment is running
- **WHEN** a process on the host machine attempts to connect to port 11111 on localhost
- **THEN** the connection SHALL be refused because the port is published nowhere

#### Scenario: OpenD port unreachable from another container

- **GIVEN** the deployment is running
- **WHEN** a process in any other container attempts to connect to port 11111 on the
  deployment's container address
- **THEN** the connection SHALL be refused because the gateway listens on loopback only

#### Scenario: MCP server reaches OpenD internally

- **GIVEN** the single-container deployment is running
- **AND** OpenD is accepting connections
- **WHEN** the MCP server connects to `127.0.0.1:11111`
- **THEN** the connection SHALL use container loopback
- **AND** OpenD SHALL NOT require a non-loopback listener

#### Scenario: MCP endpoint published to host loopback only

- **GIVEN** the deployment is running
- **WHEN** the published ports are inspected
- **THEN** the MCP HTTP endpoint SHALL be published on `127.0.0.1:8000` and no
  other port SHALL be published

### Requirement: Credential Compartmentalization

Trading credentials SHALL be injected solely into the MCP server container and SHALL NOT be present in the host agent environment.

#### Scenario: Host agent cannot inspect container trade secrets
- **GIVEN** `MOOMOO_TRADE_PASSWORD_MD5` is configured in the container stack
- **WHEN** an agent executes commands in the host shell
- **THEN** the host environment variables SHALL NOT contain `MOOMOO_TRADE_PASSWORD_MD5`

### Requirement: Session State Persistence

The deployment SHALL persist OpenD session tokens and device authorization state
across container restarts and container recreation using a persistent volume,
mounted at the path and owned by the user id that the gateway already writes as,
so an existing volume is read rather than re-authorized.

#### Scenario: OpenD restarts without requiring repeated SMS 2FA

- **GIVEN** OpenD has completed initial login and device verification
- **WHEN** the container is stopped and restarted, or recreated from a new image
- **THEN** OpenD SHALL read existing session tokens from the persistent volume
- **AND** resume serving without prompting for a new device authorization code

#### Scenario: Existing volume survives the move to a single container

- **GIVEN** a volume written by the previous two-container deployment
- **WHEN** the single-container deployment mounts it
- **THEN** the mount path and owning user id SHALL be unchanged
- **AND** OpenD SHALL NOT request device authorization again

### Requirement: Binary Download Integrity Verification

The image build SHALL verify the downloaded OpenD archive against the
reviewed SHA-256 pin for the selected OpenD release. The pin is recorded in the
version-controlled build configuration next to that release's version, tag and
download URL; today that is the `OPEND_SHA256` build arg. Verification SHALL
occur before extraction, and a mismatch SHALL fail the build, whichever source
served the archive. The expected value SHALL be a pin reviewed into version
control, never a value derived during the build from the downloaded bytes or
fetched from the download source. The build configuration is the single source
of truth for the digest, and this specification deliberately does not restate
it.

#### Scenario: Verify download checksum matches pinned hash

- **GIVEN** an OpenD archive downloaded during the image build
- **WHEN** the build computes the archive's SHA-256 digest
- **THEN** the build SHALL proceed to extraction only if the digest equals the
  reviewed pin for the selected release
- **AND** otherwise SHALL fail with an error, without extracting the archive or
  copying any of it into the image

#### Scenario: Verification applies to every download source

- **GIVEN** the primary download source is unreachable and the build fetches the
  archive from the fallback source
- **WHEN** the fallback download completes
- **THEN** the archive SHALL be verified against the same reviewed pin before
  extraction

#### Scenario: Expected digest comes from reviewed configuration

- **GIVEN** an image built by this repository's Compose files or CI workflow
- **WHEN** the build verifies the archive
- **THEN** the expected digest SHALL be the value committed in the build
  configuration
- **AND** it SHALL NOT be computed from the downloaded archive or retrieved from
  the download source at build time

#### Scenario: A version bump without a new pin fails closed

- **GIVEN** the OpenD version pin is changed to a new release
- **AND** the checksum pin still holds the previous release's digest
- **WHEN** the image is built
- **THEN** verification SHALL fail and the build SHALL stop

### Requirement: Non-Root Container Execution

Every process in the deployment SHALL run as an unprivileged, non-root user.

#### Scenario: OpenD gateway process runs as non-root

- **GIVEN** the container is running
- **WHEN** checking the execution user of the `OpenD` process
- **THEN** the effective UID SHALL NOT be 0 (root)

#### Scenario: MCP server process runs as non-root

- **GIVEN** the container is running
- **WHEN** checking the execution user of the Python MCP server process
- **THEN** the effective UID SHALL NOT be 0 (root)

#### Scenario: Supervisor runs as non-root

- **GIVEN** the container is running
- **WHEN** checking the execution user of PID 1
- **THEN** the effective UID SHALL NOT be 0 (root)

### Requirement: Paired Process Supervision

A supervisor process SHALL run as the container's PID 1 and own both the OpenD
gateway and the MCP server, implementing an explicit recovery policy rather than
leaving a dead child invisible to the container runtime's restart policy. No
other init SHALL be inserted in front of it. The supervisor SHALL reap
terminated children, including orphans reparented onto it, and SHALL forward
stop signals to both processes.

#### Scenario: Gateway process death does not remove the MCP endpoint

- **GIVEN** a client holds a working connection to the MCP endpoint
- **WHEN** the OpenD process exits unexpectedly
- **THEN** the supervisor SHALL restart OpenD in place
- **AND** the MCP endpoint SHALL remain reachable throughout, with no
  reconnection or re-initialization required of the client
- **AND** requests that do not need a healthy gateway SHALL continue to work
- **AND** requests that do need one SHALL fail with a bounded connect timeout
  rather than hanging, until the gateway answers again
- **AND** health SHALL report `disconnected` or `degraded` until it does

#### Scenario: Gateway that cannot be recovered takes the container down

- **GIVEN** the OpenD process has exited and been restarted up to the configured bound
- **WHEN** it exits again within the configured window
- **THEN** the supervisor SHALL stop the MCP server cleanly
- **AND** exit non-zero so the container runtime's restart policy restarts
  the container with fresh processes

#### Scenario: MCP process death takes the container down

- **GIVEN** the deployment is running
- **WHEN** the MCP server process exits unexpectedly
- **THEN** the supervisor SHALL stop the OpenD process cleanly
- **AND** exit non-zero so the container runtime's restart policy restarts
  the container with fresh processes

#### Scenario: A degraded broker connection is not a restart trigger

- **GIVEN** the OpenD process is running but its connection to the broker is
  unavailable
- **WHEN** health probes report `disconnected` or `degraded`
- **THEN** the supervisor SHALL NOT restart either process
- **AND** the deployment SHALL remain reachable for diagnostics while the gateway
  reconnects

#### Scenario: Operator stop terminates both processes

- **GIVEN** the deployment is running
- **WHEN** the container receives SIGTERM
- **THEN** the supervisor SHALL forward it to both processes
- **AND** wait a bounded period before escalating to SIGKILL
- **AND** exit without leaving either process running

#### Scenario: A gateway that cannot be started does not take the server down

- **GIVEN** no usable gateway login is configured
- **WHEN** the container starts
- **THEN** the supervisor SHALL log why the gateway cannot be started
- **AND** SHALL start the MCP server anyway
- **AND** the MCP endpoint SHALL answer health requests reporting the gateway
  as unavailable, rather than the container exiting and restarting in a loop

#### Scenario: A malformed supervision setting stops the container

- **GIVEN** a supervision tunable is set to a value the policy cannot run under,
  such as a non-numeric, non-finite, negative or zero interval
- **WHEN** the container starts
- **THEN** the supervisor SHALL refuse to start and exit non-zero naming the
  setting, rather than silently substituting a default nobody chose

#### Scenario: Credentials do not reach the container log

- **GIVEN** the gateway is configured with a trade or login credential
- **WHEN** the supervisor logs the command line it started
- **THEN** the credential SHALL be redacted

#### Scenario: MCP start does not wait on the gateway

- **GIVEN** the container has just started
- **WHEN** the OpenD process has not yet completed its login
- **THEN** the MCP server SHALL already be accepting requests
- **AND** SHALL answer health requests reporting the gateway as unavailable

### Requirement: Recovery From a Gateway Restart

When the OpenD gateway process exits and is restarted in place, within the
retry budget of the deployment's supervision policy, the MCP server process
SHALL keep running, and gateway access SHALL recover without restarting the MCP
server and without any change to client configuration once the gateway is
reachable and logged in again. A gateway failure that exhausts that budget MAY
instead stop the MCP server and exit the supervisor non-zero, as
`Paired Process Supervision` specifies, so that the container runtime's restart
policy restarts the container with fresh supervisor, OpenD and MCP processes.
That case stops both processes, as do an operator restart and a recreation or
redeploy of the container, and it is covered by
`Recovery From an MCP Server Restart`, not by this requirement. Recovery covers
access to the gateway. It does not guarantee that requests made while the
gateway is away succeed; see `Restart Recovery Does Not Replay Trading
Commands`.

#### Scenario: MCP server outlives a gateway restart

- **GIVEN** the deployment is running and a client has made requests
- **WHEN** the OpenD gateway process exits and is restarted in place, within the
  supervisor's configured retry budget
- **THEN** the MCP server process SHALL NOT be restarted as a consequence
- **AND** the MCP endpoint SHALL continue to answer requests throughout

#### Scenario: An exhausted retry budget falls back to a container restart

- **GIVEN** the OpenD gateway process has already been restarted in place as
  many times as the supervisor's retry budget allows within its window
- **WHEN** it exits again within that window
- **THEN** the supervisor MAY stop the MCP server process and exit non-zero,
  as `Paired Process Supervision` specifies
- **AND** the container runtime's restart policy SHALL restart the container,
  starting fresh supervisor, OpenD and MCP processes
- **AND** recovery from that point SHALL be as specified by
  `Recovery From an MCP Server Restart`

#### Scenario: Gateway access resumes without operator action

- **GIVEN** the OpenD gateway process has restarted
- **WHEN** the gateway is reachable and has completed its login
- **THEN** the MCP server's existing gateway connections SHALL reconnect on
  their own
- **AND** subsequent gateway-backed requests from the same client, with the same
  configuration, SHALL succeed

#### Scenario: Gateway absence is reported rather than hidden

- **GIVEN** the OpenD gateway process is restarting or not yet logged in
- **WHEN** a client calls `check_health`
- **THEN** it SHALL report `disconnected` or `degraded` until the gateway answers

### Requirement: Recovery From an MCP Server Restart

When the MCP server process restarts, subsequent authenticated requests SHALL
succeed once the server is serving again, and no session state the client
held from before the restart SHALL prevent them. Where the gateway restarts
together with the MCP server, as it does whenever the container is restarted,
recreated or redeployed, gateway-backed requests SHALL also require
the gateway to be reachable and logged in again.

#### Scenario: First request after restart needs no re-initialization

- **GIVEN** a client made authenticated requests before the MCP server restarted
- **WHEN** the server is serving again and the client sends its next request
  without re-initializing
- **THEN** the request SHALL be processed rather than rejected for an unknown or
  expired session

#### Scenario: Gateway connections are reopened by demand

- **GIVEN** the MCP server has restarted and holds no gateway connections
- **WHEN** it serves its first request
- **THEN** it SHALL open its gateway connections for the process
- **AND** requests SHALL NOT fail because a connection from the previous process
  is missing

### Requirement: Restart Recovery Does Not Replay Trading Commands

Restart recovery SHALL be limited to transport and connection state. A request
that overlaps downtime MAY fail, and recovery SHALL NOT retry it. Reconnection
SHALL NOT resubmit any order placement, modification or cancellation. Recovering
the transport SHALL NOT be treated as establishing whether an earlier trading
command reached the broker.

#### Scenario: A request overlapping downtime may fail

- **GIVEN** either process is restarting
- **WHEN** a client request is in flight or arrives during the outage
- **THEN** that request MAY fail with an error
- **AND** the server SHALL NOT retry it on the client's behalf

#### Scenario: SDK reconnect replays connection state only

- **GIVEN** the SDK re-establishes a gateway connection after a drop
- **WHEN** its post-reconnect work runs
- **THEN** it MAY restore quote subscriptions and the trade context's most recent
  successful unlock state
- **AND** the server SHALL NOT replay any order placement, modification or
  cancellation

#### Scenario: A lost order response is not treated as a result

- **GIVEN** an order placement, modification or cancellation was sent and its
  response was lost to a restart
- **WHEN** the transport recovers
- **THEN** the server SHALL NOT resubmit the command
- **AND** the error returned for that request SHALL NOT be relied on as evidence
  that the broker rejected it; the outcome is established only by querying
  orders or deals

### Requirement: Deployment Verification

Deployment verification SHALL be performed by a verifier that reads its
authentication token from the configuration Docker Compose resolves for the
MCP service, and SHALL NOT interpret dotenv files itself. The token SHALL be
delivered to the HTTP client through a private channel, and SHALL NOT appear
in process arguments, in files created for the probe, or in printed output.
Verified SHALL mean the MCP endpoint accepted the configured authentication
and returned a valid `initialize` result; it SHALL NOT mean broker login or
trading readiness.

#### Scenario: The verifier reads the resolved service environment

- **GIVEN** the deployment's env files and compose files are in place
- **WHEN** the verifier resolves the authentication token
- **THEN** it SHALL read the MCP service's environment from the
  configuration Compose resolves with the same Docker context, env files and
  compose files that start the container
- **AND** it SHALL NOT parse the env files itself
- **AND** it SHALL use the resolved value unchanged, except that Compose's
  own output escaping — a literal `$` printed as `$$` — is decoded back to the
  value the container received

#### Scenario: An empty token means no authentication

- **GIVEN** Compose resolves `MCP_AUTH_TOKEN` for the service as an explicit
  empty string
- **WHEN** the verifier sends the probe
- **THEN** it SHALL send no Authorization header

#### Scenario: A configuration the verifier cannot use is an error, not a guess

- **GIVEN** Compose cannot resolve the configuration, resolves no token for
  the service, or resolves a value an HTTP Authorization header cannot carry
- **WHEN** the verifier resolves the token
- **THEN** it SHALL fail with a configuration error naming what is wrong
- **AND** it SHALL NOT fall back to reading env files itself
- **AND** it SHALL NOT echo the resolved value

#### Scenario: The token never crosses a visible channel

- **GIVEN** Compose resolves a nonempty token
- **WHEN** the verifier sends the probe
- **THEN** the token SHALL reach the HTTP client through standard input
- **AND** the token SHALL NOT appear in any process arguments
- **AND** no file SHALL be created to hold it
- **AND** the resolved configuration, request headers and response bodies
  SHALL NOT be printed

#### Scenario: Verified means an accepted initialize

- **GIVEN** the deployment has started
- **WHEN** verification runs
- **THEN** the probe SHALL be an MCP `initialize` request authenticated with
  the resolved token
- **AND** a JSON-RPC success response carrying a correctly shaped initialize
  result with the matching request id — as JSON or as an SSE-framed stream —
  SHALL verify
- **AND** an HTTP 200 response alone SHALL NOT verify

#### Scenario: An endpoint that is not answering yet is retried

- **GIVEN** the deployment has just started and the endpoint answers with no
  response or a 5xx status
- **WHEN** verification runs
- **THEN** the probe SHALL be retried within the verification deadline
  measured as elapsed time
- **AND** each attempt SHALL be bounded by the time remaining in the deadline
- **AND** a deadline of zero SHALL mean a single attempt with no retries

#### Scenario: A refused probe fails the deployment immediately

- **GIVEN** the endpoint answers the probe with 401 or 403
- **WHEN** verification runs
- **THEN** the deployment SHALL fail without further retries
- **AND** the failure SHALL diagnose the authentication, naming
  `MCP_AUTH_TOKEN` as the configuration to check

#### Scenario: An invalid MCP response fails the deployment immediately

- **GIVEN** the endpoint answers HTTP 200 with a body that is not a valid
  initialize result
- **WHEN** verification runs
- **THEN** the deployment SHALL fail without further retries
- **AND** the failure SHALL suggest that the verification URL does not point
  at the MCP endpoint

#### Scenario: Initialize fields are validated where the lifecycle puts them

- **GIVEN** the endpoint answers HTTP 200 with a JSON or SSE-framed response
- **WHEN** the response is validated
- **THEN** `protocolVersion`, `capabilities` and `serverInfo` SHALL be
  required inside the result object with appropriate types
- **AND** `protocolVersion` SHALL be required to be a non-empty string
- **AND** the same field names appearing outside a result object SHALL NOT
  pass validation

#### Scenario: A partial transfer never verifies

- **GIVEN** the endpoint answers HTTP 200 and the captured response content
  is a complete, valid initialize result
- **AND** the HTTP client did not complete the transfer — a nonzero exit
  such as a partial transfer, a timeout, or signal termination
- **WHEN** the attempt is classified
- **THEN** it SHALL NOT verify
- **AND** the client's exit status SHALL be evaluated independently of the
  HTTP status line and the captured content
- **AND** response content from a failed transfer SHALL NOT be used

#### Scenario: Configuration failure restores state without restarting

- **GIVEN** a previous deployment is running
- **AND** a new deployment's configuration cannot be resolved after the
  target checkout and settings were written
- **WHEN** the deployment fails
- **THEN** the previous checkout and deployment settings SHALL be restored
- **AND** the running services SHALL NOT be restarted

#### Scenario: Verification failure rolls back with restart

- **GIVEN** a previous deployment is running
- **AND** a new deployment's services were started but verification failed
- **WHEN** the deployment rolls back
- **THEN** the previous checkout and deployment settings SHALL be restored
- **AND** the previous deployment SHALL be restarted
- **AND** a failure to collect diagnostic logs SHALL NOT prevent the rollback

#### Scenario: Verification says nothing about broker login

- **GIVEN** the deployment is verified
- **WHEN** its result is interpreted
- **THEN** verification SHALL NOT be taken to prove that OpenD is logged in
  to the broker or that trading is possible
- **AND** broker login and MCP availability SHALL be confirmed separately
  after each deployment

#### Scenario: The verifier runs from the deployed commit

- **GIVEN** the deploy script re-executes itself from a target commit
- **WHEN** it invokes the verifier
- **THEN** the verifier SHALL run from the checked-out target commit, not
  from the previously checked-out state or the re-executed script's own
  location

### Requirement: Execution Journal Storage Persistence

The deployment SHALL persist execution journal data across container restarts and
container recreation, using a dedicated volume that is separate from the OpenD
device authorization volume.

- The journal volume SHALL be mounted at a dedicated path and owned by the
  unprivileged user id the server runs as, so the server reads and writes it without
  root.
- Exactly one executor process SHALL access the journal volume at a time, enforced
  via an advisory exclusive process file lock (`execution.lock`).
- The deployment SHALL rely on POSIX filesystems honoring fsync durability.
- Backups SHALL use SQLite's online backup API, or an offline file copy that meets all
  of the following. An unheld process lock SHALL NOT by itself be treated as evidence
  that an offline copy is safe: a crashed process releases that lock exactly as a clean
  shutdown does, and may leave a hot rollback journal that is required to recover the
  database.
  - The database SHALL be cleanly closed or already recovered, with no hot rollback
    journal remaining.
  - No writer SHALL be able to start for the entire duration of the copy.
  - Where a hot rollback journal is present and cannot be cleared, it SHALL be copied
    and restored together with the database as a set.
- The OpenD authorization volume's mount path and owning user id SHALL be unchanged
  by this capability, so Session State Persistence continues to hold and no device
  re-authorization is triggered.
- The journal volume SHALL be optional. A deployment that does not run journaled
  paper execution SHALL start without it.
- Restoring older journal storage SHALL require recovery review before new mutations
  are admitted, as specified by `execution-journal` › Recovery Review Gate and
  Operator Acknowledgement.
- **Reinitializing, replacing or repointing the journal for the same broker account
  SHALL NOT be an approved way to clear unresolved execution.** The operational
  documentation SHALL state that the original journal and its unresolved records are
  preserved, and that standing up a new testing environment is a separately authorized
  action which SHALL NOT be reported as reconciliation of the previous one.
  - Continued experimentation SHALL use a separately verified, isolated paper
    environment, retaining the prior journal for investigation.
  - A provider's "reset paper account" facility SHALL NOT be assumed to provide that
    isolation. Its effect on outstanding orders, pending requests and account identity
    SHALL be verified before it is relied on.

#### Scenario: Container recreation preserves the journal

- **GIVEN** a deployment with operations recorded in the journal volume
- **WHEN** the container is recreated from a new image
- **THEN** the journal database and its recorded operations SHALL remain intact
- **AND** the OpenD authorization volume SHALL be unaffected

#### Scenario: Journal storage is owned by the unprivileged user

- **GIVEN** the container runs its processes as an unprivileged user
- **WHEN** the journal volume is mounted
- **THEN** the directory and database file SHALL be readable and writable by that
  user without root

#### Scenario: Single executor process is enforced across container environment

- **GIVEN** an active container holds the lock on `execution.lock`
- **WHEN** a second container or process mounts the volume and attempts to start
  the paper execution engine
- **THEN** the second process SHALL fail closed on lock acquisition
- **AND** the active container's database SHALL remain uncorrupted

#### Scenario: Supported consistent backup procedure does not corrupt database

- **GIVEN** active journaled execution is running
- **WHEN** a consistent backup is taken using SQLite's backup API
- **THEN** the backup file SHALL be a self-contained, valid SQLite database
- **AND** active database transactions SHALL NOT be interrupted or corrupted

#### Scenario: An unheld process lock after a crash does not make a file copy safe

- **GIVEN** the executor process terminated abnormally, leaving a hot rollback journal
  beside the database
- **AND** the process lock is consequently unheld
- **WHEN** an operator copies only the database file
- **THEN** the procedure SHALL be treated as unsupported
- **AND** the documented procedure SHALL require the database to be recovered first, or
  the rollback journal to be copied and restored with it

#### Scenario: A deployment without journaled paper execution needs no volume

- **GIVEN** the trading mode is `READ_ONLY`
- **WHEN** the container starts with no journal volume mounted
- **THEN** it SHALL start and serve reads normally

#### Scenario: A same-account journal reset is not an approved recovery procedure

- **GIVEN** a paper account whose journal holds an unresolved operation
- **WHEN** an operator considers reinitializing, replacing or repointing that journal
  to resume execution
- **THEN** the documented procedure SHALL refuse it as a recovery method
- **AND** SHALL direct the operator to preserve the journal and use a separately
  authorized, isolated paper environment instead

#### Scenario: Restored older storage requires review before mutations

- **GIVEN** an operator restores an older copy of the journal onto the volume
- **WHEN** the server starts for journaled paper execution
- **THEN** recovery review SHALL be required before any new mutation is admitted
- **AND** the OpenD authorization volume SHALL be unaffected
