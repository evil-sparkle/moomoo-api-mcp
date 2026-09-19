## ADDED Requirements

### Requirement: Paper Execution Journal Configuration

The system SHALL support configuration variables for the persistent execution journal and simulation account allowlisting:
- `MOOMOO_SIMULATE_ACC_IDS`: Comma-separated list of authorized simulated account IDs. Required when `MOOMOO_TRADING_MODE=SIMULATE`.
- `MOOMOO_EXECUTION_DB_PATH`: File system path to the SQLite execution database (default `/var/lib/moomoo-mcp/data/execution.db`).
- `MOOMOO_EXECUTION_BUSY_TIMEOUT_MS`: Bounded busy timeout in milliseconds for SQLite database lock acquisition (default 5000).

#### Scenario: Valid simulation configuration parsed
- **GIVEN** `MOOMOO_TRADING_MODE=SIMULATE`
- **AND** `MOOMOO_SIMULATE_ACC_IDS='1001,1002'`
- **AND** `MOOMOO_EXECUTION_DB_PATH='/var/lib/moomoo-mcp/data/execution.db'`
- **WHEN** the server starts
- **THEN** the configuration SHALL be accepted and the execution store initialized with those settings.

#### Scenario: Missing simulation accounts in SIMULATE mode fails startup
- **GIVEN** `MOOMOO_TRADING_MODE=SIMULATE`
- **AND** `MOOMOO_SIMULATE_ACC_IDS` is unset or empty
- **WHEN** the server starts
- **THEN** the server SHALL fail to start with a configuration error requiring an explicit simulation account allowlist.
