# Spec Delta

## MODIFIED Requirements

### Requirement: Stateless Streamable HTTP

When served over the Streamable HTTP transport, the MCP server SHALL keep no
per-client session state:

- It SHALL NOT issue a session id, and SHALL NOT require one.
- It SHALL ignore any session id a client presents.
- It SHALL process each request as already initialized.
- It SHALL answer each request with a single JSON response.

Bearer authentication SHALL be evaluated on every request, independently of any
session id. Statelessness is not access control. The endpoint SHALL NOT run
unauthenticated, except under the explicit read-only development opt-out defined by
the `configuration` capability.

This requirement covers the Streamable HTTP transport only. The SSE and stdio
transports are unaffected, apart from the startup authentication rule, which also
applies to SSE.

#### Scenario: No session id is issued

- **GIVEN** the server is running with `MCP_TRANSPORT=streamable-http`
- **WHEN** a client sends `initialize`
- **THEN** the response SHALL NOT carry an `mcp-session-id` header

#### Scenario: A request is served without a prior initialize

- **GIVEN** the server is running with `MCP_TRANSPORT=streamable-http`
- **WHEN** an authenticated client sends `tools/list` or `tools/call` without
  having sent `initialize` to this process
- **THEN** the server SHALL process the request

#### Scenario: A session id the server never issued is ignored

- **GIVEN** the server is running with `MCP_TRANSPORT=streamable-http`
- **WHEN** an authenticated request carries an `mcp-session-id`, including one
  from before a restart
- **THEN** the server SHALL process the request as if no session id were present
- **AND** SHALL NOT reject it as unknown or expired

#### Scenario: A session id does not stand in for authentication

- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** a request carries an `mcp-session-id` but no valid bearer token
- **THEN** the server SHALL reject it with HTTP 401

#### Scenario: A call is answered with its result alone

- **GIVEN** the server is running with `MCP_TRANSPORT=streamable-http`
- **WHEN** a tool emits logging notifications while handling a call
- **THEN** the server SHALL answer with a single JSON response carrying the
  call's result
- **AND** those notifications SHALL NOT be delivered to the client
- **AND** the server SHALL NOT offer resumable event streams or send
  notifications outside a request
