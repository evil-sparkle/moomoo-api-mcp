# Spec Delta

## ADDED Requirements

### Requirement: Tunnel Forwarding Preserves HTTP Security

Requests forwarded by the optional tunnel client SHALL use the existing
stateless Streamable HTTP endpoint and SHALL pass the same bearer-authentication,
Host, and Origin checks as direct clients. The final HTTP Host SHALL be derived
from the configured loopback MCP URL. An Origin, when present, SHALL be accepted
only if it exactly matches a reviewed allowed origin; wildcard or disabled
DNS-rebinding protection SHALL NOT be used as a compatibility workaround.

#### Scenario: Expected loopback host
- **WHEN** the tunnel client forwards a request to
  `http://127.0.0.1:8000/mcp`
- **THEN** the MCP request Host SHALL match the existing loopback allowlist
- **AND** no forwarded public Host value SHALL replace it

#### Scenario: Unexpected forwarded Origin
- **WHEN** a tunneled request carries an Origin that is absent from the exact
  reviewed allowlist
- **THEN** the MCP server SHALL reject it
- **AND** the deployment SHALL NOT add a wildcard origin or disable hostname
  protection

#### Scenario: Stateless request after either process restarts
- **WHEN** the MCP server or tunnel client has restarted and both are ready again
- **THEN** the next authenticated request SHALL be processed without an MCP
  session identifier from before the restart

