# Private ChatGPT access through the official tunnel

The optional Compose service runs the unmodified OpenAI tunnel client. ChatGPT
access requires server-enforced `READ_ONLY`; order placement, modification,
cancellation, trade unlocking and operator recovery are refused. The ordinary
MCP bearer is not a permanently read-only credential: disable the tunnel before
changing the deployment to SIMULATE or REAL.

## Configure and deploy

Use `scripts/deploy.sh` for manual deployment. GitHub Actions builds both images
and publishes them to the existing `moomoo-api-mcp` ECR repository on main pushes.
There is no CD. PR image builds do not publish. Application tags are seven-character
commit IDs; tunnel tags are `tunnel-<commit>`. Unchanged images are retagged for each
main commit, with a build fallback if the baseline is unavailable. Wait for both
image jobs to succeed. Production hosts need no separate image build or secret
provisioning script.

Add these settings to the deployment `.env`, using `.env.example` as the template:

```dotenv
MOOMOO_TRADING_MODE=READ_ONLY
MCP_AUTH_TOKEN=<existing ordinary MCP token>
CHATGPT_TUNNEL_API_KEY=<OpenAI tunnel runtime key with Tunnels Read and Use>
CHATGPT_TUNNEL_ID=<selected tunnel identifier beginning tunnel_>
```

The launcher derives the MCP Authorization header automatically. Supply a limited
OpenAI runtime key, never an admin key, brokerage login, trade password or operator
token. The tunnel receives only the three named tunnel inputs; the deployment
`.env` is not loaded wholesale into that container. Protect the host file and
avoid displaying resolved Compose configuration or container environments.
Credentials are visible to trusted Docker/host administrators through the
container and child-process environment; this is an accepted deployment trade-off.

Enable once, after main CI publishes the images:

```bash
./scripts/deploy.sh --chatgpt
```

Subsequent `./scripts/deploy.sh [commit]` deployments retain the selection. The
script confirms both ECR tags before checkout, saves the immutable tunnel digest,
pulls both images, validates authentication and READ_ONLY settings, then starts
the stack. First enablement detects the running container's Compose project and
saves that project for subsequent enabled and disabled deployments, preserving
its named volumes. A new host uses the checkout directory's default project.
Missing credentials fail before startup. A missing tunnel image fails before
checkout or service changes. `--prepare --chatgpt` validates and pulls without
starting services; ordinary deployments keep the tunnel off unless selected.

The application and tunnel run as separate containers. OpenD remains on brokerage
container loopback `127.0.0.1:11111`, unpublished. Host MCP remains
`127.0.0.1:8000:8000`; the tunnel reaches exactly `http://moomoo-mcp:8000/mcp`
through Docker DNS. Enabling the overlay opts the server into only that additional
Host. Existing bearer, Host and Origin checks still apply. The tunnel publishes
no ports and shares no PID/network namespace, Docker socket or brokerage volumes.
It runs as UID/GID `10002:10002`, with a read-only root, dropped capabilities,
no-new-privileges and private runtime tmpfs. The production bridge permits egress.

## Trust and upstream limitations

This deployment trusts the genuine `https://api.openai.com` endpoint with normal
certificate verification and its Docker host. Configuration fixes the OpenAI and
private MCP destinations; startup verifies the configuration digest and removes
inherited proxy, CA-bundle and endpoint overrides. The official client's upstream
redirect behavior is accepted within this trust model; the integration does not
patch that client. Preflight itself refuses redirects and ignores proxies.

