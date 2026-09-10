# container-deployment Specification

## Purpose

Provides secure, isolated container deployment specifications for running the OpenD gateway alongside the moomoo-api-mcp server using Docker Compose, enforcing network isolation, credential compartmentalization, and persistent session storage.

## Requirements

### Requirement: Isolated OpenD Gateway Network

The system SHALL deploy OpenD and the MCP server within a dedicated container network where the OpenD API port is internal and not exposed to the host network interface.

#### Scenario: OpenD port unreachable from host
- **GIVEN** the Docker Compose stack is running
- **WHEN** a process on the host machine attempts to connect to port 11111 on localhost
- **THEN** the connection SHALL be rejected because port 11111 is internal to the container network

#### Scenario: MCP server reaches OpenD internally
- **GIVEN** the Docker Compose stack is running
- **WHEN** the MCP server connects to `opend:11111`
- **THEN** the connection SHALL succeed over the internal bridge network

### Requirement: Credential Compartmentalization

Trading credentials SHALL be injected solely into the MCP server container and SHALL NOT be present in the host agent environment.

#### Scenario: Host agent cannot inspect container trade secrets
- **GIVEN** `MOOMOO_TRADE_PASSWORD_MD5` is configured in the container stack
- **WHEN** an agent executes commands in the host shell
- **THEN** the host environment variables SHALL NOT contain `MOOMOO_TRADE_PASSWORD_MD5`

### Requirement: Session State Persistence

The deployment SHALL persist OpenD session tokens and device authorization state across container restarts using a persistent volume.

#### Scenario: OpenD restarts without requiring repeated SMS 2FA
- **GIVEN** OpenD has completed initial login and device verification
- **WHEN** the OpenD container is stopped and restarted
- **THEN** OpenD SHALL read existing session tokens from the persistent volume
- **AND** resume serving without prompting for a new device authorization code

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
