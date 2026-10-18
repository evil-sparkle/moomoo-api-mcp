# Proposal

## Why

Deployment modes currently mix client authority, storage configuration, and execution lifecycle. This prevents safe pausing and observation, makes account selection disagree across workflows, and leaves deployment validation and rollback dependent on mutable settings.

## What Changes

- Add restricted MCP client credentials and explicit environment/account grants, intersected with deployment policy; permit a restricted ChatGPT client on a REAL backend.
- Add persistent operator pauses for new risk while preserving journaled cancellation and recovery in writable deployments.
- Add opt-in observation of existing execution journals and controls after write permissions are disabled.
- Use the same mutation account selection for capability discovery, approval preparation, and execution.
- Validate application settings and storage offline before replacing services; snapshot private effective configuration and immutable image identities for rollback.
- Support all trading presets and explicit tunnel disablement through Ansible, with stable storage and one lifecycle lock.
- **BREAKING**: direct source launches receive the same default USD:500 order cap as Docker; explicit NONE disables it.

## Capabilities

### New Capabilities

- `client-permissions`: restricted client tool, environment, account, and disclosure authorization.
- `execution-lifecycle`: durable operator pause and optional execution observation independent of submission permissions.

### Modified Capabilities

- `configuration`: common default limits, restricted credentials, observation, and offline validation.
- `trading-policy`: explicit observation exceptions to journal-independent read-only operation.
- `container-deployment`: deployment parity, effective configuration snapshots, immutable images, preflight, and shared lifecycle locking.
- `trading-capabilities`: consistent mutation account selection across discovery, preparation, and dispatch.
- `private-chatgpt-access`: restricted clients on REAL deployments without live trading or gateway authority.

## Impact

Python settings, MCP authentication/dispatch, execution services and controls, Docker Compose launchers, Ansible release management, private tunnel preflight, operator HTTP APIs, regression tests, and operator documentation. Existing service-layer mode checks, separate environment journals, exact approvals, and uncertain-outcome protections remain authoritative.
