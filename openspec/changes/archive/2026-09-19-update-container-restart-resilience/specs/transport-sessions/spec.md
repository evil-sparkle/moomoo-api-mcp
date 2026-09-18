## ADDED Requirements

### Requirement: Stateless Streamable HTTP

When served over the Streamable HTTP transport, the MCP server SHALL keep no
per-client session state. It SHALL NOT issue a session id, SHALL NOT require one,
SHALL ignore any session id a client presents, and SHALL process each request as
already initialized. It SHALL answer each request with a single JSON response.
When `MCP_AUTH_TOKEN` is configured, bearer authentication SHALL be evaluated on
every request, independently of any session id. Statelessness is not access
control: without a configured token the endpoint is unauthenticated. This
requirement covers the Streamable HTTP transport only; the SSE and stdio
transports are unaffected.

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

### Requirement: Process-Owned Gateway Connections

The MCP server SHALL own its OpenD quote and trade connections at process
scope. It SHALL open them at most once per process, on the first request it
serves, and SHALL share them across every request, session and client. It
SHALL NOT open or close gateway connections when a request or session begins or
ends, and SHALL release them when the process exits. Failure to reach the
gateway SHALL NOT prevent the server from serving requests, including
`check_health`.

#### Scenario: A fresh process has not dialled the gateway

- **GIVEN** the MCP server process has just started
- **WHEN** no client has sent a request yet
- **THEN** the server SHALL NOT have opened any gateway connection

#### Scenario: Requests and clients share one set of connections

- **GIVEN** the server has opened its gateway connections for an earlier request
- **WHEN** further requests arrive, from the same client or a different one
- **THEN** they SHALL use the same quote and trade connections
- **AND** the server SHALL NOT construct additional gateway connections for them

#### Scenario: Ending a request or session leaves the connections open

- **GIVEN** the server holds open gateway connections
- **WHEN** a request completes or a client disconnects
- **THEN** the gateway connections SHALL remain open for later requests

#### Scenario: Connections are released at process exit

- **GIVEN** the server holds open gateway connections
- **WHEN** the process shuts down
- **THEN** the server SHALL close each connection exactly once, even if shutdown
  runs more than once

#### Scenario: An unreachable gateway does not stop the server

- **GIVEN** OpenD is unreachable when the server opens its gateway connections
- **WHEN** a client sends a request
- **THEN** the server SHALL serve it, returning an error for any gateway-backed
  operation that cannot complete
- **AND** `check_health` SHALL remain callable and report the gateway as
  unavailable
