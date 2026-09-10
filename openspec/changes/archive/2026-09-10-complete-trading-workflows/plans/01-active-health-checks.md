# R1: Active Gateway Health Checks

Status: Implemented. Priority: High. Dependencies: None.
Requirement: [Check Server Health](../specs/system-health/spec.md).

## Problem and Outcome

`MoomooService.check_health` currently reports connected whenever a quote context
object exists. An object can outlive its connection, and trade connectivity is
not checked. Operators need an observed status they can use to diagnose failure.

Success means health detects quote and trade failures, completes within a bounded
deadline, and remains callable when downstream initialization fails.

## Scope and Requirements

- R1.1: Probe quote and trade connectivity using read-only SDK calls and check their
  return codes. Never use object existence as proof of health.
- R1.2: Preserve top-level `status` and `host`; add per-service results, UTC
  observation time, and gateway version when available.
- R1.3: Report connected for two successful probes, degraded for one, and
  disconnected for neither. A timeout is a failed probe with a diagnostic reason.
- R1.4: Bound total execution to five seconds and prevent accumulating stuck probe
  workers when health is called repeatedly.
- R1.5: Keep MCP available after downstream startup failure and clean up partially
  initialized contexts. Do not expose account contents or credentials in results.

Automatic reconnection, trading unlock, and quote-quality monitoring are excluded.

## Proposed Design

Update `services/base_service.py`, `services/trade_service.py`, `tools/system.py`,
and `server.py`. The quote probe should use global state; select a lightweight
trade read that establishes connectivity without unlocking. Report permission
failures distinctly from transport failures without calling the service healthy.

Keep the MCP tool asynchronous and isolate blocking SDK work. Verify native SDK
timeout support first. If a worker wrapper is needed, allow at most one in-flight
probe per service; an asyncio timeout alone does not stop a synchronous worker.

Proposed result fields: `status`, `host`, `checked_at`, `quote`, `trade`, and optional
`gateway_version`. Each service result contains its status and a sanitized error
when applicable. R3 adds `trading_mode` after policy implementation.

## Implementation Checklist

- [x] Verify global-state/trade probe contracts and SDK timeout behavior.
- [x] Define typed result structures and status aggregation.
- [x] Implement deadline handling and bounded in-flight work.
- [x] Adjust lifespan initialization/cleanup so health survives downstream failure.
- [x] Add tests and update the health tool description and README example.

## Acceptance Tests

| Case | Required observation |
| --- | --- |
| Both probes succeed | Connected with endpoint and timestamp |
| One probe fails | Degraded with correct service diagnostic |
| Gateway disappears after startup | Disconnected, despite existing contexts |
| Probe hangs and health is called repeatedly | Deadline respected; worker count bounded |
| Trade initialization fails after quote initialization | MCP health works; quote context eventually closes |
| Successful connection but locked trading | No claim that orders are authorized or unlocked |

Use deterministic blocking fakes for timeout tests. An optional read-only gateway
check may verify the response shape; unit tests must not need an OpenD instance.

## Rollout, Risks, and Completion

Document new `degraded` status and additive fields for existing health consumers.
Test shutdown after partial startup. Roll back by restoring the previous release,
not by masking failed probes as connected. SDK cancellation limitations must be
resolved before claiming the five-second deadline requirement is implemented.

Done when the acceptance cases and the [shared gates](README.md) pass.
