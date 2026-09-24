# Spec Delta

## ADDED Requirements

### Requirement: Optional Tunnel Service Isolation

An optional private ChatGPT tunnel SHALL run as a host service outside the
single supervised OpenD + MCP container. Adding, restarting, failing, disabling,
or removing the tunnel service SHALL NOT change the container's process
supervision, published ports, restart policy, OpenD state volume, optional
execution-journal volume, or existing local-client path.

#### Scenario: Tunnel is added
- **WHEN** an operator installs and enables the optional tunnel service
- **THEN** the OpenD + MCP Compose topology SHALL remain one service with only
  `127.0.0.1:8000` published
- **AND** OpenD SHALL remain on container-local `127.0.0.1:11111` and unpublished
- **AND** existing persistent volume identities and mount paths SHALL be unchanged

#### Scenario: Tunnel process fails
- **WHEN** the optional tunnel process exits or loses upstream connectivity
- **THEN** its supervisor SHALL handle that failure independently
- **AND** it SHALL NOT stop or restart the OpenD + MCP container

#### Scenario: Existing local client uses MCP
- **WHEN** ZeroClaw or another authorized host-local client calls the MCP endpoint
- **THEN** it SHALL use the same loopback address, authentication, tools, and
  restart behavior as before the optional integration

