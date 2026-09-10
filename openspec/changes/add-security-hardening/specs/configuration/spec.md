## ADDED Requirements

### Requirement: Security Configuration Variables

The system SHALL support security configuration environment variables:
- `MCP_AUTH_TOKEN`: Optional shared secret bearer token for FastMCP HTTP/SSE endpoints.
- `MOOMOO_MAX_ORDER_QTY`: Optional maximum share quantity allowed for a single order.
- `MOOMOO_MAX_ORDER_NOTIONAL`: Optional maximum total notional value allowed for a single order.

#### Scenario: FastMCP bearer token authentication
- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming SSE request supplies a valid `Bearer <token>` Authorization header
- **THEN** FastMCP SHALL accept and process the connection

#### Scenario: Reject unauthorized SSE request when token is required
- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming SSE request supplies an invalid or missing token
- **THEN** FastMCP SHALL reject the request with an authorization error
