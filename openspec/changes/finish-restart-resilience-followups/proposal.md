## Why

The archived restart-resilience checklist mixes completed work with outstanding
test coverage and diagnostics. Operators cannot see a refused connect-time lock
through health even though connectivity remains available.

## What Changes

- Report the latest connect/reconnect lock attempt in health without broker calls.
- Wait for gateway connections after MCP replacement in the container smoke test.
- Add a PR template prompting a documented behavior/spec impact statement.
- Reconcile the archived follow-ups with current implementation and explicitly
  defer the unverified live REAL unlock-scope experiment as non-blocking.
- Retain JSON-only notification behavior as the existing deliberate decision.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `system-health`: expose connect-time lock attempt observations.

## Impact

Trade service, aggregated health, smoke script, tests, and contributor docs.
No deployment configuration or trading permissions change. The reported hotfix
paper deployment stays in place. An isolated local hotfix test verified unlock
and re-lock, but not cross-connection scope; the documented OpenD-wide behavior
is accepted for the current design.
