# Spec Delta

## ADDED Requirements

### Requirement: Independent REAL Journal Health

Health SHALL retain existing paper `execution_journal` reporting and add independent `real_execution_journal` state, active epoch, pending IDs, blocking reasons and recovery summaries. Reporting SHALL use bounded local storage probes, remain callable while OpenD is unavailable and neither change connectivity status nor clear relock or recovery gates.

#### Scenario: Discover REAL admission epoch and pending recovery
- **WHEN** REAL journal health is requested
- **THEN** it SHALL report the REAL epoch and stored pending decisions independently of paper journal and gateway connectivity
