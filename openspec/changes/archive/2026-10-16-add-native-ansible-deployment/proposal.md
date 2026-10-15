# Native Ansible and CI/CD deployment

## Why

Moomoo publishes tested images but requires a manual deployment script and Git
checkout on the application host. CCXT already owns its Ansible role in its
application repository and deploys verified releases through GitHub Actions.
Moomoo needs that arrangement while preserving remembered OpenD login, state
volumes and separate MCP/broker readiness checks.

## What Changes

- Add an application-owned Ansible release role and dedicated inventory scope.
- Publish a public CI manifest binding application/tunnel digests to the source
  commit, CI run and attempt, including optimized retagged images.
- Add automatic successful-main deployment and manual release/rollback workflows
  using ephemeral Tailscale OIDC access and pinned ordinary SSH host trust.
- Preserve existing rootless Docker, protected server-side configuration,
  Compose project and exact named state volumes.
- Keep the initial migration READ_ONLY and retain the legacy bootstrap/writable
  deployment path. Do not change trading behavior or credential contents.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `container-deployment`: add native release provenance, state-preserving
  migration, verification/rollback and deployment authority after cutover.

## Impact

Application deployment workflows, public host/controller helpers, a new Ansible
role, standalone synthetic deployment tests and deployment documentation. GitHub
environment, SSH and tailnet access require separate operator setup. No live
host or cloud resource is changed by this source implementation.
