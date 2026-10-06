# Spec Delta

## ADDED Requirements

### Requirement: Explicit Compose Tunnel Host Configuration

The server SHALL support `MCP_ALLOW_CHATGPT_TUNNEL_HOST` with absent, blank or `0` meaning disabled and `1` meaning enabled. Any other value SHALL fail startup naming the setting. Enabled SHALL add only exact Host `moomoo-mcp:8000` to existing accepted Hosts and SHALL NOT change the Origin allowlist, disable DNS-rebinding protection, alter authentication, or change trading mode. The integration overlay SHALL select this setting explicitly; default deployments SHALL leave it disabled.

#### Scenario: Default settings remain secure

- **WHEN** the setting is absent, blank or `0`
- **THEN** localhost behavior SHALL remain unchanged and the Docker service Host SHALL not be trusted

#### Scenario: Explicit setting enables one Host

- **WHEN** the setting is `1`
- **THEN** only the exact additional Host SHALL be trusted with ordinary authentication still required
- **AND** unexpected Origins SHALL remain rejected

#### Scenario: Invalid setting fails closed

- **WHEN** the setting is another value
- **THEN** the server SHALL fail before listening with a safe configuration error naming the setting


### Requirement: Optional Tunnel Environment Configuration

The optional Compose service SHALL receive `CHATGPT_TUNNEL_API_KEY`,
`CHATGPT_TUNNEL_ID` and the existing ordinary `MCP_AUTH_TOKEN` through explicit
environment injection. The public template SHALL document the settings and the
required READ_ONLY deployment mode. The runtime key SHALL be nonblank printable
ASCII; the selected identifier SHALL match `tunnel_[a-zA-Z0-9_-]{1,128}` after
normalization. Credentials SHALL NOT be required by default-off deployments or
tunnel stop/removal operations. Startup validation SHALL reject missing or invalid
inputs without printing their values.

#### Scenario: Enabled inputs are complete

- **WHEN** the optional service is enabled with valid settings
- **THEN** both OpenAI settings and the ordinary MCP token SHALL reach only the intended tunnel environment
- **AND** the launcher SHALL derive the configured local bearer header without a separate file

#### Scenario: Invalid optional input

- **WHEN** an enabled deployment has a missing or invalid runtime key, tunnel identifier or ordinary MCP bearer
- **THEN** deployment validation SHALL fail before service startup with a safe diagnostic
- **AND** no credential value SHALL be printed

#### Scenario: Optional credentials are unnecessary while disabled

- **WHEN** the optional service is absent or stopped/removed
- **THEN** ordinary deployment or disablement SHALL succeed without OpenAI settings
