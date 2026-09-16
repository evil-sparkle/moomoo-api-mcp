# MCP Transport

## ADDED Requirements

### Requirement: Stateless Streamable HTTP Sessions

The streamable HTTP transport SHALL be served statelessly: the server SHALL NOT issue a session identifier, SHALL NOT require one on subsequent requests, and SHALL treat each request as belonging to an already-initialized session.

A session identifier is state held in one process's memory. Holding one means a server restart invalidates it and answers the client's next call with `404`, which is exactly the interruption this transport is meant to avoid on a remote deployment. Issuing none leaves a restart nothing to invalidate.

The accepted cost SHALL be stated rather than discovered: statelessness gives up resumable event streams and server-initiated notifications outside a request. This server uses neither. Logging notifications emitted while a tool call is running still ride that call's own response.

#### Scenario: No session identifier is issued
- **GIVEN** a client initializes against the streamable HTTP endpoint
- **WHEN** the server answers
- **THEN** the response SHALL NOT carry an `mcp-session-id` header
- **AND** the client SHALL be able to make subsequent calls without supplying one

#### Scenario: Calls survive a server restart
- **GIVEN** a client has made at least one successful tool call
- **WHEN** the server process restarts
- **THEN** the client's next call SHALL be served normally
- **AND** SHALL NOT be answered with `404` or a request to re-initialize

#### Scenario: A stale session identifier is ignored, not rejected
- **GIVEN** a client still holds a session identifier from an earlier server or transport
- **WHEN** it sends that identifier on a request
- **THEN** the server SHALL ignore it and serve the request
- **AND** SHALL NOT reject the request on account of it

### Requirement: Process-Scoped Gateway Connections

Connections to the OpenD gateway SHALL be created at most once per server process and shared by every client, rather than created per session or per request. The process SHALL close them once on shutdown, and closing SHALL be safe to attempt more than once.

The MCP lifespan runs inside `Server.run()`, which is entered once per session — and once per *request* when the transport is stateless. Building the gateway connections there would open a fresh pair for every client, pay the trade connect timeout each time, and close them when that client left; under a stateless transport it would do all of that on every tool call. Moving the connections to the process is what makes statelessness affordable.

#### Scenario: Two clients share one set of connections
- **GIVEN** the server process is running
- **WHEN** two clients each make tool calls
- **THEN** both SHALL be served by the same quote and trade connections
- **AND** the second client's arrival SHALL NOT open a second pair

#### Scenario: A client disconnecting leaves the connections open
- **GIVEN** a client has made tool calls and then disconnects
- **WHEN** another client calls a tool afterwards
- **THEN** the existing gateway connections SHALL still be in place
- **AND** the call SHALL NOT wait on a fresh connect

#### Scenario: Connections are opened lazily on first use
- **GIVEN** a server process that no client has called yet
- **WHEN** its logs are inspected
- **THEN** there SHALL be no gateway connection activity
- **AND** this SHALL be understood as correct rather than as a failure to start

#### Scenario: Shutdown closes the connections once
- **GIVEN** gateway connections have been opened in a server process
- **WHEN** the process shuts down
- **THEN** the connections SHALL be closed
- **AND** a second close attempt SHALL be a no-op rather than an error
