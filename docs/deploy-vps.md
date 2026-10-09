# Deploy moomoo-api-mcp to an Ubuntu server

[Host setup](#one-time-provision-the-host) ·
[First deployment](#one-time-deploy-the-application) ·
[Upgrade and rollback](#everyday-deploy-a-new-image) ·
[Restarts](#everyday-restart-the-container) ·
[State](#state-and-restart-reference) ·
[ChatGPT](#optional-chatgpt-access) ·
[Paper recovery](#paper-execution-and-recovery)

The GitHub Actions workflow builds and pushes the `moomoo-api-mcp` image to
ECR on every `main` push. This runbook covers everything from pulling that image
to having a running, logged-in stack on a fresh Ubuntu server (VPS or
otherwise). Tested on Ubuntu 24.04 LTS; other Linux distributions with rootless
Docker should work but are untested.

Use `scripts/deploy.sh` for every manual deployment, including upgrades and
rollback. `compose-prod.sh` is the shared internal wrapper for diagnostics,
interactive login and service management; it is not an alternative image-selection
workflow.

The brokerage image runs OpenD and MCP under one supervisor. OpenD listens only
on container loopback; MCP is published at host `127.0.0.1:8000`. First login is
interactive; later starts reuse remembered state in the `opend-data` volume.
The base deployment is READ_ONLY. See [state and restarts](#state-and-restart-reference)
for storage and process behavior, and [private ChatGPT access](#optional-chatgpt-access)
for the optional tunnel enabled through this same deployment entrypoint.

The production wrapper selects `docker-compose.paper.yml` in SIMULATE and for
selected ChatGPT tunnels. Configure the paper-account allowlist and journal
before starting SIMULATE; the overlay alone does not initialize a journal.
See [the paper support boundary](#deployment-support-boundary).

---

## One-time: provision the host

### 1. Rootless Docker prerequisites

Use the deploy user's rootless Docker installation on Linux. If it is already
working, skip installation. Otherwise follow [Docker's rootless setup guide](https://docs.docker.com/engine/security/rootless/),
which creates the `rootless` context and a user `docker.service`.
Install Git, the AWS CLI, the ECR credential helper, current Docker Compose v2,
and Python 3.10 or newer as `python3` (Ubuntu 24.04 ships 3.12) if missing:
`scripts/deploy_verify.py` runs on the host, not in the image, and uses only
the standard library. On Ubuntu 24.04, install `git`, `python3`, and
`amazon-ecr-credential-helper` with apt, and AWS CLI v2 with
`sudo snap install aws-cli --classic` (or AWS's official installer).

Verify as the deploy user, without `sudo`:

```sh
docker --context rootless info --format '{{json .SecurityOptions}}'
# Must include name=rootless.
docker --context rootless compose version
aws --version
command -v docker-credential-ecr-login
systemctl --user status docker.service
```

The scripts explicitly select the `rootless` context, including under systemd.
Use a Compose v2 release supporting `!reset` and multiple `--env-file` arguments.
Keep the checkout and credentials in this user's home directory. If moving an
existing rootful deployment, migrate its OpenD volume separately before starting;
the rootless daemon has its own storage.

### 2. ECR authentication

The credential helper obtains and caches ECR authorization tokens using the IAM
user's credentials. No manual `docker login` is needed. The deploy wrapper uses
`BatchGetImage` to check the `moomoo-api-mcp` image, which the pull-only IAM policy already allows.

### 3. Write AWS credentials for the helper

Apply the `ecr-pull-iam-user` Terraform module first (`cd ~/Develop/infra/aws/ecr-pull-iam-user && terraform apply …`). Read the values on the workstation with `terraform output -raw aws_access_key_id` / `aws_secret_access_key`; Terraform retains the secret in its state — do not paste it into chat.

Then on the VPS, write the credentials file with the helper, which prompts for
both values (the secret is not echoed), writes them unquoted at mode 0600, and
verifies with `aws sts get-caller-identity`. It ships with the repo, so run it
after the clone in step 5 — or use the manual form below to stay in order:

```sh
cd "$HOME/moomoo"
./scripts/write-aws-credentials.sh
```

To write the file by hand instead, paste the values **bare — no surrounding
quotes**. The AWS INI parser treats quotes as part of the value and fails every
later `aws` call with `Unable to parse config file`:

```sh
install -d -m 0700 "$HOME/.aws"
umask 077

cat > "$HOME/.aws/credentials" <<'EOF'
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
EOF
chmod 0600 "$HOME/.aws/credentials"
```

### 4. Point Docker at the helper

Merge this setting into `$HOME/.docker/config.json`, preserving existing context
and plugin settings (create the file if absent):

```json
{
  "credsStore": "ecr-login"
}
```

Set the file mode to 0600. If other registries already use a credential store,
add a `credHelpers` entry for the Terraform `ecr_registry` hostname with value
`ecr-login` instead of replacing their store.

---

## One-time: deploy the application

### 5. Clone the repo

```sh
git clone https://github.com/evil-sparkle/moomoo-api-mcp.git "$HOME/moomoo"
cd "$HOME/moomoo"
```

The production overlay and both scripts are versioned with the application. Use a
commit containing these files whose CI image build has published successfully.
The deployment script checks out the resolved commit before pulling the image.

### 6. Write `.env`

Create `$HOME/moomoo/.env` (mode 0600, not committed). See `.env.example` for the full list. The non-obvious values:

```ini
# Login identity
MOOMOO_LOGIN_ACCOUNT=                          # your Moomoo login identity
MOOMOO_LOGIN_BY_REMEMBER=1                     # reuse state from the first interactive login
MOOMOO_LOGIN_REGION=sg                         # match your account region
OPEND_INTERACTIVE=0                           # interactive mode is only for one-time setup

# Trading safety
MOOMOO_TRADING_MODE=READ_ONLY
MOOMOO_TRADING_MARKET=NONE                     # account discovery: NONE, HK, US, CN, HKCC, SG, AU, JP, MY, CA
MOOMOO_TRADE_PASSWORD_MD5=                     # blank until you intend to place orders
MOOMOO_REAL_ACC_IDS=                           # required once MOOMOO_TRADING_MODE=REAL
MOOMOO_MAX_ORDER_QTY=1000
MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:10000

# MCP transport
MCP_TRANSPORT=streamable-http
MCP_AUTH_TOKEN=                                # generate: openssl rand -hex 32
```

`MCP_AUTH_TOKEN` is not optional on this transport. The server refuses to start
without it, because an unauthenticated HTTP endpoint exposes every tool,
including the order-mutating ones, to anything that can reach the port.

`MOOMOO_LOGIN_ACCOUNT` is required for unattended remembered login. Without an
account or usable remembered state, the supervisor logs the setup problem and
keeps MCP diagnostics running without OpenD. This differs from invalid MCP or
trading settings, which refuse server startup.

For account-bound reads, discover accounts with `get_accounts` and copy the
returned string ID exactly. `acc_id="0"` works only when one account matches the
requested environment. Login region, securities firm, trading market and trading
environment are separate settings.

If rolling back across a configuration or journal schema change,
check that target version's requirements before deployment. Do not assume an
older image preserves current trading safeguards.

### 7. Prepare image, then perform interactive OpenD login

On the Terraform workstation, obtain the nonsecret registry hostname with
`terraform output -raw ecr_registry` from `aws/ecr-pull-iam-user`.
On the VPS, substitute that hostname below (only required on the first deploy):

```sh
cd "$HOME/moomoo"
export ECR_REGISTRY='<Terraform ecr_registry output>'
./scripts/deploy.sh --prepare
```

This checks the image, checks out the full commit, writes `.deploy.env`, and
pulls without starting services. It derives the seven-character tag from the
full commit; you do not enter image tags. An optional commit argument selects
an older published commit: `./scripts/deploy.sh --prepare <commit>`.

Every Compose command below uses `compose-prod.sh`, which loads `.env` and
`.deploy.env` explicitly. The same wrapper is used by systemd.

This is the one non-mechanical step. Follow the documented
[OpenD 10.10 startup flow](https://openapi.moomoo.com/moomoo-api-doc/en/opend/opend-cmd.html):
log in interactively once, complete device verification and remember the password.
Unattended starts then use `login_by_remember` with the persistent OpenD volume.
The unverified `MOOMOO_LOGIN_PWD_MD5` startup option has been retired from this
deployment; an old environment value is ignored. If remembered state is missing,
the supervisor reports the required setup and keeps MCP diagnostics available
without starting OpenD.

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh run --rm --no-deps -it \
  -e OPEND_INTERACTIVE=1 -e OPEND_MAX_RESTARTS=0 moomoo-mcp
```

This runs OpenD interactively with supervisor retries disabled. Follow its account,
password and device-verification prompts directly in your terminal. If login fails
or OpenD reports a cooldown, stop with Ctrl-C rather than retrying. When asked:

> Remember the password? (Y/n)

Type `Y`. OpenD logs in, the token file appears under `/home/opend/.com.moomoo.OpenD/F3CNN/`, and the container exits when you Ctrl-C.

Check that remembered state exists without printing credential files or account
identifiers:

```sh
./scripts/compose-prod.sh run --rm --no-deps --entrypoint python moomoo-mcp -c \
  'from moomoo_mcp.supervisor import has_remembered_token; print(has_remembered_token("/home/opend"))'
```

`True` confirms state is present; also confirm OpenD reported successful login.

You only do this once per account-region. Subsequent restarts use `login_by_remember=1` and complete unattended. If you ever wipe `opend-data` or change region, the same interactive flow must happen again.

### 8. Bring up the stack

```sh
cd "$HOME/moomoo"
./scripts/deploy.sh HEAD
./scripts/compose-prod.sh ps
./scripts/compose-prod.sh logs -f --tail=200
```

One log stream carries both processes. OpenD should reach "TRC login OK" within ~30s, and the server reports `MCP server listening on 0.0.0.0:8000`; lines prefixed `[supervisor]` are the process policy itself, including any gateway restart. Hit `http://localhost:8000/mcp` from the host (the port is bound to `127.0.0.1` only) with the `Authorization: Bearer $MCP_AUTH_TOKEN` header.

`deploy.sh HEAD` starts the prepared commit and runs authenticated MCP
verification followed by the separate gateway readiness check. See
[deployment verification and rollback](#everyday-deploy-a-new-image) below for
success criteria. A listening endpoint alone does not prove broker login.

### 9. systemd unit, so the stack survives reboots

Use a user unit alongside the rootless Docker user service. Both units belong
to the same systemd manager, so `Requires=docker.service` resolves correctly.
The Compose wrapper reads the current `.env` and `.deploy.env` on every start.

**Every `systemctl` command for this stack takes `--user` and no `sudo`.** The
unit lives in the deploy user's manager, so `sudo systemctl restart moomoo`
searches the system scope and reports the misleading `Unit moomoo.service not
found`. Use `systemctl --user restart moomoo.service`.

```sh
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/moomoo.service" <<'EOF'
[Unit]
Description=moomoo-api-mcp (OpenD + MCP)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
Environment="PATH=%h/bin:/usr/local/bin:/usr/bin:/bin"
WorkingDirectory=%h/moomoo
ExecStart=%h/moomoo/scripts/compose-prod.sh up -d
ExecStop=%h/moomoo/scripts/compose-prod.sh stop
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now docker.service
systemctl --user enable --now moomoo.service
systemctl --user status moomoo.service
```

For startup without an interactive login, enable lingering once if it is not
already enabled: `sudo loginctl enable-linger "$USER"`. No Docker group membership
or system-level application service is needed.

---

## Everyday: deploy a new image

```sh
cd "$HOME/moomoo"
./scripts/deploy.sh                 # current origin/main
./scripts/deploy.sh <commit>        # explicit full or abbreviated commit
```

The start uses `--remove-orphans`, so containers stranded by earlier manual
troubleshooting no longer block it with `container name is already in use`.
When running Compose by hand rather than through the script, pass the same flag.

The script fetches `main`, resolves the full commit, derives the seven-character
tag CI writes on every main build, and confirms it on the ECR repository with
`aws ecr batch-get-image`. AWS errors remain visible; a missing image stops the
deployment before checkout. It never uses `:latest` and ignores git `v*` tags,
which are release bookmarks only. If `main` has not finished publishing the
image, wait for CI or select an already-published commit. It does not search
for the newest green build. If `scripts/deploy.sh` in the target commit differs
from the local checkout, the script automatically re-executes using the target
commit's deploy script so updated deployment, verification, and rollback logic
takes effect immediately.

How far back you can roll back is bounded by the ECR lifecycle policy, which
keeps every `v*`-tagged image and the 30 most recent commit builds per
repository. Optional tunnel images share this repository under `tunnel-*` tags,
so they share its retention policy; do not assume a separate 30-image window or
that `tunnel-v*` tags receive the application's `v*` exemption.

When the private ChatGPT tunnel is explicitly selected, CI also publishes its
image as `moomoo-api-mcp:tunnel-<commit>`. The deploy script confirms that tag
before checkout, saves its immutable ECR digest in `.chatgpt-deploy.json`, and
pulls both selected images. The VPS needs no separate image build. Default
deployment still checks and pulls only the application image. Follow the
[private ChatGPT runbook](#optional-chatgpt-access): add the two OpenAI settings
to `.env` and enable with `scripts/deploy.sh --chatgpt`; subsequent deployments
retain that selection.

After start, it verifies the endpoint as a client would: an authenticated MCP
`initialize` using the `MCP_AUTH_TOKEN` Compose resolves for the service,
retried while the endpoint is not answering yet (no response, or 5xx), within
`DEPLOY_VERIFY_TIMEOUT` seconds (default 90; must be a whole number of
seconds; `0` means one attempt). Verified means a completed HTTP transfer
(curl exit 0) **and** HTTP 200 **and** a JSON-RPC `initialize` result. A
refused probe (401/403), a 200 that is not an initialize result, or an
incomplete transfer — valid-looking content included — fails the deploy
immediately. The token reaches curl on
stdin, never a command line or file; the resolved configuration is never
printed or written anywhere. On failure, or if the configuration cannot be
resolved, or `pull` or `up` fails, it restores the previous `.deploy.env` and
commit and any previous tunnel image selection (restarting the previous deployment if something had been started) and
exits non-zero. If the rollback's own restart fails, the script says so and the
stack needs attention: retry `scripts/deploy.sh <previous-commit>`. Verification does not
prove OpenD login.
After verification succeeds, local image cleanup keeps the current container's
image and the image used by the container before deployment, including all
repository tags pointing to either image. It removes older tags only from this
registry's `moomoo-api-mcp` repository on the rootless Docker context, without
forced deletion or a global prune. Cleanup is skipped for `--prepare`, failed
deployments, an unidentified previous container (including the first deploy),
and redeploys using the same image, which preserve the earlier rollback image.
Cleanup errors warn without failing a verified deployment. Other repositories,
untagged images, tunnel-prefixed tags, and volumes are untouched.

After MCP verification, deployment separately calls the read-only `check_health`
tool, waiting up to `DEPLOY_GATEWAY_TIMEOUT` seconds (default 60; a whole number
of seconds; `0` means one attempt) for remembered login to finish. `OpenD ready`
requires successful quote and trade probes and an explicitly true quote-login
flag. It confirms gateway connectivity and quote login, not trading permission,
unlocked trading, or market authorization.

If OpenD remains unavailable, reports incomplete login, or provides no usable
health result, the script prints a gateway readiness warning and recovery
instructions. A verified MCP deployment still exits successfully and remains
running for diagnostics; this warning does not trigger rollback. `--prepare`
does not send health requests. You can check readiness again without redeploying:

```sh
python3 scripts/deploy_verify.py gateway-readiness
```

For a required initial login, or when remembered login is no longer usable,
first stop the background service so two gateways do not share the same state:

```sh
./scripts/compose-prod.sh stop moomoo-mcp
./scripts/compose-prod.sh run --rm --no-deps -it \
  -e OPEND_INTERACTIVE=1 -e OPEND_MAX_RESTARTS=0 moomoo-mcp
```

Complete the terminal prompts, choose `Y` to remember the password, confirm
successful login, then stop the temporary container with Ctrl-C. Keep
`OPEND_INTERACTIVE=0` and `MOOMOO_LOGIN_BY_REMEMBER=1` for background operation,
and ensure the account and region match that login. Then start the service and
check readiness again:

```sh
./scripts/compose-prod.sh up -d moomoo-mcp
python3 scripts/deploy_verify.py gateway-readiness
```

An unavailable gateway can also reflect connectivity or permission problems;
inspect diagnostics before repeating login. Container startup alone does not
confirm a usable broker session.

Keep the tracked working tree clean. Runtime secrets belong in `.env`; the non-secret
registry, image tag and retained Compose project belong in `.deploy.env`. Both are ignored by Git.
Tags are mutable in ECR, so a commit tag records source identity but is not a
cryptographic guarantee of immutable image content.

`opend-data` is a named volume by default, mounted at
`/home/opend/.com.moomoo.OpenD`. It is not `$HOME/moomoo/opend-data`.
The inspection command above also works if `OPEND_DATA_DIR` selects a bind mount.
Do not remove this volume during ordinary deployments: it holds device tokens.

## Everyday: restart the container

For a restart with unchanged configuration:

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh restart moomoo-mcp
```

A restart reuses the container's existing environment. After editing settings,
run `./scripts/deploy.sh HEAD` to apply them to the currently checked-out commit;
Compose recreates affected containers. `./scripts/deploy.sh` without a commit
also selects the latest `origin/main`.

OpenD reuses its remembered authorization but must log in again. Requests can
fail while processes restart or reconnect; stateless HTTP does not require
recreating a session. An uncertain order outcome must be reconciled, not retried
as a new order. See [state and restarts](#state-and-restart-reference) for supervision,
recovery and persistence details.

## Everyday: rotate `MCP_AUTH_TOKEN`

Generate a replacement with `openssl rand -hex 32`, update `MCP_AUTH_TOKEN` in
the deployment environment through your normal secret-editing process, and update
authorized clients. Apply it with `./scripts/deploy.sh HEAD`; restarting an
existing container does not load the edited environment. Verify an authenticated
MCP initialize succeeds with the new token and the old token returns 401.
Do not put the token in shell command arguments or commit it.

If the tunnel is enabled, use the coordinated disable/update/enable sequence in
[the tunnel credential procedure](#credentials-and-restarts).
OpenD authorization remains on its volume, so this does not require another
interactive device login.

## Stop the deployment

```sh
systemctl --user stop moomoo.service
cd "$HOME/moomoo"
./scripts/compose-prod.sh down       # do not add -v; preserve device tokens
```

This retains the named volume. Account changes and deliberate token removal
require a separate, explicit storage-cleanup procedure; deleting a similarly
named directory in the checkout does not remove the Docker volume.

## State and restart reference

| State | Persistence and operator consequence |
| --- | --- |
| OpenD device authorization and remembered login | Stored in `opend-data`; preserve across recreation and rollback. Deleting it requires interactive login again. |
| OpenD live login | Process memory; gateway or container restart requires broker login again, commonly around 30 seconds but not a downtime guarantee. |
| MCP HTTP session | Only stateless Streamable HTTP with JSON responses is supported; interrupted requests can fail, but there is no session to recreate. |
| Gateway connections and quote subscriptions | Process-owned; SDK reconnects after gateway failure. Container replacement loses subscriptions. |
| Trade halt (`ARMED`/`HALTED`) | MCP process memory; container replacement starts a new process. Restarting is not evidence an uncertain order was reconciled. |
| Paper execution journal and recovery audit | Separate `execution-data` volume when configured; preserve it and its identity through mode changes, upgrades and restores. |
| Container environment | Restart reuses existing values; use deployment to recreate containers after settings change. |

When OpenD exits, the supervisor restarts it with bounded retries while MCP
keeps diagnostics available. Repeated gateway exits or an MCP exit stop the
container for Docker to restart. A running gateway with a broker connectivity
problem does not trigger a restart. Missing login state leaves MCP available
without a gateway; malformed supervision settings fail startup.

A REAL deployment with a stored trade credential locks at rest, unlocks for one
write and re-locks afterward. A failed re-lock sets `HALTED`. Inspect
`check_health` and reconcile the order before recovery: a receipt can coexist
with `gateway_relock_error` and `execution_halted: true`. Successful explicit
`lock_trade` clears the halt; reconnect locking does not. Risk-increasing REAL
writes are blocked while halted, while permitted cancellation and reduction
operations remain available. Paper recovery gates are separate and durable.

The OpenD volume name depends on the saved Compose project. Inspect only mount
metadata to locate it without exposing container credentials:

```sh
docker --context rootless container inspect moomoo-api-mcp \
  --format '{{range .Mounts}}{{if eq .Destination "/home/opend/.com.moomoo.OpenD"}}{{.Name}} {{.Source}}{{end}}{{end}}'
```

OpenD state is mounted at `/home/opend/.com.moomoo.OpenD`, owned by UID 10001.
An `OPEND_DATA_DIR` path selects a bind mount instead. Preserve the same storage
and project; never use `down -v` for normal operations.

The repository does not encrypt volumes. Protect the deploy user's account,
backups and host filesystem. Runtime credentials are visible to trusted Docker
administrators; do not display complete container environments or resolved
Compose configuration. Rootless Docker limits daemon privileges, while the
application's UID 10001 controls privileges inside its container.

See the [container deployment](../openspec/specs/container-deployment/spec.md),
[transport sessions](../openspec/specs/transport-sessions/spec.md) and
[trade unlock](../openspec/specs/trade-unlock/spec.md) specs for detailed contracts.

## Optional ChatGPT access

The optional Compose service runs the unmodified OpenAI tunnel client. ChatGPT
access permits server-enforced `READ_ONLY` or `SIMULATE`. In `READ_ONLY`, every
order mutation is refused. In `SIMULATE`, supported paper orders are permitted
for explicitly allowlisted simulated accounts through the persistent execution
journal. Real-account, position and market-data reads remain available; real
orders and trade unlocking are refused before any gateway write. Operator
recovery requires its separate credential, which is never given to the tunnel.
The ordinary MCP bearer follows the server's trading mode: disable the tunnel
before changing the deployment to `REAL`.

### Configure and deploy

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

For paper trading, select `MOOMOO_TRADING_MODE=SIMULATE` and configure the
simulated-account allowlist and journal prerequisites in
[paper execution](#standalone-paper-topology). Selected tunnel deployments also
include `docker-compose.paper.yml`, with the same project-scoped `execution-data`
volume across `READ_ONLY` and `SIMULATE`. `READ_ONLY` never opens the journal.
Set `MOOMOO_CREATE_JOURNAL=1` only for deliberate first initialization, then
return it to `0`. Preserve the existing journal when switching modes.
Paper tool calls must explicitly use `trd_env="SIMULATE"`, the selected paper
account, and the operation ID and admission epoch required by paper execution.
Real-account reads may explicitly use `trd_env="REAL"`; the server never silently
converts a real-order request into a paper order.

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
pulls both images, validates authentication and READ_ONLY or SIMULATE settings,
then starts the stack. First enablement detects the running container's Compose
project and saves it for subsequent enabled and disabled deployments, preserving
its named volumes. A new host uses the checkout directory's default project.
Missing credentials fail before startup. A missing tunnel image fails before
checkout or service changes. `--prepare --chatgpt` validates and pulls without
starting services; ordinary deployments keep the tunnel off unless selected.

Brokerage reads also require OpenD login. Complete the
[one-time interactive login](#7-prepare-image-then-perform-interactive-opend-login),
remember the password and preserve the OpenD volume. Later starts use remembered
login; no brokerage login password or hash is configured through Compose.

The tunnel reaches authenticated MCP over Docker DNS and cannot reach OpenD's
container-loopback listener. It publishes no ports. The deployment trusts OpenAI
and the Docker host, including the official client's upstream redirect behavior.
Architecture and trust requirements live in the
[private ChatGPT access spec](../openspec/specs/private-chatgpt-access/spec.md);
exact client pins live in [the release manifest](../deploy/tunnel-client/release.json).
See [OpenAI's private MCP tunnel guide](https://platform.openai.com/docs/guides/developer-mode/private-mcp-tunnels)
for organization/workspace eligibility and setup.

### Startup, checks and recovery

Before launching the client, the manager performs authenticated MCP initialize,
tool discovery and `check_health`, requiring READ_ONLY or SIMULATE. Transient MCP
failures retry within a 90-second deadline; invalid credentials, mode or results
fail closed. Every client launch repeats the gate. Degraded broker connectivity
can still pass when MCP itself is available in an allowed mode; that does not
prove broker login or data access.

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

### Credentials and restarts

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

### Disable and rollback

```bash
./scripts/deploy.sh --no-chatgpt
```

This stops/removes only the optional tunnel service and clears its selection while
continuing the ordinary application deployment. It preserves the saved project
and persistent state. In `SIMULATE`, the production wrapper keeps the paper overlay
selected so local paper execution retains its journal after tunnel disablement.
It does not require Compose down or volume deletion. Do not combine disablement
with `--prepare`, which promises not to start/stop services.

The deploy script restores the previous image selection, checkout and deployment
metadata if a deployment fails. Failed first enablement removes the new optional
selection and tunnel. Failure after disablement restores the previous selection
and restarts the former stack. This rollback does not restore deliberately edited
credentials in `.env`. A target predating tunnel support requires disablement first:
disable on the current supported commit, then deploy the older application commit.

### Tunnel acceptance

Local diagnostics and synthetic CI checks do not prove OpenAI eligibility or
forwarded calls. Verify actual read-only tool access from ChatGPT web and any
native clients you use, and confirm broker login separately. Keep dated evidence
in PRs or CI rather than treating it as permanent deployment status.

## Paper execution and recovery

Paper execution persists SIMULATE operations in a dedicated SQLite journal. When
configured in SIMULATE or REAL mode, it uses the **same paper journal**. REAL mode
permits real trading without disabling paper trading. Real orders are not
journaled; a separate REAL journal is deferred. Do not share a database between
paper and real execution.

### Deployment support boundary

The production `scripts/deploy.sh` path supports paper execution in `SIMULATE`.
Its shared `compose-prod.sh` wrapper selects `docker-compose.paper.yml` for that
mode, including when the optional ChatGPT tunnel is disabled. Selected tunnels
also include the paper overlay in `READ_ONLY`, preserving the project-scoped
journal volume across mode changes without opening it in `READ_ONLY`.

Configure the explicit simulated-account allowlist and journal settings below.
Journal initialization and recovery remain deliberate operator actions; deployment
does not reset an existing journal or bypass its review gates. Preserve the saved
Compose project and both persistent volumes through upgrades and rollback.
Systemd and subsequent deployments use the same wrapper and mode selection.

Use `deploy.sh` for production. The direct Compose examples below describe
standalone development and test setups. Automatic production paper-overlay
selection covers `SIMULATE`; `REAL` without a tunnel retains the base deployment
files. Disable ChatGPT before switching to `REAL`, which the tunnel refuses.

### Standalone paper topology

Add `docker-compose.paper.yml` to the existing Compose file list when enabling
paper execution. Preserve the Compose project name across restarts, upgrades and
mode changes: it determines the named volume's identity. The overlay adds
`execution-data` at `/var/lib/moomoo-mcp/data`; it retains the existing OpenD
`opend-data` mount and uid 10001 ownership at `/home/opend/.com.moomoo.OpenD`.
The image prepares the journal directory for uid 10001, and a new named volume
inherits that ownership. Use a local POSIX filesystem honoring fsync and advisory
locks. Network filesystems without these guarantees are unsupported.

Supply the following values through your deployment's existing settings mechanism:

| Setting | Meaning |
| --- | --- |
| `MOOMOO_TRADING_MODE` | `SIMULATE` or `REAL`; neither changes paper storage identity |
| `MOOMOO_SIMULATED_ACC_IDS` | Explicit positive paper account IDs; the allowlist cannot contain 0 |
| `MOOMOO_JOURNAL_PATH` | Overlay fixes this to `/var/lib/moomoo-mcp/data/execution.sqlite3` |
| `MOOMOO_CREATE_JOURNAL` | `1` only for explicitly authorized first initialization; normally `0` |
| `MOOMOO_JOURNAL_LOCK_WAIT_MS` | Bounded SQLite wait, 1–60000 milliseconds; default 5000 |
| `MCP_AUTH_TOKEN` | Normal authenticated tool access |

Existing REAL trading credentials and account allowlists still apply to REAL
orders. Paper journaling adds no authority to trade REAL accounts. Use the US or
NONE market filter for the supported paper execution scope.

For example, pass the same project, overlays and explicit operator settings file
to every deployment command (substitute your existing settings file path):

```sh
docker compose --project-name moomoo --env-file /path/to/operator-settings \
  -f docker-compose.yml -f docker-compose.paper.yml up -d
```

If using the production image overlay, include `docker-compose.prod.yml` before
the paper overlay and supply its required image settings as usual. Set
`MOOMOO_CREATE_JOURNAL=1` only when intentionally provisioning the first empty
journal. Once created, return it to `0` before normal operation so a missing mount
fails closed. Never use initialization to erase or bypass an unresolved outcome.
Do not run `down --volumes` against this deployment.

READ_ONLY deployments without a selected tunnel omit the paper overlay and need
no journal volume. Selected tunnel deployments retain its mount. Switching an
existing executor temporarily to READ_ONLY must retain its original volume for
later use; READ_ONLY does not open the database even if journal settings exist.

Exactly one executor may hold a journal at a time. A second process or container
fails closed on `execution.lock`. Scaling the executor horizontally against a
shared journal is unsupported. Each process start creates a new admission epoch;
startup review must complete before new mutations can proceed. Retain each
operation ID **and its admission epoch** across client/network retries. Never
refresh either merely because a response was lost.

### Backup and restore

Prefer SQLite's online backup API. It produces a consistent, self-contained
snapshot while the executor remains active; copying the live main database file
alone does not. A simple backup executed as the image's normal uid 10001 is:

```sh
docker compose --project-name moomoo --env-file /path/to/operator-settings \
  -f docker-compose.yml -f docker-compose.paper.yml exec -T moomoo-mcp python - <<'PY'
import os
import sqlite3
from pathlib import Path

source = Path('/var/lib/moomoo-mcp/data/execution.sqlite3')
backup = source.with_name('execution-backup.sqlite3')
# Exclusive creation refuses an existing backup and restricts it to this uid.
fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live:
    with sqlite3.connect(backup) as snapshot:
        live.backup(snapshot)
        assert snapshot.execute('PRAGMA integrity_check').fetchone() == ('ok',)
PY
```

Retain backups outside the running volume according to your own retention policy.
Treat execution records as sensitive operational data. Confirm the copied backup
passes `PRAGMA integrity_check` before relying on it.

For an offline main-file copy, prevent **all** executor starts for the entire copy
and restore interval, acquire the process lock, and require a cleanly closed or
recovered database. Conservatively refuse a database-only copy whenever a sibling
`execution.sqlite3-journal` exists. A crash releases the process lock but can leave
a hot rollback journal; an unheld lock alone proves nothing about copy safety.
Recover the original database with SQLite while no executor can start, or preserve
and restore the database **and its matching rollback journal together** as a set.
Never discard a rollback journal to make a copy appear clean.

Before restoring, stop the executor, disable automatic restarts, prevent concurrent
starts, and preserve the current database and any sidecars for investigation.
Restore into the original journal volume with uid 10001 ownership and retain the
OpenD authorization volume unchanged. Restart with creation disabled. Restore
creates a new process epoch and requires recovery review; it cannot reconstruct
rows missing from the snapshot. Unknown tokens carrying retired epochs are refused,
and existing uncertain/nonterminal rows gate new admissions until accounted for.
A missing restored token is not evidence that its broker request never happened.

### Automatic recovery

Recovery runs inside the MCP process after startup and uncertain responses. It does
not depend on client instructions or a separate operator credential. See
[Automatic paper execution recovery](paper-recovery.md) for matching, retry timing,
assumed absence, late discovery and schema migration details.

Use `check_health` for journal readiness, pending operation IDs and recent recovery
updates. Use `get_execution(operation_id)` for the stored request outcome, order
identity, actual check times and recovery disposition. `reconcile_execution` uses
that same recovery policy for a due check; repeated calls cannot bypass the schedule.

New paper placements require their caller-generated operation ID, current admission
epoch, explicit SIMULATE environment and decimal-string limit price. Preserve the
original request and token on every retry. The server generates a separate order tag
and sends it as the broker remark. ACKNOWLEDGED means gateway acceptance, not a fill.
UNKNOWN_OUTCOME remains uncertain even if the paper absence policy releases its
recovery block. A separate new trading decision uses a new ID; the original never
replays. Conflicting evidence, active uncertain mutations, identityless legacy rows
and storage failures can still keep paper trading blocked.

Keep the original journal and unresolved records. Reinitializing, replacing or
repointing storage does not recover broker actions missing from that journal.

### Paper verification boundary

Container checks use an isolated mocked SDK and do not prove live provider
response-loss recovery, retention or external retry propagation. Those remain in
[the provider acceptance change](../openspec/changes/validate-paper-execution-provider/proposal.md).

### Journal upgrades and dependent modifications

Take a consistent backup before upgrading. Schema 1 and 2 journals upgrade atomically
to schema 3; older executors refuse the upgraded journal. Unobserved
acknowledged modifications can block a dependent modification until fresh broker
observations match the earlier request. A durably refused operation stays refused
on retry; never change an uncertain operation's token to bypass recovery.
See the [execution journal spec](../openspec/specs/execution-journal/spec.md) for
identity, admission, observation and recovery requirements.
