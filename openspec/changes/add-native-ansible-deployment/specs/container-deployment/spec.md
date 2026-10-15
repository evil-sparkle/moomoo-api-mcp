## ADDED Requirements

### Requirement: Verified native release selection

Native deployment SHALL accept only an immutable application and tunnel image
record bound to a completed successful main CI run in this repository, its exact
attempt and full source commit. Automatic deployment SHALL require a main push
and skip a revision no longer current main. Manual rollback MAY select an older
successful native release.

#### Scenario: CI attempt or source does not match

- **WHEN** a release record belongs to another run, attempt, commit or repository
- **THEN** selection SHALL fail before deployment credentials or host changes

#### Scenario: Successful CI is obsolete

- **WHEN** an automatic request's revision is no longer current main
- **THEN** dispatch or release selection SHALL skip the request
- **AND** an explicit manual rollback MAY still select that successful release

### Requirement: State-preserving READ_ONLY migration

The initial native migration SHALL require an explicit single non-root host,
rootless Docker, READ_ONLY configuration, an existing matching Compose project
and named OpenD state. It SHALL preserve the discovered OpenD and known journal
volume identities, use existing protected server-side configuration and install
only public release definitions. Native deployment SHALL NOT initialize or
delete journals, transport credential contents to the controller, or modify
existing credential files.

#### Scenario: Unsafe or unsupported existing deployment

- **WHEN** configuration is writable, OpenD state is missing, state is bind-mounted or the project differs
- **THEN** migration SHALL fail before replacing the running application

#### Scenario: Application verification fails

- **WHEN** starting or verifying the new application or selected tunnel fails
- **THEN** native deployment SHALL restore the prior native or legacy selection
- **AND** the release SHALL still report failure
- **AND** persistent state SHALL be retained

#### Scenario: Authenticated MCP is ready but OpenD is not

- **WHEN** authenticated MCP and READ_ONLY checks pass but quote login or quote/trade health does not
- **THEN** application deployment SHALL remain running with a separate OpenD readiness warning
- **AND** it SHALL NOT claim broker or hosted ChatGPT acceptance

## MODIFIED Requirements

### Requirement: Single manual deployment entrypoint

Each server SHALL use one selected deployment authority. The existing manual
script SHALL remain the bootstrap and writable-mode deployment entrypoint.
After native READ_ONLY migration, the application-owned playbook and its shared
GitHub workflow SHALL own upgrades and rollback for that server. Operators SHALL
NOT mix the legacy script with native release management. Both paths SHALL use
CI-published images and preserve existing Compose project and persistent volume
identities. Site-specific credential setup SHALL remain outside the repository.
The first native version SHALL preserve an existing enabled tunnel and refuse
an implicit change to its disabled selection; operators SHALL choose any
required legacy disablement before native cutover.

#### Scenario: First enablement

- **WHEN** the operator enables the optional tunnel with valid deployment settings
- **THEN** the selected deployment authority SHALL resolve its CI image and preserve the existing project
- **AND** missing required credentials or images SHALL fail before service changes

#### Scenario: Disable through the deployment script

- **WHEN** an operator selects tunnel disablement through the legacy script before native cutover
- **THEN** only the optional service and its public selection SHALL be removed
- **AND** brokerage state SHALL be retained

#### Scenario: Newly enabled deployment fails

- **WHEN** deployment fails after adding the optional selection
- **THEN** rollback SHALL restore the previous selection and application images
- **AND** a newly enabled optional tunnel SHALL NOT remain running
