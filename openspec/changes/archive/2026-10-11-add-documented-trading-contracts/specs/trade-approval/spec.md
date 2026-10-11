## Purpose

Provide durable prepared trading requests and enforce trusted operator decisions independently from broker execution outcomes.

## ADDED Requirements

### Requirement: Immutable Prepared Mutation
The system SHALL prepare a reviewable mutation bound to its full parameters, account, environment, operation identifier, admission epoch and expiry. Preparing SHALL perform no broker mutation. Approval-enabled dispatch SHALL require a matching approved request.

#### Scenario: Changed or expired request
- **WHEN** a mutation differs from its prepared parameters or its approval expires
- **THEN** it SHALL be refused before broker mutation and unlock

### Requirement: Trusted Operator Decision Channel
Approval and rejection SHALL be accepted only through a separately authenticated operator channel unavailable as an MCP tool. The ordinary agent credential SHALL NOT authorize decisions. Decisions SHALL retain server-configured operator identity and audit evidence.

#### Scenario: Agent cannot approve itself
- **WHEN** an ordinary MCP credential calls the operator channel or supplies approved=true
- **THEN** no approval SHALL be granted

### Requirement: Approval Preserves Journal Replay Safety
Approval state SHALL remain separate from execution outcome. A consumed approval SHALL authorize only its bound operation, and an identical journal retry SHALL return stored state without another dispatch, budget charge or fresh approval.

#### Scenario: Unknown outcome retry
- **WHEN** an approved mutation has an unknown outcome and is retried identically
- **THEN** its durable journal result SHALL be returned without replay
