# Deploy moomoo-api-mcp to an Ubuntu server

The GitHub Actions workflow builds and pushes the `moomoo-api-mcp` image to
ECR on every `main` push. This runbook covers everything from pulling that image
to having a running, logged-in stack on a fresh Ubuntu server (VPS or
otherwise). Tested on Ubuntu 24.04 LTS; other Linux distributions with rootless
Docker should work but are untested.

For what state the stack holds and what each restart costs, see
[`state-and-restarts.md`](state-and-restarts.md).

**One image, two runtime constraints.** The image carries both the OpenD gateway and the MCP server, started by a supervisor that owns them (`src/moomoo_mcp/supervisor.py`). The server half is ordinary — pull and run. OpenD is not: the first start must happen interactively so you can answer the device-verification prompt and "remember the password". Until that token lands in `opend-data`, no unattended start can complete login.

**Upgrading from the two-container deployment.** The `opend-data` volume carries over untouched — same mount path, same owning uid — so no interactive re-login is needed. `docker compose up -d` replaces both old containers with the one new one; `--remove-orphans` (which `deploy.sh` already passes) clears the leftover `opend` container. The `moomoo-opend` ECR repository stops being written to: leave it until the last two-container image is past being a rollback target, then delete the repository.

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
MOOMOO_LOGIN_PWD_MD5=                          # leave blank (first run uses console)
MOOMOO_LOGIN_BY_REMEMBER=1                     # set after the first interactive login lands a token
MOOMOO_LOGIN_REGION=sg                         # match your account region

# Trading safety
MOOMOO_TRADING_MODE=READ_ONLY
MOOMOO_TRADING_MARKET=NONE                     # account discovery: NONE, HK, US, CN, HKCC, SG, AU, JP, MY, CA
MOOMOO_TRADE_PASSWORD_MD5=                     # blank until you intend to place orders
MOOMOO_REAL_ACC_IDS=                           # required once MOOMOO_TRADING_MODE=REAL
MOOMOO_MAX_ORDER_QTY=1000
MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:10000
MOOMOO_MAX_ORDER_NOTIONAL=10000                # legacy, rollback-only; see below

# MCP transport
MCP_TRANSPORT=streamable-http
MCP_AUTH_TOKEN=                                # generate: openssl rand -hex 32
```

`MCP_AUTH_TOKEN` is not optional on this transport. The server refuses to start
without it, because an unauthenticated HTTP endpoint exposes every tool,
including the order-mutating ones, to anything that can reach the port.

`MOOMOO_MAX_ORDER_NOTIONAL` is **legacy and rollback-only**. This image never
applies it as a limit; the previous image does. Keeping it here beside
`MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is what lets one `.env` serve both, so
a rollback needs no edit under pressure. Set on its own it is a startup error.

`MOOMOO_LOGIN_ACCOUNT` is required even with `MOOMOO_LOGIN_BY_REMEMBER=1`: the
remembered-token path passes `-login_account` alongside `-login_by_remember=1`.
Leaving it blank now exits the container with an explicit error rather than
leaving OpenD waiting on a console prompt that never arrives under `up -d`.

### Migrating an existing deployment

Configuration is validated at startup, before anything listens, so a variable
this version rejects stops the process rather than failing requests one at a
time. Edit `.env` **before** deploying the new image:

1. Set `MOOMOO_REAL_ACC_IDS` to the REAL account identifiers writes may target.
   Get them from `get_accounts`. Required in `REAL` mode.
2. `MOOMOO_TRADING_MARKET` now defaults to `NONE`, which discovers every
   securities market returned for this login and firm. Set it to `HK` if you
   need the former HK-only discovery scope. The filter does not authorize writes.
3. Store exact account IDs from `get_accounts(market="US", trd_env="SIMULATE")`
   and pass them unchanged to later reads. An `acc_id="0"` read now fails when
   more than one account matches its environment.
4. Add `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY=USD:<amount>`, plus any other
   currency you trade, if you use a notional cap. Leave the legacy
   `MOOMOO_MAX_ORDER_NOTIONAL` in place.
5. Confirm `MCP_AUTH_TOKEN` is set.
6. Deploy through the normal path, then call `check_health` and confirm
   `execution_halted: false` and the expected `trade_market`.
7. Place and cancel a SIMULATE order.

**The crash-loop signal.** If `.env` was not migrated, the MCP process exits,
the supervisor stops OpenD, and `restart: unless-stopped` restarts the
container repeatedly. The log line names the variable:

```
Refusing to start: MOOMOO_REAL_ACC_IDS is required when MOOMOO_TRADING_MODE is REAL...
```

Read that line rather than the restart count; it says exactly what to add.

