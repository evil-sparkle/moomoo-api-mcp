## MODIFIED Requirements

### Requirement: Check Server Health

The system MUST provide a tool to check the health and connectivity of the MCP
server and its downstream OpenD gateway. It SHALL actively probe quote and trade
connectivity, preserve top-level status and host fields, report per-service status
and observation time, and include gateway version information when available.
It SHALL complete within a five-second total deadline and SHALL NOT infer health
from the mere existence of a context object or equate connectivity with unlock.

#### Scenario: Verify connectivity to OpenD

- **WHEN** both quote and trade probes succeed
- **THEN** the tool returns `connected`, the endpoint, observation time, and the
  available gateway version without account contents or credentials.

#### Scenario: Report connection failure

- **WHEN** the gateway is inaccessible, including after successful initialization
- **THEN** the tool returns `disconnected` and a diagnostic error.

#### Scenario: Report partial availability

- **WHEN** exactly one of the quote and trade probes succeeds
- **THEN** the overall status is `degraded` and identifies the failing service.

#### Scenario: Bound unavailable gateway probes

- **WHEN** a probe exceeds the deadline or repeated calls arrive during a stuck probe
- **THEN** health returns a timeout result within the deadline without accumulating
  unbounded background work.

#### Scenario: Serve health after downstream startup failure

- **WHEN** a downstream connection cannot initialize
- **THEN** MCP remains available for health requests and releases partially
  initialized resources on shutdown.
