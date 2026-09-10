## ADDED Requirements

### Requirement: Binary Download Integrity Verification

The OpenD container build process SHALL cryptographically verify the SHA256 checksum of downloaded gateway binaries before extraction and execution.

#### Scenario: Verify download checksum matches pinned hash
- **GIVEN** a downloaded OpenD tarball during container image build
- **WHEN** the sha256sum tool computes the checksum of the archive
- **THEN** the build SHALL proceed only if the checksum matches `d38aad772b296f922e3b270119ca1abbadadc61cd3a45826e1b087a5b79069a5`
- **AND** abort immediately with an error if the checksum differs

### Requirement: Non-Root Container Execution

Containers in the deployment stack SHALL run as unprivileged, non-root users to minimize blast radius in the event of a container escape.

#### Scenario: OpenD gateway process runs as non-root
- **GIVEN** the OpenD container is running
- **WHEN** checking the execution user of the `OpenD` process
- **THEN** the effective UID SHALL NOT be 0 (root)

#### Scenario: MCP server process runs as non-root
- **GIVEN** the MCP server container is running
- **WHEN** checking the execution user of the Python MCP server process
- **THEN** the effective UID SHALL NOT be 0 (root)
