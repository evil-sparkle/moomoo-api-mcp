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

The system SHALL support the following security configuration environment variables.

- `MCP_TRANSPORT`: MCP transport mode (`streamable-http`, `sse`, or `stdio`).
- `MCP_AUTH_TOKEN`: shared-secret bearer token for the HTTP and SSE transports.
  Required when either of them is selected.
- `MCP_ALLOW_UNAUTHENTICATED_HTTP`: when `1`, permits an HTTP or SSE transport
  without a token. Honoured only when `MOOMOO_TRADING_MODE` is `READ_ONLY`.
- `MOOMOO_MAX_ORDER_QTY`: optional finite, positive maximum quantity for a single
  order, or for a combo's largest leg.
- `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`: optional currency-qualified caps on a
  single order's notional value, in the form `CURRENCY:AMOUNT[,CURRENCY:AMOUNT...]`.
- `MOOMOO_MAX_ORDER_NOTIONAL`: legacy, unit-less cap.
  - This version never applies it as a limit.
  - It is ignored when `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is set, so one
    environment file can serve both this version and an earlier one during a
    rollback.
  - Set on its own, it is a configuration error.
- `MOOMOO_REAL_ACC_IDS`: comma-separated REAL account identifiers that REAL writes
  may target. Required when `MOOMOO_TRADING_MODE` is `REAL`.
- `MOOMOO_SECURITY_FIRM`: optional securities-firm identifier. When set, it SHALL
  name an actual securities firm the SDK defines. The SDK's firm enumeration also
  carries a not-applicable placeholder; that placeholder SHALL NOT be accepted as a
  configured firm.

An invalid value for any of these SHALL fail startup with a configuration error that
names the variable. The server SHALL NOT fall back to a default in its place.

#### Scenario: FastMCP bearer token authentication

- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP or SSE request supplies a valid `Bearer <token>`
  Authorization header
- **THEN** FastMCP SHALL accept and process the connection

#### Scenario: Reject unauthorized request when token is required

- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP or SSE request supplies an invalid or missing token
- **THEN** FastMCP SHALL reject the request with an authorization error

#### Scenario: HTTP transport without a token refuses to start

- **GIVEN** `MCP_TRANSPORT` is `streamable-http` or `sse`
- **AND** `MCP_AUTH_TOKEN` is unset or blank
- **AND** `MCP_ALLOW_UNAUTHENTICATED_HTTP` is not `1`, or `MOOMOO_TRADING_MODE` is
  not `READ_ONLY`
- **WHEN** the server starts
- **THEN** it SHALL exit with a configuration error naming `MCP_AUTH_TOKEN`
- **AND** SHALL NOT listen on the HTTP port

#### Scenario: Explicit unauthenticated read-only development

- **GIVEN** `MCP_TRANSPORT` is `streamable-http`, `MCP_AUTH_TOKEN` is blank,
  `MCP_ALLOW_UNAUTHENTICATED_HTTP` is `1`, and `MOOMOO_TRADING_MODE` is `READ_ONLY`
- **WHEN** the server starts
- **THEN** it SHALL serve without authentication and log a warning saying so

#### Scenario: stdio needs no token

- **GIVEN** `MCP_TRANSPORT` is `stdio`
- **WHEN** the server starts without `MCP_AUTH_TOKEN`
- **THEN** it SHALL start normally

#### Scenario: One environment file serves old and new images

- **GIVEN** the environment sets `MOOMOO_MAX_ORDER_NOTIONAL=25000` and
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:25000`
- **WHEN** this version starts
- **THEN** it SHALL start, enforce the currency-qualified cap, and log that the
  legacy variable is ignored

#### Scenario: Unrecognized security firm

- **WHEN** `MOOMOO_SECURITY_FIRM` is set to a value the SDK does not define
- **THEN** startup SHALL fail with a configuration error listing the valid values

#### Scenario: The not-applicable placeholder is not a firm

- **WHEN** `MOOMOO_SECURITY_FIRM` is set to the SDK's not-applicable placeholder
- **THEN** startup SHALL fail with a configuration error
- **AND** the listed valid values SHALL NOT include that placeholder

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
