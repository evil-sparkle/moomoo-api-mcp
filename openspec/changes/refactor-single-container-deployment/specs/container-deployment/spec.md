## MODIFIED Requirements

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

#### Scenario: MCP server reaches OpenD over container loopback

- **GIVEN** the deployment is running and the gateway has started
- **WHEN** the MCP server connects to `127.0.0.1:11111`
- **THEN** the connection SHALL succeed without the gateway listening on any
  non-loopback address

#### Scenario: MCP endpoint published to host loopback only

- **GIVEN** the deployment is running
- **WHEN** the published ports are inspected
- **THEN** the MCP HTTP endpoint SHALL be published on `127.0.0.1:8000` and no
  other port SHALL be published

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

## ADDED Requirements

### Requirement: Paired Process Supervision

A supervisor process SHALL own both the OpenD gateway and the MCP server, and
SHALL implement an explicit recovery policy rather than leaving a dead child
invisible to the container runtime's restart policy. The supervisor SHALL reap
terminated children and SHALL forward stop signals to both processes.

#### Scenario: Gateway process death does not remove the MCP endpoint

- **GIVEN** a client holds a working connection to the MCP endpoint
- **WHEN** the OpenD process exits unexpectedly
- **THEN** the supervisor SHALL restart OpenD in place
- **AND** the MCP server SHALL keep serving requests throughout
- **AND** health SHALL report `disconnected` or `degraded` until the gateway answers

#### Scenario: Gateway that cannot be recovered takes the container down

- **GIVEN** the OpenD process has exited and been restarted up to the configured bound
- **WHEN** it exits again within the configured window
- **THEN** the supervisor SHALL stop the MCP server cleanly
- **AND** exit non-zero so the container runtime replaces the whole unit

#### Scenario: MCP process death takes the container down

- **GIVEN** the deployment is running
- **WHEN** the MCP server process exits unexpectedly
- **THEN** the supervisor SHALL stop the OpenD process cleanly
- **AND** exit non-zero so the container runtime replaces the whole unit

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

#### Scenario: MCP start does not wait on the gateway

- **GIVEN** the container has just started
- **WHEN** the OpenD process has not yet completed its login
- **THEN** the MCP server SHALL already be accepting requests
- **AND** SHALL answer health requests reporting the gateway as unavailable
