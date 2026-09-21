# Spec Delta

## ADDED Requirements

### Requirement: Execution Journal Storage Persistence

The deployment SHALL persist execution journal data across container restarts and
container recreation, using a dedicated volume that is separate from the OpenD
device authorization volume.

- The journal volume SHALL be mounted at a dedicated path and owned by the
  unprivileged user id the server runs as, so the server reads and writes it without
  root.
- Exactly one executor process SHALL access the journal volume at a time, enforced
  via an advisory exclusive process file lock (`execution.lock`).
- The deployment SHALL rely on POSIX filesystems honoring fsync durability. Backups
  SHALL use SQLite's online backup API or stopped-container file copies.
- The OpenD authorization volume's mount path and owning user id SHALL be unchanged
  by this capability, so Session State Persistence continues to hold and no device
  re-authorization is triggered.
- The journal volume SHALL be optional. A deployment that does not run journaled
  paper execution SHALL start without it.
- Restoring older journal storage SHALL require recovery review before new mutations
  are admitted, as specified by `execution-journal` › Recovery Review Gate and
  Operator Acknowledgement.

#### Scenario: Container recreation preserves the journal

- **GIVEN** a deployment with operations recorded in the journal volume
- **WHEN** the container is recreated from a new image
- **THEN** the journal database and its recorded operations SHALL remain intact
- **AND** the OpenD authorization volume SHALL be unaffected

#### Scenario: Journal storage is owned by the unprivileged user

- **GIVEN** the container runs its processes as an unprivileged user
- **WHEN** the journal volume is mounted
- **THEN** the directory and database file SHALL be readable and writable by that
  user without root

#### Scenario: Single executor process is enforced across container environment

- **GIVEN** an active container holds the lock on `execution.lock`
- **WHEN** a second container or process mounts the volume and attempts to start
  the paper execution engine
- **THEN** the second process SHALL fail closed on lock acquisition
- **AND** the active container's database SHALL remain uncorrupted

#### Scenario: Supported consistent backup procedure does not corrupt database

- **GIVEN** active journaled execution is running
- **WHEN** a consistent backup is taken using SQLite's backup API
- **THEN** the backup file SHALL be a self-contained, valid SQLite database
- **AND** active database transactions SHALL NOT be interrupted or corrupted

#### Scenario: A deployment without journaled paper execution needs no volume

- **GIVEN** the trading mode is `READ_ONLY`
- **WHEN** the container starts with no journal volume mounted
- **THEN** it SHALL start and serve reads normally

#### Scenario: Restored older storage requires review before mutations

- **GIVEN** an operator restores an older copy of the journal onto the volume
- **WHEN** the server starts for journaled paper execution
- **THEN** recovery review SHALL be required before any new mutation is admitted
- **AND** the OpenD authorization volume SHALL be unaffected