**Rollback needs no `.env` edit.** Redeploy the previous image tag. It ignores
`MOOMOO_REAL_ACC_IDS` and `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY`, and enforces
its own unit-less `MOOMOO_MAX_ORDER_NOTIONAL` as before. It also restores
startup auto-unlock, which is that version's behaviour.

Two tool-facing changes the agent needs to know about: the write tools now
require `trd_env`, and `modify_order`/`cancel_order` resolve the account before
dispatch, so a call that relied on the gateway's own default for `acc_id="0"`
must name an account when more than one is eligible.

Account-bound reads now resolve `acc_id="0"` only when one account matches the
requested `trd_env`; otherwise they fail before the account query and ask for an
explicit ID. `get_accounts` accepts independent response filters, for example
`get_accounts(market="US", trd_env="SIMULATE")`. Copy the returned string ID
exactly into `get_assets`, `get_positions`, `get_orders`, or another read. The
broker region (`MOOMOO_LOGIN_REGION=sg`), securities firm
(`MOOMOO_SECURITY_FIRM=FUTUSG`), market (`US`), and trading environment
(`SIMULATE` or `REAL`) are separate settings.

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

This is the one non-mechanical step. The Linux OpenD build has no password flag, so unattended `-login_pwd_md5` does not work. The only headless path is `login_by_remember`, which needs a token written by a previous **interactive** session that also cleared the device-verification code. Without `OPEND_INTERACTIVE=1`, a headless start without a remembered token now exits with an error instead of hanging.

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh run --rm -it -e OPEND_INTERACTIVE=1 moomoo-mcp
```

This runs the supervisor with the gateway in interactive mode; OpenD prints its banner, then asks for the device-verification code. Check the Moomoo app on your phone, enter the 6-digit code. It then prompts:

> Remember the password? (Y/n)

Type `Y`. OpenD logs in, the token file appears under `/home/opend/.com.moomoo.OpenD/F3CNN/`, and the container exits when you Ctrl-C.

Verify the token:

```sh
./scripts/compose-prod.sh run --rm --entrypoint /bin/sh moomoo-mcp -c \
  'ls -la "$HOME/.com.moomoo.OpenD/F3CNN/"'
# Inspect UserAccMap/ and ftnet/auth_acc_list inside the mounted volume.
# Presence alone does not prove login; also confirm a successful OpenD login.
```

You only do this once per account-region. Subsequent restarts use `login_by_remember=1` and complete unattended. If you ever wipe `opend-data` or change region, the same interactive flow must happen again.

### 8. Bring up the stack

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh up -d
./scripts/compose-prod.sh ps
./scripts/compose-prod.sh logs -f --tail=200
```

One log stream carries both processes. OpenD should reach "TRC login OK" within ~30s, and the server reports `MCP server listening on 0.0.0.0:8000`; lines prefixed `[supervisor]` are the process policy itself, including any gateway restart. Hit `http://localhost:8000/mcp` from the host (the port is bound to `127.0.0.1` only) with the `Authorization: Bearer $MCP_AUTH_TOKEN` header.

The deploy script verifies the same way a client would: it sends an MCP `initialize` request authenticated with the `MCP_AUTH_TOKEN` Compose resolves for the service, so a healthy deploy logs `POST /mcp 200`. The reply must also be a JSON-RPC `initialize` result, not merely any 200 — a URL that answers 200 without speaking MCP fails the deploy. Docker Compose resolves the deployment configuration — the same env files and compose files `scripts/compose-prod.sh` starts the container with — and `scripts/deploy_verify.py` (host Python 3.10+, standard library only) reads the resolved service environment; it never parses dotenv files itself. The one translation it makes is Compose's own output escaping: `config` prints values as compose input, where a literal `$` appears as `$$`, so the printed pairs are decoded back to what the container received. The resolved configuration stays in the helper's memory — it is never printed or written anywhere, and it can carry credentials besides the token. The token is handed to curl on stdin, never a command line or file. A bare unauthenticated `GET /mcp` still answers 401 by design — that only means the endpoint is up with auth enabled. The probe also requires the HTTP transfer itself to complete: curl's exit status is evaluated independently of the response content, so a partial transfer (curl exit 18) — even one whose captured bytes would parse as a complete, valid initialize result — fails the attempt, and a timeout or interrupted attempt never verifies either. Verification confirms the endpoint accepts the configured authentication and returns a valid initialize result over a completed transfer; it does not confirm broker login or trading readiness. If the probe is refused (401/403), the deploy fails and names `MCP_AUTH_TOKEN` as the thing to check.

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
repository.

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
commit (restarting the previous deployment if something had been started) and
exits non-zero. If the rollback's own restart fails, the script says so and the
stack needs a manual `scripts/compose-prod.sh up -d`. Verification does not
prove OpenD login.
Confirm login and MCP availability after each deployment; container startup
alone is not a successful authenticated session.

