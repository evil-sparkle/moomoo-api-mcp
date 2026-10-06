# Design

## Context

See proposal.md for motivation. Existing CI publishes the brokerage image to ECR; deployment is manual through `scripts/deploy.sh`. The owner wants the official client, accepts READ_ONLY ChatGPT access, and trusts OpenAI HTTPS and the Docker host. Read-only verification of the existing server and its site-specific migration handoff are maintained outside the repository. Actual environment/credential files were not read.

## Goals / Non-Goals

**Goals:** One operator deployment script, ready CI images, explicit optional tunnel selection, all settings in the deployment environment, preserved local clients and persistent state, and independently tested authenticated operation.

**Non-Goals:** CD, a client fork, trading enablement, host-service migration code, manual production image builds, credential-file staging, or archiving before final chat approval.

## Decisions

### Official image and fixed managed runtime

Use unmodified official v0.0.14, tag commit `0f870e50a973fa820d4c409000059e181e8d242b`, archive SHA-256 `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`. Verify integrity before execution; pin container bases and allowlist public build inputs. The client targets `https://api.openai.com` and exactly `http://moomoo-mcp:8000/mcp`. The config digest rejects unexpected configuration changes. The launcher filters inherited proxy, custom-CA and endpoint overrides, gates on authenticated READ_ONLY initialize/discovery/health, suppresses raw client output, reaps children and handles bounded shutdown/liveness recovery. Upstream redirects are accepted under the owner's trusted-endpoint decision, without claiming the client enforces redirect confinement.

### Environment credentials

Compose injects only `MCP_AUTH_TOKEN`, `CHATGPT_TUNNEL_API_KEY` and `CHATGPT_TUNNEL_ID` into the tunnel. The launcher validates them, derives the MCP bearer header, and supplies documented `CONTROL_PLANE_API_KEY`, `CONTROL_PLANE_TUNNEL_ID` and `MCP_AUTHORIZATION` references to the official child, alongside HOME/PATH. It never copies the brokerage environment. Both credentials are deliberately visible to trusted Docker/host administrators; no secret values enter tracked YAML, image layers, argv or diagnostics. Remove root-owned master/staging helpers, secret mounts and mapping/ACL operations. Normal restart/deploy reuses settings; deliberate rotation updates the environment and recreates affected containers. Restart alone retains old environment. MCP rotation also updates authorized local clients.

### One deployment entrypoint

Operators run `deploy.sh --chatgpt [commit]` to enable, `deploy.sh [commit]` to update, and `deploy.sh --no-chatgpt [commit]` to disable. Internal Compose/metadata helpers are implementation details, not extra setup commands. The script verifies matching application and tunnel ECR tags before service changes, records immutable tunnel selection, and automatically preserves the existing Compose project. Non-secret selection persists across deploys/restarts. Both images share the existing ECR repository with separate tag namespaces and independent build caches; main-only publishing, unchanged-image retagging and missing-baseline build fallback remain intact. Rollback restores checkout, deployment inputs and optional selection, including undoing a newly enabled tunnel. Default-off deployment needs no OpenAI settings.

### Container boundaries and acceptance

UID/GID 10002, read-only root, dropped capabilities, no-new-privileges and private tmpfs remain. Production has no secret mounts, Docker socket, shared namespace, brokerage/journal volume or tunnel port publication. Docker DNS uses the existing outbound bridge; OpenD remains brokerage-loopback-only and local MCP publication stays loopback. Server authentication/Host/Origin/trading checks remain authoritative. The bearer is not permanently scoped to read-only: disable the tunnel before changing deployment mode.

Required rootful/rootless tests use the actual official image and fresh brokerage image, synthetic environment credentials, isolated control-plane fixtures, unique projects and random host ports. Cover forwarding, negative auth, environment rotation, proxy filtering, isolation, DNS recovery, state preservation, child recovery/signals and fatal/deadline startup refusal. Lint, types, unit/deployment tests, workflow validation and strict OpenSpec also apply. Removed direct-client diagnostic suites/reports remain only in Git history.

## Risks / Trade-offs

- Environment credentials are visible to Docker/host administrators: explicitly accepted; supply only the ordinary MCP token and limited Read + Use tunnel key.
- Trusted OpenAI redirects retain upstream limitations: accepted; retain official pin and fixed HTTPS endpoint without maintaining a fork.
- Applying images/settings can briefly interrupt MCP: preserve project and volumes and verify authenticated local access afterward.
- Live OpenAI eligibility, ChatGPT web and native iPad access are independent of synthetic success: verify separately after merge.

## Deployment handoff

Keep the site-specific migration guide outside Git, attach/link it in chat and save it on the server. After PR merge and main image publication, perform its steps with existing permissions/credentials; escalate only missing credentials or permissions. The known missing OpenAI runtime key must be supplied through the owner's protected environment procedure before live enablement. Archive/sync OpenSpec after final PR approval in chat, not during implementation.
