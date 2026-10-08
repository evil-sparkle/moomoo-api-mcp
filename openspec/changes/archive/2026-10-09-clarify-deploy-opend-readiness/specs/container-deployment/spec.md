## ADDED Requirements

### Requirement: Separate OpenD readiness reporting

After successful authenticated MCP deployment verification, a normal deployment
SHALL separately report OpenD readiness using read-only health checks within a
bounded startup window. Readiness SHALL require successful quote and trade
probes and explicit confirmation of quote login. Gateway readiness SHALL NOT
change the meaning of MCP verification or imply trading permission or unlock.

#### Scenario: Remembered login completes during startup

- **WHEN** MCP initialization succeeds and remembered login finishes within the
  readiness window
- **THEN** deployment SHALL report MCP verification and OpenD readiness separately
- **AND** readiness SHALL require both probes to succeed and quote login to be true

#### Scenario: Login is incomplete despite connectivity

- **WHEN** health reports gateway connectivity but quote login is false
- **THEN** deployment SHALL wait within the startup window and, if still unready,
  report that OpenD login is required
- **AND** connectivity alone SHALL NOT be reported as broker readiness

#### Scenario: Gateway remains unavailable or login cannot be confirmed

- **WHEN** the readiness window expires without successful probes and confirmed
  quote login, or the health response is refused or malformed
- **THEN** deployment SHALL clearly report MCP deployment success and unconfirmed
  OpenD readiness with an explicit gateway warning, leaving the verified
  deployment running without rollback and returning deployment success
- **AND** it SHALL NOT claim that every connectivity or permission failure is a
  missing login
- **AND** it SHALL provide conditional initial-login instructions, including
  stopping the background gateway, interactive login with remembered password,
  and restarting the service with interactive mode disabled

#### Scenario: Readiness diagnostics protect credentials

- **WHEN** the authenticated health request is sent and its outcome reported
- **THEN** the resolved token SHALL reach the HTTP client through stdin only
- **AND** health probe diagnostics SHALL NOT print or write credentials, account
  identifiers, response bodies or arbitrary gateway error text
- **AND** partial transfers and unrelated JSON-RPC responses SHALL NOT prove readiness

#### Scenario: Prepare does not start or probe the gateway

- **WHEN** deployment runs with `--prepare`
- **THEN** it SHALL prepare the image without sending health requests

#### Scenario: Public template describes initial setup

- **WHEN** an operator follows the public environment template and deployment runbook
- **THEN** they SHALL be told that the initial interactive login is required before
  `OPEND_INTERACTIVE=0` can reuse remembered state during background operation
