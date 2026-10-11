## ADDED Requirements

### Requirement: Optional Durable Trading Controls
The server SHALL support optional symbol limits, currency-qualified daily budgets, approval requirement and explicitly initialized persistent controls storage. Required controls with unavailable storage SHALL fail startup before listening. READ_ONLY SHALL not open controls storage. Defaults SHALL preserve existing execution behavior.

#### Scenario: Missing required controls storage
- **WHEN** approval or daily budgets are enabled without valid initialized storage
- **THEN** startup SHALL fail closed with a safe configuration error

### Requirement: Separate Operator Approval Credential
The optional operator approval endpoint SHALL require a distinct credential and server-configured operator identity. It SHALL not change ordinary MCP or recovery authentication. Equal ordinary and operator credentials SHALL be rejected without printing either value.

#### Scenario: Credential separation
- **WHEN** the configured operator credential equals the ordinary MCP credential
- **THEN** startup SHALL fail rather than let an agent approve its own mutations
