# configuration Specification

## Purpose

Defines environment variable configuration settings for connecting the MCP server to custom OpenD host addresses and port numbers.

## Requirements

### Requirement: Support Custom Host and Port

The server **MUST** allow configuring the OpenD connection address via environment variables.

#### Scenario: Default Configuration

- Given the environment variables are not set
- When the server starts
- Then it should connect to `127.0.0.1` on port `11111`

#### Scenario: Custom Configuration

- Given `MOOMOO_OPEND_HOST` is set to `192.168.1.100`
- And `MOOMOO_OPEND_PORT` is set to `22222`
- When the server starts
- Then it should connect to `192.168.1.100` on port `22222`

### Requirement: Security Configuration Variables

The system SHALL support security configuration environment variables:
- `MCP_TRANSPORT`: Optional MCP transport mode (`streamable-http`, `sse`, or `stdio`).
- `MCP_AUTH_TOKEN`: Optional shared secret bearer token for FastMCP HTTP/SSE endpoints.
- `MOOMOO_MAX_ORDER_QTY`: Optional maximum share quantity allowed for a single order.
- `MOOMOO_MAX_ORDER_NOTIONAL`: Optional maximum total notional value allowed for a single order.

#### Scenario: FastMCP bearer token authentication
- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP or SSE request supplies a valid `Bearer <token>` Authorization header
- **THEN** FastMCP SHALL accept and process the connection

#### Scenario: Reject unauthorized request when token is required
- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP or SSE request supplies an invalid or missing token
- **THEN** FastMCP SHALL reject the request with an authorization error
