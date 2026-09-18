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
