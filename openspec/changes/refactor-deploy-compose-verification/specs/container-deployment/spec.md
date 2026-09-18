## ADDED Requirements

### Requirement: Deployment Verification

Deployment verification SHALL be performed by a verifier that reads its
authentication token from the configuration Docker Compose resolves for the
MCP service, and SHALL NOT interpret dotenv files itself. The token SHALL be
delivered to the HTTP client through a private channel, and SHALL NOT appear
in process arguments, in files created for the probe, or in printed output.
Verified SHALL mean the MCP endpoint accepted the configured authentication
and returned a valid `initialize` result; it SHALL NOT mean broker login or
trading readiness.

#### Scenario: The verifier reads the resolved service environment

- **GIVEN** the deployment's env files and compose files are in place
- **WHEN** the verifier resolves the authentication token
- **THEN** it SHALL read the MCP service's environment from the
  configuration Compose resolves with the same Docker context, env files and
  compose files that start the container
- **AND** it SHALL NOT parse the env files itself
- **AND** it SHALL use the resolved value unchanged, except that Compose's
  own output escaping — a literal `$` printed as `$$` — is decoded back to the
  value the container received

#### Scenario: An empty token means no authentication

- **GIVEN** Compose resolves `MCP_AUTH_TOKEN` for the service as an explicit
  empty string
- **WHEN** the verifier sends the probe
- **THEN** it SHALL send no Authorization header

#### Scenario: A configuration the verifier cannot use is an error, not a guess

- **GIVEN** Compose cannot resolve the configuration, resolves no token for
  the service, or resolves a value an HTTP Authorization header cannot carry
- **WHEN** the verifier resolves the token
- **THEN** it SHALL fail with a configuration error naming what is wrong
- **AND** it SHALL NOT fall back to reading env files itself
- **AND** it SHALL NOT echo the resolved value

#### Scenario: The token never crosses a visible channel

- **GIVEN** Compose resolves a nonempty token
- **WHEN** the verifier sends the probe
- **THEN** the token SHALL reach the HTTP client through standard input
- **AND** the token SHALL NOT appear in any process arguments
- **AND** no file SHALL be created to hold it
- **AND** the resolved configuration, request headers and response bodies
  SHALL NOT be printed

#### Scenario: Verified means an accepted initialize

- **GIVEN** the deployment has started
- **WHEN** verification runs
- **THEN** the probe SHALL be an MCP `initialize` request authenticated with
  the resolved token
- **AND** a JSON-RPC success response carrying a correctly shaped initialize
  result with the matching request id — as JSON or as an SSE-framed stream —
  SHALL verify
- **AND** an HTTP 200 response alone SHALL NOT verify

#### Scenario: An endpoint that is not answering yet is retried

- **GIVEN** the deployment has just started and the endpoint answers with no
  response or a 5xx status
- **WHEN** verification runs
- **THEN** the probe SHALL be retried within the verification deadline
  measured as elapsed time
- **AND** each attempt SHALL be bounded by the time remaining in the deadline
- **AND** a deadline of zero SHALL mean a single attempt with no retries

#### Scenario: A refused probe fails the deployment immediately

- **GIVEN** the endpoint answers the probe with 401 or 403
- **WHEN** verification runs
- **THEN** the deployment SHALL fail without further retries
- **AND** the failure SHALL diagnose the authentication, naming
  `MCP_AUTH_TOKEN` as the configuration to check

#### Scenario: An invalid MCP response fails the deployment immediately

- **GIVEN** the endpoint answers HTTP 200 with a body that is not a valid
  initialize result
- **WHEN** verification runs
- **THEN** the deployment SHALL fail without further retries
- **AND** the failure SHALL suggest that the verification URL does not point
  at the MCP endpoint

#### Scenario: Configuration failure restores state without restarting

- **GIVEN** a previous deployment is running
- **AND** a new deployment's configuration cannot be resolved after the
  target checkout and settings were written
- **WHEN** the deployment fails
- **THEN** the previous checkout and deployment settings SHALL be restored
- **AND** the running services SHALL NOT be restarted

#### Scenario: Verification failure rolls back with restart

- **GIVEN** a previous deployment is running
- **AND** a new deployment's services were started but verification failed
- **WHEN** the deployment rolls back
- **THEN** the previous checkout and deployment settings SHALL be restored
- **AND** the previous deployment SHALL be restarted
- **AND** a failure to collect diagnostic logs SHALL NOT prevent the rollback

#### Scenario: Verification says nothing about broker login

- **GIVEN** the deployment is verified
- **WHEN** its result is interpreted
- **THEN** verification SHALL NOT be taken to prove that OpenD is logged in
  to the broker or that trading is possible
- **AND** broker login and MCP availability SHALL be confirmed separately
  after each deployment

#### Scenario: The verifier runs from the deployed commit

- **GIVEN** the deploy script re-executes itself from a target commit
- **WHEN** it invokes the verifier
- **THEN** the verifier SHALL run from the checked-out target commit, not
  from the previously checked-out state or the re-executed script's own
  location
