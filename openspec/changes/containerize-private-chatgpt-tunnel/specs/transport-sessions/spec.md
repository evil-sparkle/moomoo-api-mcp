# Spec Delta

## MODIFIED Requirements

### Requirement: Tunnel Forwarding Preserves HTTP Security

Requests forwarded by the optional tunnel client SHALL use the existing
stateless Streamable HTTP endpoint and SHALL pass the same bearer-authentication,
Host, and Origin checks as direct clients. The final HTTP Host SHALL be derived
from the approved MCP URL: the existing loopback URL for legacy use, or exactly
`http://moomoo-mcp:8000/mcp` for explicitly enabled Compose integration. An Origin, when present, SHALL be accepted
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

#### Scenario: Exact Compose Host opt-in

- **WHEN** the integration's explicit server setting is enabled and an authenticated request carries Host `moomoo-mcp:8000`
- **THEN** the server SHALL accept that Host while retaining all existing bearer and Origin checks
- **AND** the same Host SHALL be rejected with the setting disabled
- **AND** other Docker names, suffixes, ports and unexpected Hosts SHALL remain rejected

#### Scenario: Preflight destination opt-in

- **WHEN** Compose destination support is not explicitly selected
- **THEN** preflight SHALL continue to accept only existing loopback HTTP MCP URLs
- **AND** explicit Compose selection SHALL add only the exact URL `http://moomoo-mcp:8000/mcp`, not arbitrary private-network URLs

#### Scenario: Redirect or proxy attempts credential diversion

- **WHEN** preflight or official-client discovery/startup/forwarding encounters a redirect to an unapproved destination or inherited proxy configuration
- **THEN** protected Authorization headers SHALL NOT reach that destination or proxy
- **AND** preflight SHALL refuse redirects and ignore inherited proxy configuration
- **AND** container forwarding SHALL be configured and behaviorally verified to preserve approved-origin credential confinement


#### Scenario: Control-plane runtime key remains destination confined

- **WHEN** the official client handles a control-plane request or any redirect hop
- **THEN** the OpenAI runtime credential SHALL NOT be injected into or retained on a request to an unapproved destination
- **AND** redirect checks SHALL prevent protected headers or bodies from reaching an unapproved destination
- **AND** this requirement SHALL be tested separately from ordinary MCP bearer confinement

#### Scenario: Normal operation and confinement are both required

- **WHEN** compatibility is verified for the final official binary
- **THEN** normal authenticated control-plane polling/response delivery and MCP discovery/startup/forwarding SHALL succeed against isolated fixtures
- **AND** redirect/proxy confinement and missing/wrong/conflicting credential refusal SHALL also pass for their respective paths
- **AND** preventing all traffic or replacing the official binary with a stub SHALL NOT count as successful compatibility
