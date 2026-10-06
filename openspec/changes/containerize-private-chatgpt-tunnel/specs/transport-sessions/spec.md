# Spec Delta

## MODIFIED Requirements

### Requirement: Tunnel Forwarding Preserves HTTP Security

In the fixed managed deployment, requests forwarded by the optional tunnel client
SHALL use the existing stateless Streamable HTTP endpoint and SHALL pass the same bearer-authentication,
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

#### Scenario: Preflight refuses redirect or proxy diversion

- **WHEN** preflight encounters a redirect to an unapproved destination or inherited proxy configuration
- **THEN** protected Authorization headers SHALL NOT reach that destination or proxy
- **AND** preflight SHALL refuse redirects and ignore inherited proxy configuration

#### Scenario: Managed client excludes inherited proxies

- **WHEN** the managed container launches the official forwarding client
- **THEN** its child environment SHALL exclude inherited proxy, CA-bundle and endpoint/configuration overrides
- **AND** actual-entrypoint tests SHALL demonstrate that poisoned parent settings do not route credential-bearing requests to a fixture proxy

#### Scenario: Trusted control-plane configuration

- **WHEN** the managed runtime starts the official client
- **THEN** its approved configuration SHALL use `https://api.openai.com` with normal certificate verification and the pinned private MCP URL
- **AND** unexpected configuration changes SHALL fail startup
- **AND** upstream redirect behavior SHALL remain documented as a conditional limitation rather than an integration-enforced redirect policy

#### Scenario: Normal operation and managed controls are required

- **WHEN** compatibility is verified through the final managed image and official binary
- **THEN** normal authenticated control-plane polling/response delivery and MCP discovery/startup/forwarding SHALL succeed against isolated fixtures
- **AND** preflight redirect refusal, managed proxy filtering and missing/wrong/conflicting credential refusal SHALL pass
- **AND** preventing all traffic or replacing the official binary with a stub SHALL NOT count as successful compatibility
