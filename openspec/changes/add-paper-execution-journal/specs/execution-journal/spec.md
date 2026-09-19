## Purpose

Provides a persistent, application-local execution journal for order mutations, guaranteeing durable operation identity, duplicate suppression, pre-dispatch persistence, and crash recovery during paper execution.

## ADDED Requirements

### Requirement: Durable Operation Identification and Duplicate Suppression

The execution journal SHALL require every order mutation operation to carry a stable, caller-provided operation identifier (`operation_id`). The system SHALL use the identifier to enforce idempotent execution and prevent duplicate orders.

#### Scenario: Same operation ID with identical normalized request returns stored result
- **GIVEN** an operation with ID `op-12345` has previously been submitted and recorded in the journal
- **WHEN** a client submits an order mutation with `operation_id='op-12345'` and identical normalized parameters (account, environment, code, side, quantity, price, order type)
- **THEN** the system SHALL return the stored result or current known execution state
- **AND** the system SHALL NOT dispatch a new order mutation to the broker gateway.

#### Scenario: Same operation ID with differing parameters is rejected
- **GIVEN** an operation with ID `op-12345` is recorded in the journal with quantity `100`
- **WHEN** a client submits a mutation with `operation_id='op-12345'` but quantity `200`
- **THEN** the system SHALL reject the request as a conflict error
- **AND** the system SHALL NOT dispatch the order to the broker gateway.

#### Scenario: Concurrent submissions with the same operation ID dispatch at most once
- **GIVEN** no operation exists with ID `op-concurrent-1`
- **WHEN** two concurrent requests arrive with `operation_id='op-concurrent-1'` and identical parameters
- **THEN** exactly one request SHALL acquire the dispatch reservation and submit to the broker
- **AND** the other request SHALL wait for the in-flight operation to complete and return the resulting state without duplicate submission.

#### Scenario: Missing operation ID fails explicitly
- **GIVEN** the server is configured for journaled paper execution
- **WHEN** a client calls an order mutation tool without an `operation_id` or with an empty string
- **THEN** the system SHALL reject the call before contacting the broker gateway
- **AND** the system SHALL NOT generate a random or synthetic operation identifier.

### Requirement: Pre-Dispatch State Persistence and Storage Failure Handling

The system SHALL durably commit an operation record in SQLite before initiating the broker gateway network request. If journal storage cannot be written before dispatch, the mutation SHALL be refused. If journal storage fails after dispatch, the system SHALL retain uncertainty, fail closed, and block further automated execution.

#### Scenario: Operation durably committed prior to broker call
- **GIVEN** a valid mutation request with `operation_id='op-precommit-1'`
- **WHEN** the mutation is processed
- **THEN** the system SHALL commit the operation to the SQLite journal with state `PENDING_SUBMIT` before making the broker API call
- **AND** the database write transaction SHALL be closed before the broker network call begins.

#### Scenario: Storage write failure before dispatch refuses mutation
- **GIVEN** the SQLite execution database is read-only, locked, or unavailable
- **WHEN** a client calls an order mutation tool
- **THEN** the system SHALL refuse the mutation with a storage error
- **AND** the system SHALL NOT contact the broker gateway
- **AND** the error SHALL indicate that the order was not sent and is safe to retry once storage is restored.

#### Scenario: Storage write failure after dispatch retains uncertainty and halts execution
- **GIVEN** an order was successfully sent to the broker gateway
- **WHEN** the system attempts to update the journal with the broker outcome but the database write fails
- **THEN** the system SHALL mark the execution engine as `HALTED`
- **AND** the system SHALL NOT report a clean success or a safe-to-retry error to the client
- **AND** subsequent mutation requests SHALL be refused until storage is repaired and the unpersisted outcome is reconciled.

### Requirement: Dispatch Boundary and Outcome Uncertainty Separation

The journal SHALL distinguish local submission lifecycle states (`PENDING_SUBMIT`, `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED`, `FAILED`) from broker order statuses (`SUBMITTED`, `FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL`, `REJECTED`). Gateway acknowledgement SHALL NOT be reported as filled, and a network timeout SHALL NOT be reported as rejected.

#### Scenario: Crash or timeout during broker call marks outcome unknown
- **GIVEN** an operation is in state `PENDING_SUBMIT`
- **WHEN** the broker network call times out or the connection drops before receiving a response
- **THEN** the operation state in the journal SHALL be set to `UNKNOWN_OUTCOME`
- **AND** the system SHALL NOT automatically re-send the order
- **AND** the error returned to the caller SHALL indicate that the outcome is unknown and must be reconciled.

#### Scenario: Preserving broker receipt if subsequent local processing fails
- **GIVEN** the broker accepts an order and returns broker order ID `moomoo-order-987`
- **WHEN** an error occurs during local post-processing or serialization
- **THEN** the broker receipt including order ID `moomoo-order-987` SHALL remain durably recorded in the journal
- **AND** the response SHALL report the broker acknowledgement along with the local diagnostic failure.

### Requirement: Order Status Reconciliation and Recovery

The system SHALL provide an explicit reconciliation path for operations in `UNKNOWN_OUTCOME`. Recovery and server restart SHALL NEVER automatically resubmit or replay pending commands.

#### Scenario: Reconciliation queries broker to resolve unknown outcome
- **GIVEN** an operation `op-unrec-1` is recorded with state `UNKNOWN_OUTCOME`
- **WHEN** reconciliation is executed for `op-unrec-1`
- **THEN** the system SHALL query the broker's active order list and deal list for matching records
- **AND** if a uniquely matching broker order is found, the journal state SHALL transition to `RECONCILED` with the authoritative broker status.

#### Scenario: Empty or ambiguous query result remains unresolved
- **GIVEN** an operation `op-unrec-2` has state `UNKNOWN_OUTCOME`
- **WHEN** a broker order query returns no matching records or multiple ambiguous matches
- **THEN** the system SHALL NOT treat an empty query as proof that the order never existed at the broker
- **AND** the operation state SHALL remain `UNKNOWN_OUTCOME`
- **AND** automated trading on the associated account SHALL remain blocked until manual operator resolution.

#### Scenario: Distinguishing partial fill, complete fill, and cancellation
- **GIVEN** an operation was submitted to the broker
- **WHEN** order status queries or deal queries are observed
- **THEN** the journal SHALL record the authoritative broker order status and filled quantity
- **AND** partial fills, complete fills, and cancellations SHALL remain mutually distinct in journal records.

### Requirement: Journal Storage Integrity and Schema Compatibility

The execution store SHALL verify database file integrity and schema version on startup. A missing storage directory or an incompatible schema SHALL fail closed and SHALL NOT silently initialize an empty database that drops duplicate-protection history.

#### Scenario: Incompatible schema version halts startup
- **GIVEN** an existing SQLite execution database file has a schema version higher than supported by the running binary
- **WHEN** the server starts in `SIMULATE` trading mode
- **THEN** the server startup SHALL fail with an explicit schema incompatibility error
- **AND** the database file SHALL NOT be overwritten or modified.

#### Scenario: Missing expected database fails closed in existing deployment
- **GIVEN** an existing deployment configured for persistent paper execution starts with a missing database file at the configured path
- **WHEN** the server initializes the execution store
- **THEN** the system SHALL require an explicit initialization flag or operator confirmation before creating a fresh empty database
- **AND** if the flag is absent, startup SHALL fail to prevent silent loss of duplicate-suppression state.
