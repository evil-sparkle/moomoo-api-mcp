## Why

A fresh deployment can verify MCP initialization while OpenD cannot log in, and
the success message leaves the required interactive setup unclear. Operators
need separate deployment and gateway readiness results.

## What Changes

- Follow authenticated MCP verification with a bounded, read-only `check_health`
  request, allowing remembered login time to complete.
- Report OpenD connectivity and confirmed quote login separately from MCP
  deployment success. Keep a verified deployment available when OpenD is unready.
- Print conditional interactive login and service restart instructions without
  exposing health response bodies or credentials.
- Clarify the required initial login in the public template and deployment docs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `container-deployment`: Add separate post-deployment OpenD readiness reporting
  without changing authenticated MCP verification or rollback decisions.

## Impact

Changes `scripts/deploy.sh`, `scripts/deploy_verify.py`, deployment tests,
`.env.example`, and deployment documentation. No new dependencies or changes to
the OpenD login paths, volumes, tool schema, or trading policy are required.
