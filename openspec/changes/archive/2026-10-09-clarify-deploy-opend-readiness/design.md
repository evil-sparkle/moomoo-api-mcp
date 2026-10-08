## Context

See proposal.md for motivation. The existing host-side verifier uses Compose's
resolved token and curl with private stdin headers. MCP stays available without
OpenD. `check_health` already exposes quote/trade probe results and an optional
boolean `quote.logged_in`; its overall `connected` status describes connectivity.

## Goals / Non-Goals

**Goals:** Reuse the authenticated HTTP probe, separately confirm gateway probes
and quote login, and give actionable recovery guidance without changing rollback.

**Non-Goals:** Automate credential entry, retry broker login, change health tool
semantics, validate market permissions, unlock trading, or change persistent state.

## Decisions

- Add a `gateway-readiness` verifier command that sends `tools/call check_health`
  with the same token handling. Reuse HTTP and JSON/SSE framing validation so
  partial transfers and mismatched responses cannot become ready results.
- Require `connected`, both per-service statuses `ok`, and `quote.logged_in is
  True`. Missing login evidence is unconfirmed, not ready. Return only fixed
  diagnostic labels, never broker error strings or account information.
- Use a separate `DEPLOY_GATEWAY_TIMEOUT` (60 seconds by default, zero means one
  attempt). Retry unavailable probes or login still warming up within that window;
  malformed responses and authentication refusals stop the readiness check.
- Keep MCP verification as the rollback gate. Gateway warnings retain exit zero
  for a verified deploy, and the success message names MCP explicitly. The helper
  can return nonzero for unready health without invoking Bash rollback.
- Show interactive recovery conditionally: stop the existing application service,
  run the temporary interactive container, choose to remember the password and
  stop it, then start the background service with `OPEND_INTERACTIVE=0`.
  This avoids concurrently running two gateways against the same login volume.

## Risks / Trade-offs

- A network or permission failure can resemble missing login state → report
  unavailable/unknown separately and describe interactive login conditionally.
- An older gateway may omit quote-login evidence → report readiness unconfirmed
  rather than silently inferring successful login from a reachable socket.
- Gateway readiness adds up to a minute to deployment → use a separate configurable
  deadline, bound HTTP attempts, and leave `--prepare` unaffected.
- A successful health check does not grant trading permission → docs and output
  describe quote login and probes only.

## Migration Plan

Deploy through the existing published-image workflow. No state migration or new
credentials are needed. Rolling back restores the previous reporting behavior.
