## MODIFIED Requirements

### Requirement: Binary Download Integrity Verification

The OpenD container build process SHALL cryptographically verify the SHA256 checksum of downloaded gateway binaries before extraction and execution. The expected checksum SHALL be the one pinned in `Dockerfile.opend`, which is the single source of truth for it. This specification SHALL NOT restate that literal: a duplicate goes stale on the next version bump and then reads as authoritative while being wrong.

#### Scenario: Verify download checksum matches the pinned hash
- **GIVEN** a downloaded OpenD tarball during container image build
- **WHEN** the sha256sum tool computes the checksum of the archive
- **THEN** the build SHALL proceed only if the checksum matches the value pinned in `Dockerfile.opend`
- **AND** abort immediately with an error if the checksum differs

#### Scenario: A tampered download fails the build
- **GIVEN** a downloaded OpenD tarball whose contents differ from the pinned release
- **WHEN** the build verifies its checksum
- **THEN** the build SHALL fail before the archive is extracted
- **AND** no unverified binary SHALL be copied into the image

## ADDED Requirements

### Requirement: Client-Transparent Container Restarts

Restarting either container SHALL cost a connected MCP client at most the call in flight at that moment. The client SHALL NOT be required to reconnect, to re-initialize by hand, or to be restarted itself, and the MCP endpoint SHALL keep the same address throughout.

This holds in both directions: a gateway restart SHALL NOT take the MCP endpoint down with it, and an MCP server restart SHALL NOT invalidate a client's established connection to it.

#### Scenario: Gateway restart leaves the MCP endpoint serving
- **GIVEN** the Compose stack is running and a client is connected to the MCP server
- **WHEN** the `opend` container is restarted
- **THEN** the `moomoo-mcp` container SHALL keep running and SHALL keep serving its published port
- **AND** the client SHALL continue to reach the same endpoint without reconnecting

#### Scenario: Gateway recreated at a different address is found again
- **GIVEN** the MCP server has an established connection to the gateway
- **WHEN** the gateway container is recreated and comes back at a different container IP
- **THEN** the MCP server SHALL re-resolve the `opend` service name on its next connection attempt
- **AND** reconnect without operator intervention

#### Scenario: Restarting the gateway does not restart the MCP server
- **GIVEN** the Compose stack is running
- **WHEN** Compose restarts the `opend` service
- **THEN** the `moomoo-mcp` container SHALL NOT be restarted as a consequence
- **AND** client sessions in progress SHALL survive the gateway restart

#### Scenario: MCP server restart keeps clients connected
- **GIVEN** a client has an established MCP session
- **WHEN** the `moomoo-mcp` container is restarted
- **THEN** the client's next request SHALL be served normally once the server is back
- **AND** the client SHALL NOT be required to re-initialize or to be restarted

#### Scenario: In-flight call during a restart
- **GIVEN** a tool call is in flight when the container serving it restarts
- **WHEN** that container goes down
- **THEN** that one call MAY fail
- **AND** the client SHALL be able to retry it against the same endpoint once the container is back
