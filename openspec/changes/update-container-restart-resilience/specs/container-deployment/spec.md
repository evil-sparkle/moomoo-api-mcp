## MODIFIED Requirements

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

## ADDED Requirements

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
