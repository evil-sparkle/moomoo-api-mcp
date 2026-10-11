## ADDED Requirements

### Requirement: Govern Fee Queries and Server Continuations
Actual fee retrieval SHALL use its documented account-scoped budget of ten requests per thirty seconds. Valid server snapshot continuations SHALL consume no broker quota because they perform no SDK query. Existing per-SDK enforcement SHALL cover direct and MCP callers.

#### Scenario: Fee budget exhausted
- **WHEN** a supported account exhausts its fee budget
- **THEN** another fee query SHALL fail or wait according to existing admission rules without SDK dispatch

#### Scenario: Query page continuation
- **WHEN** a valid immutable server cursor is continued
- **THEN** no provider query or timed quota entry SHALL be created
