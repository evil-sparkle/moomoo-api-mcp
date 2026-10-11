# Spec Delta

## ADDED Requirements

### Requirement: Separate Durable REAL Journal Configuration

REAL startup SHALL require `MOOMOO_REAL_JOURNAL_PATH` and accept explicit `MOOMOO_CREATE_REAL_JOURNAL=0|1`, defaulting to 0. REAL and SIMULATE stores SHALL use distinct directories and immutable environment metadata. Startup SHALL validate and lock both configured stores before listening, preserve existing paper storage, and fail closed for missing, corrupt, incompatible or competing storage. READ_ONLY SHALL open neither store.

#### Scenario: Live deployment requires initialized persistence
- **WHEN** REAL mode lacks its path or initialization is disabled for a missing database
- **THEN** startup SHALL fail before HTTP listens without modifying existing execution records

#### Scenario: Environment or directory collision
- **WHEN** REAL points to a paper database or shares the paper journal directory
- **THEN** startup SHALL refuse instead of migrating records across trading environments

#### Scenario: Read-only and paper compatibility
- **WHEN** the server runs READ_ONLY or SIMULATE
- **THEN** no REAL journal SHALL be opened and existing paper behavior SHALL be preserved
