## MODIFIED Requirements

### Requirement: Security Configuration Variables

The system SHALL support the following security configuration environment variables.

#### Scenario: Security configuration parsing contract

- **WHEN** the service loads security configuration
- **THEN** the following detailed obligations SHALL hold:

- `MCP_TRANSPORT`: optional transport setting; only `streamable-http` is accepted,
  and it is the default for both local and container startup.
- `MCP_AUTH_TOKEN`: required shared-secret bearer token for the HTTP endpoint.
- `MCP_ALLOW_UNAUTHENTICATED_HTTP`: when `1`, permits an HTTP transport
  without a token. Honoured only when `MOOMOO_TRADING_MODE` is `READ_ONLY`.
- `MOOMOO_MAX_ORDER_QTY`: optional finite, positive maximum quantity for a single
  order, or for a combo's largest leg.
- `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`: optional currency-qualified caps on a
  single order's notional value, in the form `CURRENCY:AMOUNT[,CURRENCY:AMOUNT...]`.
  - All launchers SHALL supply `USD:500` when this setting is unset or empty.
  - Launchers SHALL preserve a nonempty explicit value; `NONE` SHALL disable the cap.
- `MOOMOO_REAL_ACC_IDS`: comma-separated REAL account identifiers that REAL writes
  may target. Required when `MOOMOO_TRADING_MODE` is `REAL`.
- `MOOMOO_SECURITY_FIRM`: optional securities-firm identifier. When set, it SHALL
  name an actual securities firm the SDK defines. The SDK's firm enumeration also
  carries a not-applicable placeholder; that placeholder SHALL NOT be accepted as a
  configured firm.

An invalid value for any of these SHALL fail startup with a configuration error that
names the variable. The server SHALL NOT fall back to a default in its place.

#### Scenario: Compose supplies a default notional cap

- **GIVEN** the notional-cap environment setting is unset or empty
- **WHEN** the base or production Compose configuration is rendered
- **THEN** the server environment SHALL contain
  `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:500`

