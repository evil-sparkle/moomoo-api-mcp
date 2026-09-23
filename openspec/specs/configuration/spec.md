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

### Requirement: Configure Trade Account Market Discovery

The system SHALL support `MOOMOO_TRADING_MARKET` with values `NONE`, `HK`, `US`,
`CN`, `HKCC`, `SG`, `AU`, `JP`, `MY`, and `CA`. Missing or blank configuration
SHALL resolve to `NONE`. Values SHALL be trimmed and normalized to uppercase.
`NONE` SHALL request all securities accounts exposed by the provider for the
configured login and securities firm; a named value SHALL select the provider's
market-filtered discovery behavior. Recognition of a market SHALL NOT imply that
the account or provider supports trading in it.

Invalid values SHALL fail startup before serving a transport, with an error naming
the variable and accepted values. The selected filter SHALL remain fixed for the
process lifetime and survive reconnection without falling back to HK. The standard
container deployment SHALL pass this configuration to the MCP server.

#### Scenario: All-market default

- **WHEN** the server starts with the variable absent, empty, or whitespace-only
- **THEN** its trade account discovery filter SHALL be `NONE`
- **AND** it SHALL NOT implicitly use the provider's HK default

#### Scenario: Explicit legacy scope

- **WHEN** the server starts with `MOOMOO_TRADING_MARKET=" hk "`
- **THEN** its filter SHALL be `HK`, retaining the previous market discovery scope

#### Scenario: Reject a typo or unsupported category

- **WHEN** the variable is `USA`, `FUTURES`, or another unaccepted value
- **THEN** startup SHALL fail with the accepted-value diagnostic
- **AND** no transport SHALL start listening

#### Scenario: Reconnect preserves scope

- **GIVEN** the configured filter is `NONE` or `US`
- **WHEN** the gateway disconnects and reconnects
- **THEN** subsequent account discovery SHALL use the same configured filter

#### Scenario: Market selection does not authorize trading

- **WHEN** an operator selects `US` or `NONE`
- **THEN** environment policy, REAL write allowlists, limits, and lock behavior
  SHALL continue to apply independently of that selection
- **AND** market-data queries SHALL NOT be restricted to that discovery filter
