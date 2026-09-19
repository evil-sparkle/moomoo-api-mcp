## ADDED Requirements

### Requirement: Persistent Execution Journal Storage Volume

The deployment SHALL persist execution journal data across container restarts and container recreation using a dedicated persistent volume (`execution-data`), separate and isolated from the OpenD device authorization volume (`opend-data`).

#### Scenario: Container recreation preserves execution journal
- **GIVEN** an active deployment with operations recorded in the execution journal volume
- **WHEN** the container is recreated or redeployed (`docker compose down` followed by `docker compose up`)
- **THEN** the execution journal database file and all recorded operations SHALL remain intact
- **AND** the OpenD device authorization volume SHALL NOT be affected.

#### Scenario: Journal storage owned by unprivileged container user
- **GIVEN** the container runs processes under unprivileged uid 10001
- **WHEN** the execution journal volume is mounted at `/var/lib/moomoo-mcp/data`
- **THEN** the directory and database files SHALL be owned by uid 10001 with read/write permissions
- **AND** no root access SHALL be required to read or write the journal.

#### Scenario: Restoring older backup requires reconciliation before resumption
- **GIVEN** an operator restores an older backup copy of the SQLite execution journal
- **WHEN** the server starts in `SIMULATE` mode
- **THEN** the system SHALL detect potential discrepancies between restored records and broker state
- **AND** operator reconciliation SHALL be required before accepting new automated orders.
