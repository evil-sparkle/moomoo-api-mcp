# Proposal

## Why

Private ChatGPT access should deploy through the existing manual deployment script and CI-published ECR images. Operators should only configure the deployment environment and run one command, without building another image, staging credentials or maintaining a tunnel-client fork.

## What Changes

- Add a default-off, separate official-client Compose service with independent recovery and no published ports, brokerage state or shared namespaces.
- Build both application and tunnel images in existing CI and publish them on main to the existing ECR repository, using distinct tunnel tags and immutable production selection.
- Make `scripts/deploy.sh` the sole operator deployment entrypoint: `--chatgpt` enables and saves selection, normal deploys retain it, and `--no-chatgpt` disables it. Image selection and the existing Compose project are automatic.
- Supply `CHATGPT_TUNNEL_API_KEY`, `CHATGPT_TUNNEL_ID` and the existing `MCP_AUTH_TOKEN` through explicit Compose environment injection. Update the public example template. Remove mounted-secret provisioning and legacy host-service assets.
- Retire the unverified OpenD password-MD5 startup path. Document one-time interactive login followed by persistent remembered login, and remove the obsolete setting from Compose and the public template.
- Preserve authenticated READ_ONLY startup, exact MCP Host/destination opt-ins, proxy filtering, fixed official HTTPS endpoint, server trading policy and bounded process recovery.
- Trust the official OpenAI endpoint and Docker host; accept the unchanged upstream redirect limitation. Remove old diagnostic code/jobs and dated reports; original findings remain in Git history.
- Keep site-specific migration instructions outside the repository, deliver them in chat and on the server, and perform deployment after PR merge using available permissions and credentials.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `private-chatgpt-access`: optional Compose runtime, environment credentials, READ_ONLY controls, reproducible operations and current acceptance.
- `container-deployment`: separate hardened tunnel container, interactive/remembered OpenD login, and one-script CI-image deployment with persistent project/volume identity and rollback.
- `transport-sessions`: exact opted-in Docker Host/destination, preserved authentication and proxy filtering.
- `configuration`: default-off exact Host setting and explicit optional deployment configuration.

## Impact

Changes affect Compose, the official image/launcher, deployment helpers, CI, tests and runbooks. The official v0.0.14 release/integrity pin and brokerage trading policy remain unchanged. Runtime credentials are deliberately visible to trusted Docker/host administrators. Production deployment waits for PR merge; OpenAI workspace/product acceptance remains a separate live check. Archive and sync this OpenSpec change only after the owner gives final PR approval in chat.
