## ADDED Requirements

### Requirement: Execution Journal Health Reporting

The system health diagnostics SHALL report the operational status of the execution journal when the server is configured for `SIMULATE` trading mode. A degraded or locked database SHALL be diagnosed explicitly without taking down read-only diagnostic endpoints.

#### Scenario: Execution store healthy in SIMULATE mode
- **GIVEN** the server is running in `SIMULATE` mode with an active, writable SQLite journal
- **WHEN** `check_health` is called
- **THEN** the health report SHALL include an `execution_store` section indicating `status='ok'`, database path, schema version, and count of unresolved operations.

#### Scenario: Execution store not initialized in READ_ONLY mode
- **GIVEN** the server is running in `READ_ONLY` mode
- **WHEN** `check_health` is called
- **THEN** the health report SHALL state that the execution store is `disabled` or `not_initialized`
- **AND** the overall server status SHALL remain healthy for read operations.

#### Scenario: Degraded journal storage reported without breaking quote probes
- **GIVEN** the SQLite execution database becomes read-only or locked
- **WHEN** `check_health` is called
- **THEN** the health report SHALL indicate `execution_store` status as `degraded` or `halted`
- **AND** quote and account read connectivity probes SHALL continue to be reported accurately.
