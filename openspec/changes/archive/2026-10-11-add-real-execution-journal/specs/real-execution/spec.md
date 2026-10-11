# Spec Delta

## Purpose

Provides durable submission identity and conservative broker accounting for live trading mutations, preventing application retries from creating another SDK invocation.

## ADDED Requirements

### Requirement: Durable REAL Mutation Identity

Every REAL placement, combo placement, modification and cancellation SHALL require caller-owned `operation_id` and server-issued `admission_epoch`. Identity SHALL bind the environment, concrete allowlisted account, mutation kind and complete original parameters. Known identical tokens SHALL return stored or in-flight state without broker discovery, quota waits, unlock or replay, including after restart and while disconnected.

#### Scenario: Duplicate or conflicting retry
- **WHEN** an admitted REAL token is retried concurrently, after restart or under its original epoch
- **THEN** an identical request SHALL return its stored state without another SDK mutation
- **AND** different account or parameters SHALL be refused with the differing fields

#### Scenario: Missing or stale new token
- **WHEN** a REAL mutation lacks a complete token or presents an unknown retired epoch
- **THEN** the server SHALL refuse before broker discovery or unlock and SHALL not manufacture an identifier

#### Scenario: Numeric input and combo identity
- **WHEN** existing supported REAL order types, markets, numeric parameters or nested combo legs are supplied
- **THEN** durable identity SHALL include all dispatch-affecting inputs without adopting paper order restrictions
- **AND** changes to triggers, trailing fields, remark, time in force, target or combo legs SHALL conflict

### Requirement: Durable REAL Dispatch Boundary

REAL mutations SHALL durably commit admission and a dispatch marker before exactly one application SDK mutation, with no database transaction across broker I/O. Preparation, target reads, validation, invocation and durable outcome SHALL be serialized. Existing allowlists, valuation limits, managed unlock/relock and relock halt SHALL remain authoritative. Unavailable storage SHALL refuse writes with no fallback.

#### Scenario: Pre-dispatch failure
- **WHEN** validation, capacity reservation or managed unlock fails before dispatch
- **THEN** no SDK order mutation SHALL occur and the operation SHALL remain explicitly not sent

#### Scenario: Response or outcome persistence is lost
- **WHEN** dispatch starts and its response is lost, unreadable, or cannot be durably stored
- **THEN** the journal SHALL preserve uncertainty, block new REAL admissions and never replay the operation
- **AND** a known local acknowledgement SHALL remain visible if storage fails after the gateway responds

#### Scenario: Relock fails after acknowledgement
- **WHEN** a REAL SDK mutation is acknowledged but managed relock fails
- **THEN** its acknowledgement SHALL be stored and returned with the independent relock halt
- **AND** clearing the halt SHALL not clear journal recovery requirements

#### Scenario: Unsupported mutation cannot bypass persistence
- **WHEN** any REAL mutation reaches execution without a configured healthy REAL journal
- **THEN** it SHALL be refused before an SDK mutation or unlock

### Requirement: Conservative Automatic REAL Recovery

Process-owned recovery SHALL query fresh current/history orders and positions for the recorded REAL account using ordinary MCP authentication. Only exact persisted broker ID or placement tag and consistent request/account/environment evidence SHALL account for an operation. Recovery SHALL never place, modify, cancel, replace or resend orders. Negative, failed, ambiguous or incomplete observations SHALL keep unresolved dispatched REAL operations blocked.

#### Scenario: Exact broker evidence resolves uncertainty
- **WHEN** a correlated order and fresh position evidence consistently account for an uncertain REAL operation
- **THEN** the server SHALL atomically persist evidence, accounting disposition and gate release
- **AND** mutation success SHALL not be inferred from a modification or cancellation target's terminal state

#### Scenario: Repeated absence remains uncertainty
- **WHEN** any number of successful REAL searches find no correlated order
- **THEN** the original SHALL remain unresolved and new REAL admissions SHALL remain blocked
- **AND** both recovery policy and durable storage SHALL reject the paper assumed-absence resolution

#### Scenario: Similar or conflicting broker orders
- **WHEN** an order matches only attributes, a tag identifies multiple orders, or observed account/environment/fields conflict
- **THEN** recovery SHALL persist pending evidence and SHALL not release the gate

#### Scenario: Recovery survives client and process lifetimes
- **WHEN** an HTTP session ends or the process restarts with a dispatch marker lacking an outcome
- **THEN** background recovery SHALL continue independently of any client call and SHALL never dispatch the original again

### Requirement: Environment Selected Execution Inspection

Execution lookup and reconciliation SHALL accept explicit `trd_env`, defaulting to SIMULATE for compatibility, and route only to that environment's journal. Results SHALL expose bound environment/account, local submission state, durable broker evidence and recovery disposition without permitting caller-authored state changes.

#### Scenario: Same operation ID in both stores
- **WHEN** two journals contain the same caller operation ID
- **THEN** lookup with `trd_env="REAL"` SHALL return only the REAL record and default lookup SHALL return only the SIMULATE record
