## ADDED Requirements

### Requirement: Exact Symbol Allowlist
Optional configured symbol restrictions SHALL match exact instrument codes and apply to every leg of new placements and exposure-adding modifications. Cancellations SHALL remain possible. Invalid or empty configured lists SHALL fail startup.

#### Scenario: Disallowed combo leg
- **WHEN** one combo leg is absent from the configured allowlist
- **THEN** the operation SHALL be refused before any broker mutation

### Requirement: Durable Daily Submission Budget
Optional daily budgets SHALL be enforced durably by UTC date, account, environment and currency using conservative policy valuation. New placements and exposure-adding modifications SHALL consume budget before dispatch; uncertain outcomes SHALL remain charged. Identical operation retries SHALL never charge twice.

#### Scenario: Concurrent budget contention
- **WHEN** concurrent operations would collectively exceed a configured budget
- **THEN** atomic accounting SHALL refuse the excess before dispatch

#### Scenario: Restart or uncertainty does not restore capacity
- **WHEN** the server restarts or a dispatched operation has unknown outcome
- **THEN** its daily charge SHALL remain durable

#### Scenario: Cancellation is not a budget refund
- **WHEN** an order is cancelled
- **THEN** cancellation SHALL remain available without refunding submission turnover
