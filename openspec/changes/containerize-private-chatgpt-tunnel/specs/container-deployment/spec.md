# Spec Delta

## MODIFIED Requirements

### Requirement: Optional Tunnel Service Isolation

An optional private ChatGPT tunnel SHALL run as a separate optional Compose container outside the
single supervised OpenD + MCP brokerage container; legacy host service assets
SHALL remain a documented rollback path. Adding, restarting, failing, disabling,
or removing the tunnel service SHALL NOT change the container's process
supervision, published ports, restart policy, OpenD state volume, optional
execution-journal volume, or existing local-client path.

#### Scenario: Tunnel is added

- **WHEN** an operator installs and enables the optional tunnel service
- **THEN** the brokerage SHALL remain one service with two supervised processes, with one
  additional tunnel service only when its overlay is explicitly selected
- **AND** only `127.0.0.1:8000:8000` SHALL be published by the deployment
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

#### Scenario: Bridge connectivity does not expose the gateway

- **WHEN** the tunnel connects on the user-defined bridge using `moomoo-mcp` service DNS
- **THEN** authenticated MCP at port 8000 SHALL be reachable and OpenD at port 11111 SHALL be unreachable
- **AND** OpenD SHALL remain bound only to 127.0.0.1 inside the brokerage namespace
- **AND** both services SHALL retain required outbound connectivity without an internal-only network

#### Scenario: Tunnel container isolation

- **WHEN** the enabled deployment is inspected
- **THEN** the tunnel SHALL have separate network and PID namespaces, no host networking, no privileged mode, no Docker socket and no brokerage-state or journal mounts
- **AND** no tunnel port SHALL be published and its health/admin listener SHALL remain on its own loopback

#### Scenario: Default topology remains unchanged

- **WHEN** only the ordinary base/production overlays are selected
- **THEN** there SHALL be exactly one brokerage service with the existing two-process supervisor, ports, volumes and restart behavior

#### Scenario: Deployment preserves explicit tunnel selection

- **WHEN** deployment, verification or rollback resolves an enabled stack
- **THEN** they SHALL use the same saved overlay selection, Docker context and project identity
- **AND** generic orphan cleanup SHALL NOT remove the explicitly enabled tunnel
- **AND** rollback to a version without tunnel-overlay support SHALL require prior tunnel disablement

## ADDED Requirements

### Requirement: Reproducible hardened tunnel image

The tunnel SHALL use a dedicated small image containing the official reviewed release and minimal startup/diagnostic support, with immutable image inputs and archive integrity verification before binary execution. The reviewed release manifest SHALL remain the release source of truth. Runtime startup SHALL NOT download latest or upgrade the client. The container SHALL run as a numeric non-root UID/GID with a read-only root filesystem, dropped capabilities, no-new-privileges, and narrowly scoped writable runtime storage. It SHALL preserve signal handling, bounded shutdown and independent restart behavior.

#### Scenario: Unverified archive

- **WHEN** the official-client archive fails its committed integrity pin
- **THEN** image construction SHALL fail before extraction or execution

#### Scenario: Hardened runtime works with real release

- **WHEN** the pinned image runs as its declared identity
- **THEN** the verified client, entrypoint, configuration and mounted secrets SHALL work under the declared filesystem and capability restrictions
- **AND** the image SHALL contain no brokerage SDK, OpenD binary or deployment secrets

#### Scenario: Runtime exits or receives termination

- **WHEN** the client exits or the container receives a stop signal
- **THEN** child processes SHALL be reaped and stopped within the documented bound
- **AND** failure recovery SHALL affect only the tunnel service

### Requirement: Isolated behavioral container verification

CI SHALL exercise the actual tunnel image, entrypoint, runtime permissions and pinned client using synthetic credentials and disposable resources. Simulated control-plane tests SHALL be identified separately from live OpenAI acceptance. Static assertions or stub processes SHALL NOT constitute all runtime evidence. Tests SHALL preserve default security assertions and use unique projects and available loopback test ports without stopping developer brokerage stacks.

#### Scenario: Runtime verification uses disposable resources

- **WHEN** container checks run on a machine with an existing deployment
- **THEN** tests SHALL use isolated project/network/volume identities and synthetic inputs
- **AND** cleanup SHALL remove only their resources without claiming host port 8000 or reading real deployment secrets

#### Scenario: External acceptance remains pending

- **WHEN** only synthetic container and simulated control-plane checks passed
- **THEN** VPS deployment, credentialed OpenAI access, ChatGPT web invocation and native iPad acceptance SHALL remain pending


#### Scenario: Fixtures remain isolated while the release is blocked

- **WHEN** independent container implementation is exercised before official-client release-gate closure
- **THEN** tests SHALL use synthetic credential sources and disposable resources with enforced fixture-only destinations and no real OpenAI traffic
- **AND** image hardening, secret permissions, Compose integration and lifecycle results SHALL be reported separately from official-client compatibility
- **AND** neither those results nor an explicitly selected test overlay SHALL authorize production enablement