[Earlier investigation results](https://github.com/evil-sparkle/moomoo-api-mcp/tree/76470318f3d9dbe3eca51ee10902580d50dfad1c/openspec/changes/containerize-private-chatgpt-tunnel)
record conditional redirect/proxy and unauthenticated-doctor limitations. They do
not establish a malicious OpenAI redirect or actual credential exposure. Those
reports and diagnostic runners are removed from the current tree; Git retains the
records. Current CI tests the managed deployment with the real pinned client on
rootful and rootless Docker, using synthetic credentials and isolated resources.

## Startup, checks and recovery

Before launching the client, the manager performs authenticated MCP initialize,
tool discovery and `check_health`, requiring READ_ONLY. Transient MCP failures
retry within a 90-second deadline; invalid credentials, mode or results fail
closed. Every client launch repeats the gate. Degraded broker connectivity can
still pass when MCP itself is available and READ_ONLY; that does not prove broker
login or data access.

The manager suppresses raw official-client output and prints bounded status
messages without secrets or account data. It forwards stop signals, reaps the
client and exits nonzero on client exit or sustained failed local liveness. Docker
restarts only the optional tunnel. Local `/healthz` proves process liveness and
`/readyz` proves client startup; neither proves live OpenAI forwarding. An upstream
outage does not trigger a restart loop merely because forwarding is unavailable.

Safe local diagnostics, after deployment:

```bash
./scripts/compose-prod.sh ps
./scripts/compose-prod.sh logs --tail=100 chatgpt-tunnel
./scripts/compose-prod.sh exec -T chatgpt-tunnel python /opt/tunnel/runtime.py diagnostics
```

`compose-prod.sh` is the internal wrapper used by deployment and service management;
it resolves the same files, rootless context, saved project and immutable image.
`build-tunnel-image.sh` is a developer/CI fixture helper. Neither is an additional
operator provisioning entrypoint.

## Credentials and restarts

A normal process/container restart reuses its configured credentials. It does not
require a new token or editing the ChatGPT connection. A deliberate environment
change requires container recreation; `docker restart` retains the old values.

For an OpenAI key rotation, update `CHATGPT_TUNNEL_API_KEY` in `.env` and run
`./scripts/deploy.sh`. Compose recreates the affected tunnel with the new value.
Verify actual forwarding with the replacement key, then confirm the revoked key
is refused. Rotating this key does not rotate the ordinary MCP bearer.

For an MCP bearer rotation, disable the tunnel with the command below, update
`MCP_AUTH_TOKEN` and authorized local client settings, then run
`./scripts/deploy.sh` to recreate MCP. Verify the new bearer is accepted and the
old bearer returns 401. Enable again with `./scripts/deploy.sh --chatgpt`; it
receives the same new value automatically. Verify forwarded MCP traffic. Update
any ChatGPT connection that separately stores the ordinary bearer; the official
client's configured local header alone supplies it in this integration.

## Disable and rollback

```bash
./scripts/deploy.sh --no-chatgpt
```

This stops/removes only the optional tunnel service and clears its selection while
continuing the ordinary application deployment. It preserves the saved project
and persistent state. It does not require Compose down or volume deletion. Do not
combine disablement with `--prepare`, which promises not to start/stop services.

The deploy script restores the previous image selection, checkout and deployment
metadata if a deployment fails. Failed first enablement removes the new optional
selection and tunnel. Failure after disablement restores the previous selection
and restarts the former stack. This rollback does not restore deliberately edited
credentials in `.env`. A target predating tunnel support requires disablement first:
disable on the current supported commit, then deploy the older application commit.

## Provenance and live acceptance

The image uses official
[v0.0.14](https://github.com/openai/tunnel-client/releases/tag/v0.0.14), tag commit
`0f870e50a973fa820d4c409000059e181e8d242b`. The release manifest pins the Linux amd64
archive SHA-256 `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`;
image construction verifies it before execution and pins the base image by digest.
There is no runtime client download or upgrade. Supported environment credential
references come from the
[pinned official configuration documentation](https://github.com/openai/tunnel-client/tree/0f870e50a973fa820d4c409000059e181e8d242b/docs).
See [OpenAI's private MCP tunnel guide](https://platform.openai.com/docs/guides/developer-mode/private-mcp-tunnels)
for organization/workspace eligibility and product setup. Runtime Read + Use and
admin Manage permissions have separate purposes.

Synthetic CI success does not prove real OpenAI eligibility, live VPS deployment,
ChatGPT web invocation or native iPad support. Each remains pending until separately
verified. Site-specific migration instructions are delivered outside this
repository. OpenSpec planning stays active until final PR approval in chat.
