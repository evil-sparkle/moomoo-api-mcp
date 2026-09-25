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
