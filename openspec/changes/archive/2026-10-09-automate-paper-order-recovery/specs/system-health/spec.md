# Spec Delta

## ADDED Requirements

### Requirement: Report Autonomous Recovery Progress

Journal health SHALL include pending operation IDs, durable recovery summaries and
recent audited decisions without making broker queries. Existing journal-state,
connectivity and storage-failure reporting SHALL remain independent.

#### Scenario: Pending recovery and decisions are discoverable
- **WHEN** `check_health` returns an enabled execution journal
- **THEN** it SHALL include pending operation IDs and recent recovery updates
- **AND** updates SHALL identify the operation, tag, recovery disposition, clean
  negative count, actual check times and next scheduled check
- **AND** assumed absence SHALL remain distinct from verified broker accounting

#### Scenario: Background completion reaches the next ordinary reply
- **WHEN** recovery completes between stateless HTTP requests
- **THEN** the next health reply SHALL expose its durable decision
- **AND** health SHALL not require the client to invoke reconciliation first
