## Purpose

Let operators stop new trading risk and inspect existing execution evidence without conflating gateway locking with deployment write permission.

## ADDED Requirements

### Requirement: Persistent operator pause
An authenticated operator SHALL be able to persist and inspect a new-risk pause for an environment or account with an attributed reason. Pauses SHALL survive process replacement and gate new exposure at dispatch. Cancellations SHALL retain their existing durable safety checks and SHALL NOT be blocked solely by this pause.

#### Scenario: Pause survives restart
- **WHEN** REAL new risk is paused and the server restarts
- **THEN** REAL exposure-adding dispatch SHALL remain refused until an operator resumes it
- **AND** independently permitted paper trading and journaled cancellation SHALL remain available

### Requirement: Opt-in execution observation
Explicit observation SHALL permit existing configured journals and controls to remain inspectable outside writable modes. Observation SHALL NOT create journals, unlock trading, submit orders, or elevate policy. Ordinary READ_ONLY operation SHALL remain independent of journal storage.

#### Scenario: Disable writes while retaining evidence
- **WHEN** a deployment switches to READ_ONLY with observation enabled and existing journals configured
- **THEN** execution inspection and broker-query reconciliation SHALL remain available
- **AND** broker mutations SHALL remain refused

### Requirement: Cancellation without quote classification
Cancellation of a verified in-scope paper order SHALL NOT depend on obtaining new instrument classification or valuation quotes.

#### Scenario: Quote metadata is unavailable
- **WHEN** an authorized paper cancellation identifies a valid existing in-scope order but quote metadata is unavailable
- **THEN** quote metadata alone SHALL NOT prevent its journaled cancellation
