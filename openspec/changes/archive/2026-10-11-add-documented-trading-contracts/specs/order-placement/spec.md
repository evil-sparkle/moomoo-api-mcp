## ADDED Requirements

### Requirement: Explicit Compatible Trading Sessions
REAL placements SHALL accept documented session parameters, enforce product/order restrictions, and bind explicit session changes to durable identity. The default SHALL preserve pre-existing REAL behavior and canonical retries. Paper SHALL retain its established RTH contract and reject wider sessions before dispatch. Deprecated fill_outside_rth SHALL not be introduced.

#### Scenario: Session changes are different decisions
- **WHEN** a caller retries an operation identifier with a different explicit REAL session
- **THEN** the journal SHALL reject an identity conflict without mutation

#### Scenario: Old default retry remains identical
- **WHEN** an existing REAL operation is retried without a new explicit session
- **THEN** its old canonical request SHALL still match

#### Scenario: Paper session cannot broaden the executor
- **WHEN** paper placement requests ETH, OVERNIGHT or ALL
- **THEN** it SHALL be refused without silently converting it to RTH