#### Scenario: Compose preserves an explicit notional cap

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1500,HKD:8000`
- **WHEN** the base or production Compose configuration is rendered
- **THEN** the server SHALL receive and parse those currency-qualified caps

#### Scenario: FastMCP bearer token authentication

- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP request supplies a valid `Bearer <token>`
  Authorization header
- **THEN** FastMCP SHALL accept and process the connection

#### Scenario: Reject unauthorized request when token is required

- **GIVEN** `MCP_AUTH_TOKEN` is configured
- **WHEN** an incoming HTTP request supplies an invalid or missing token
- **THEN** FastMCP SHALL reject the request with an authorization error

#### Scenario: HTTP transport without a token refuses to start

- **GIVEN** `MCP_TRANSPORT` is `streamable-http` or unset
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

#### Scenario: Removed transports fail startup

- **GIVEN** `MCP_TRANSPORT` is `sse`, `stdio`, or another unsupported value
- **WHEN** the server starts
- **THEN** it SHALL exit with a configuration error naming `MCP_TRANSPORT`
- **AND** SHALL NOT start a listener or fall back to another transport

#### Scenario: Unrecognized security firm

- **WHEN** `MOOMOO_SECURITY_FIRM` is set to a value the SDK does not define
- **THEN** startup SHALL fail with a configuration error listing the valid values

#### Scenario: The not-applicable placeholder is not a firm

- **WHEN** `MOOMOO_SECURITY_FIRM` is set to the SDK's not-applicable placeholder
- **THEN** startup SHALL fail with a configuration error
- **AND** the listed valid values SHALL NOT include that placeholder

### Requirement: Separate Durable REAL Journal Configuration

REAL startup SHALL require `MOOMOO_REAL_JOURNAL_PATH` and accept explicit `MOOMOO_CREATE_REAL_JOURNAL=0|1`, defaulting to 0. REAL and SIMULATE stores SHALL use distinct directories and immutable environment metadata. Startup SHALL validate and lock both configured stores before listening, preserve existing paper storage, and fail closed for missing, corrupt, incompatible or competing storage. READ_ONLY SHALL open neither store unless explicit observation is enabled; observation SHALL NOT enable writes.

#### Scenario: Live deployment requires initialized persistence
- **WHEN** REAL mode lacks its path or initialization is disabled for a missing database
- **THEN** startup SHALL fail before HTTP listens without modifying existing execution records

#### Scenario: Environment or directory collision
- **WHEN** REAL points to a paper database or shares the paper journal directory
- **THEN** startup SHALL refuse instead of migrating records across trading environments

#### Scenario: Read-only and paper compatibility
- **WHEN** the server runs READ_ONLY or SIMULATE without explicit observation
- **THEN** no REAL journal SHALL be opened and existing paper behavior SHALL be preserved

### Requirement: Optional Durable Trading Controls
The server SHALL support optional symbol limits, currency-qualified daily budgets, approval requirement and explicitly initialized persistent controls storage. Required controls with unavailable storage SHALL fail startup before listening. READ_ONLY SHALL open configured existing controls only for explicit observation or a configured trusted operator channel. Defaults SHALL preserve existing execution behavior.

#### Scenario: Missing required controls storage
- **WHEN** approval or daily budgets are enabled without valid initialized storage
- **THEN** startup SHALL fail closed with a safe configuration error

#### Scenario: Compose forwards explicit controls
- **WHEN** the operator configures symbol restrictions, daily budgets, approval requirements or operator credentials in the Compose environment file
- **THEN** the MCP server SHALL receive those settings
- **AND** omitted symbol restrictions and daily budgets SHALL remain disabled while explicitly empty values SHALL fail validation

#### Scenario: Container controls use independent durable storage
- **WHEN** daily budgets, approvals or an operator channel are selected in a writable production deployment
- **THEN** the production wrapper SHALL include `docker-compose.controls.yml`, with or without a selected ChatGPT tunnel
- **AND** this overlay SHALL mount an independent `trading-controls-data` volume at `/var/lib/moomoo/trading-controls`, prepared for UID 10001
- **AND** first initialization SHALL require explicit `MOOMOO_CREATE_TRADING_CONTROLS=1`, with normal operation using `0`

### Requirement: Optional Tunnel Environment Configuration

The optional Compose service SHALL receive `CHATGPT_TUNNEL_API_KEY`,
`CHATGPT_TUNNEL_ID` and the selected restricted MCP token or existing ordinary `MCP_AUTH_TOKEN` through explicit
environment injection.

#### Scenario: Optional tunnel credential validation contract

- **WHEN** an operator configures the optional tunnel service
- **THEN** the following detailed obligations SHALL hold:

The public template SHALL document the settings and the
allowed READ_ONLY and SIMULATE modes, REAL only with a restricted client lacking live-write and gateway-control grants, and
the existing paper configuration required in SIMULATE. The runtime key SHALL be nonblank printable
ASCII; the selected identifier SHALL match `tunnel_[a-zA-Z0-9_-]{1,128}` after
normalization. Credentials SHALL NOT be required by default-off deployments or
tunnel stop/removal operations. Startup validation SHALL reject missing or invalid
inputs without printing their values.

#### Scenario: Enabled inputs are complete

- **WHEN** the optional service is enabled with valid settings
- **THEN** both OpenAI settings and the selected MCP token SHALL reach only the intended tunnel environment
- **AND** the launcher SHALL derive the configured local bearer header without a separate file

#### Scenario: Invalid optional input

- **WHEN** an enabled deployment has a missing or invalid runtime key, tunnel identifier or selected MCP bearer
- **THEN** deployment validation SHALL fail before service startup with a safe diagnostic
- **AND** no credential value SHALL be printed

#### Scenario: Optional credentials are unnecessary while disabled

- **WHEN** the optional service is absent or stopped/removed
- **THEN** ordinary deployment or disablement SHALL succeed without OpenAI settings

## ADDED Requirements

### Requirement: Offline effective configuration validation
Deployment preflight SHALL validate the selected application's effective settings and mounted storage without connecting to the broker, taking execution locks, initializing databases, or revealing credential values. Missing storage SHALL require an explicit initialization flag.

#### Scenario: Invalid writable configuration
- **WHEN** effective writable settings lack required accounts or journal storage
- **THEN** preflight SHALL fail before replacing the healthy running application
