# Spec Delta

## ADDED Requirements

### Requirement: Recovery Uses Ordinary MCP Authentication

Recovery SHALL not require a separate MCP_OPERATOR_TOKEN. The service SHALL accept
only its ordinary configured MCP_AUTH_TOKEN for authenticated MCP requests and
SHALL not expose an operator acknowledgement capability.

#### Scenario: Obsolete operator configuration has no capability
- **WHEN** an environment still defines MCP_OPERATOR_TOKEN
- **THEN** it SHALL not create an accepted bearer credential or a recovery tool
- **AND** paper deployment templates SHALL not provision that variable

#### Scenario: Agent credential reads recovery context
- **WHEN** an ordinary authenticated MCP client invokes execution lookup,
  reconciliation or health
- **THEN** it SHALL receive the server-owned recovery context
- **AND** no client-supplied identity or status SHALL overwrite a journal decision
