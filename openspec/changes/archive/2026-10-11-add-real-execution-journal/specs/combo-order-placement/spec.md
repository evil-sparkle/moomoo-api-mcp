# Spec Delta

## ADDED Requirements

### Requirement: Journaled REAL Combo Placement

REAL combo placements SHALL require `operation_id` and `admission_epoch` through both service and MCP interfaces. All original leg parameters, signed package price, quantity, order type, time in force and caller remark SHALL participate in immutable identity. Dispatch SHALL preserve existing combo validation and managed lock safeguards while using durable placement correlation.

#### Scenario: Retry an acknowledged package
- **WHEN** an identical REAL combo token is retried
- **THEN** its stored receipt SHALL be returned without a second package submission
