# Spec Delta

## MODIFIED Requirements

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
  name a firm the SDK recognizes.

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
