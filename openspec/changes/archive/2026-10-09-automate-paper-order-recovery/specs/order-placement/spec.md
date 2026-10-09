# Spec Delta

## ADDED Requirements

### Requirement: Paper Placement Recovery Context

Journaled paper placements SHALL use the persisted order tag as the broker remark
and return server-owned recovery context. Caller remarks SHALL remain in the
immutable journal request. REAL order remarks and dispatch classification SHALL
retain their existing behavior.

#### Scenario: Paper remark is reserved for correlation
- **WHEN** a paper placement with a caller remark is dispatched
- **THEN** the broker SHALL receive the unique persisted order tag as its remark
- **AND** the reply SHALL expose both the operation ID and order tag
- **AND** retry identity SHALL still compare the original caller remark

#### Scenario: Paper uncertainty triggers automatic recovery
- **WHEN** a paper placement receives an uncertain gateway outcome
- **THEN** the service SHALL schedule automatic recovery and return its durable state
- **AND** the original operation SHALL never be resent
- **AND** subsequent paper mutation replies SHALL include recent recovery decisions

#### Scenario: A separate new decision after assumed absence
- **WHEN** recovery records ASSUMED_NOT_PLACED_AFTER_RETRIES
- **THEN** the original operation SHALL remain UNKNOWN_OUTCOME
- **AND** an identical retry SHALL return that record without a broker call
- **AND** a new operation ID SHALL represent a separate trading decision subject to
  the existing trading limits and readiness checks
