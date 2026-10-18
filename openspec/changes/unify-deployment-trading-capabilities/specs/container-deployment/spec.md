## MODIFIED Requirements

### Requirement: State-preserving READ_ONLY migration

The initial native migration SHALL require an explicit single non-root host,
rootless Docker, an explicitly selected READ_ONLY, SIMULATE or REAL configuration, an existing matching Compose project
and named OpenD state. It SHALL preserve the discovered OpenD and known journal
volume identities, use existing protected server-side configuration and install
only public release definitions. Native deployment SHALL NOT delete journals or initialize them without an explicit operator flag, transport credential contents to the controller, or modify
existing credential files.

#### Scenario: Unsafe or unsupported existing deployment

- **WHEN** configuration disagrees with the explicitly selected mode, OpenD state is missing, state is bind-mounted or the project differs
- **THEN** migration SHALL fail before replacing the running application

#### Scenario: Application verification fails

- **WHEN** starting or verifying the new application or selected tunnel fails
- **THEN** native deployment SHALL restore the prior native or legacy selection
- **AND** the release SHALL still report failure
- **AND** persistent state SHALL be retained

#### Scenario: Authenticated MCP is ready but OpenD is not

- **WHEN** authenticated MCP and expected-mode checks pass but quote login or quote/trade health does not
- **THEN** application deployment SHALL remain running with a separate OpenD readiness warning
- **AND** it SHALL NOT claim broker or hosted ChatGPT acceptance

#### Scenario: Change mode and disable tunnel
- **WHEN** an operator explicitly selects another valid trading mode or disables an existing tunnel
- **THEN** native deployment SHALL validate its effective configuration and apply that selection while preserving existing state volumes

## ADDED Requirements

### Requirement: Reproducible effective releases
Managed deployments SHALL retain private atomic effective-configuration snapshots with immutable application image identities. Rollback SHALL restore the prior effective environment, image and state mounts independently of edits to the operator configuration file. Snapshots SHALL be excluded from public artifacts and build contexts.

#### Scenario: Configuration changes cause startup failure
- **WHEN** candidate configuration or verification fails after operator settings changed
- **THEN** the previous effective release SHALL remain selected or be restored without reusing the changed settings

#### Scenario: Restore a saved native configuration
- **WHEN** an operator selects a previously saved native release for rollback
- **THEN** the runtime and future-start selection SHALL use its validated frozen settings without reloading the edited operator configuration
- **AND** failure SHALL restore the current release without deleting or rewinding journal data

#### Scenario: Migration compatibility is no longer needed by ordinary upgrades
- **WHEN** the host already has a verified native release
- **THEN** subsequent rollouts SHALL use that release's snapshot for failure recovery without reconstructing the legacy runtime
- **AND** initial migration SHALL validate its reconstructed rescue configuration before replacing containers

### Requirement: Exclusive lifecycle ownership
All deployment, rollback and service lifecycle mutations of the same project SHALL share exclusive ownership. A competing restart or rollout SHALL fail or wait without changing runtime state.

#### Scenario: Restart during deployment
- **WHEN** a deployment owns the project and another caller requests a restart
- **THEN** the second caller SHALL NOT replace containers from a competing release