Keep the tracked working tree clean. Runtime secrets belong in `.env`; the two
saved deployment settings belong in `.deploy.env`. Both are ignored by Git.
Tags are mutable in ECR, so a commit tag records source identity but is not a
cryptographic guarantee of immutable image content.

`opend-data` is a named volume by default, mounted at
`/home/opend/.com.moomoo.OpenD`. It is not `$HOME/moomoo/opend-data`.
The inspection command above also works if `OPEND_DATA_DIR` selects a bind mount.
Do not remove this volume during ordinary deployments: it holds device tokens.

## Everyday: restart the container

One container holds both processes, and the supervisor inside it — not Compose —
decides what a restart means.

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh restart moomoo-mcp
```

There is deliberately no command to restart the gateway on its own. The
supervisor does that by itself whenever OpenD dies, without disturbing anything
a client can see, and what an operator restarts is the container.

**When the gateway process dies**, the supervisor restarts it in place and MCP
clients are not disturbed: the stateless Streamable HTTP endpoint keeps serving calls across it. The
moomoo SDK reconnects on its own, retrying every six seconds for as long as it
takes, and on reconnect it replays the quote subscriptions it was holding and
re-asserts the gateway lock at rest — in `READ_ONLY`, and in `REAL` with a
stored trade credential. Nothing unlocks on that path: an order's just-in-time
unlock is not replayed, because the order re-locks when it finishes, and the
re-lock clears the SDK's cached unlock. If a write is in flight and holding the
gateway unlocked, the reconnect skips its lock request rather than locking
underneath it. Tool calls made during the gap fail
with a connect timeout and the next call succeeds; `check_health` reports
`disconnected` or `degraded` until it is back. OpenD still needs ~30s to log in,
so expect that long before health goes green. Look for `[supervisor]` lines in
the log to see it happen. If OpenD fails repeatedly — five times in five
minutes, by default — the supervisor stops the server and exits instead, and
Docker replaces the whole container.

**Restarting the container** costs clients one failed call. The endpoint is
served statelessly, so there is no session for the restart to invalidate: a call
in flight fails and the next one succeeds. OpenD does *not* keep its login
across this — the process is replaced — so expect the same ~30s before health
goes green. No interactive step is needed: the device token is on the volume.

**If the gateway cannot start at all** — no account set, or no remembered token
yet — the container does *not* exit. The supervisor logs the reason and runs the
MCP server without a gateway, so `check_health` still answers and tells you the
gateway is unavailable. Fix `.env`, then restart the container. A malformed
supervision setting (`OPEND_MAX_RESTARTS`, `OPEND_RESTART_WINDOW_SECONDS`,
`SUPERVISOR_STOP_TIMEOUT_SECONDS`) is treated the other way and refuses to
start, naming the setting, rather than running under a default you did not
choose.

That is also the cost of the single container, and it is worth stating plainly:
**every deploy restarts OpenD**, because there is no longer a way to update the
server without replacing the container. Two containers could be upgraded
independently; this one cannot.

Do not publish OpenD's port 11111 to get around a problem. Its API has no
authentication; it listens on container loopback by design, and nothing outside
the container is meant to reach it.

## Everyday: rotate `MCP_AUTH_TOKEN`

Rotate whenever the token has been displayed, shared, or copied into a client
you no longer control. Only the MCP server reads it, so OpenD keeps its session
and no interactive login is needed.

```sh
cd "$HOME/moomoo"
NEW_TOKEN="$(openssl rand -hex 32)"
sed -i "s|^MCP_AUTH_TOKEN=.*|MCP_AUTH_TOKEN=${NEW_TOKEN}|" .env
systemctl --user restart moomoo.service
echo "$NEW_TOKEN"
```

Paste the printed value into every client config's `Authorization: Bearer …`
header, then confirm the old token is refused and the new one is accepted:

```sh
curl -si -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer ${NEW_TOKEN}"
```

Clients keep working across the restart itself — the endpoint is stateless, so
there is no session to lose — but the token they present must be the new one.
A client still sending the old token is refused with 401 until its configuration
is updated.

## Stop the deployment

```sh
systemctl --user stop moomoo.service
cd "$HOME/moomoo"
./scripts/compose-prod.sh down       # do not add -v; preserve device tokens
```

This retains the named volume. Account changes and deliberate token removal
require a separate, explicit storage-cleanup procedure; deleting a similarly
named directory in the checkout does not remove the Docker volume.
