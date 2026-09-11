# Deploy moomoo-api-mcp to a Vultr VPS

The GitHub Actions workflow builds and pushes `moomoo-api-mcp` + `moomoo-opend` to ECR on every `main` push. This runbook covers everything from pulling that image to having a running, logged-in stack on a fresh Vultr instance.

**Two images, two runtime constraints.** The MCP server is ordinary — pull and run. OpenD is not: the first start must happen interactively so you can answer the device-verification prompt and "remember the password". Until that token lands in `opend-data`, no unattended start can complete login.

---

## One-time: provision the host

### 1. Rootless Docker prerequisites

Use the deploy user's rootless Docker installation on Linux. If it is already
working, skip installation. Otherwise follow [Docker's rootless setup guide](https://docs.docker.com/engine/security/rootless/),
which creates the `rootless` context and a user `docker.service`.
Install Git, the AWS CLI, the ECR credential helper, and current Docker Compose v2
if missing. On Ubuntu, Git/AWS/helper packages are `git`, `awscli`, and
`amazon-ecr-credential-helper`.

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
`BatchGetImage` to check both images, which the pull-only IAM policy already allows.

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
commit containing these files whose two CI image builds have published successfully.
The deployment script checks out the resolved commit before pulling its images.

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
MOOMOO_TRADE_PASSWORD_MD5=                     # blank until you intend to place orders; unlock_trade wants this
MOOMOO_MAX_ORDER_QTY=1000
MOOMOO_MAX_ORDER_NOTIONAL=10000

# MCP transport
MCP_TRANSPORT=streamable-http
MCP_AUTH_TOKEN=                                # generate: openssl rand -hex 32
```

`MOOMOO_LOGIN_ACCOUNT` is required even with `MOOMOO_LOGIN_BY_REMEMBER=1`: the
remembered-token path passes `-login_account` alongside `-login_by_remember=1`.
Leaving it blank now exits the container with an explicit error rather than
leaving OpenD waiting on a console prompt that never arrives under `up -d`.

### 7. Prepare images, then perform interactive OpenD login

On the Terraform workstation, obtain the nonsecret registry hostname with
`terraform output -raw ecr_registry` from `aws/ecr-pull-iam-user`.
On the VPS, substitute that hostname below (only required on the first deploy):

```sh
cd "$HOME/moomoo"
export ECR_REGISTRY='<Terraform ecr_registry output>'
./scripts/deploy.sh --prepare
```

This checks both images, checks out the full commit, writes `.deploy.env`, and
pulls without starting services. It derives the seven-character tag from the
full commit; you do not enter image tags. An optional commit argument selects
an older published commit: `./scripts/deploy.sh --prepare <commit>`.

Every Compose command below uses `compose-prod.sh`, which loads `.env` and
`.deploy.env` explicitly. The same wrapper is used by systemd.

This is the one non-mechanical step. The Linux OpenD build has no password flag, so unattended `-login_pwd_md5` does not work. The only headless path is `login_by_remember`, which needs a token written by a previous **interactive** session that also cleared the device-verification code. Without `OPEND_INTERACTIVE=1`, a headless start without a remembered token now exits with an error instead of hanging.

```sh
cd "$HOME/moomoo"
./scripts/compose-prod.sh run --rm -it -e OPEND_INTERACTIVE=1 opend
```

OpenD prints its banner, then asks for the device-verification code. Check the Moomoo app on your phone, enter the 6-digit code. It then prompts:

> Remember the password? (Y/n)

Type `Y`. OpenD logs in, the token file appears under `/home/opend/.com.moomoo.OpenD/F3CNN/`, and the container exits when you Ctrl-C.

Verify the token:

```sh
./scripts/compose-prod.sh run --rm --no-deps --entrypoint /bin/sh opend -c \
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

`opend` should reach "TRC login OK" within ~30s. `moomoo-mcp` reports `MCP server listening on 0.0.0.0:8000`. Hit `http://localhost:8000/mcp` from the host (the port is bound to `127.0.0.1` only) with the `Authorization: Bearer $MCP_AUTH_TOKEN` header.

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
tag CI writes on every main build, and confirms it on both repositories with
`aws ecr batch-get-image`. AWS errors remain visible; a missing image stops the
deployment before checkout. It never uses `:latest` and ignores git `v*` tags,
which are release bookmarks only. If `main` has not finished publishing both
images, wait for CI or select an already-published commit. It does not search
for the newest green build.

How far back you can roll back is bounded by the ECR lifecycle policy, which
keeps every `v*`-tagged image and the 30 most recent commit builds per
repository.

After start, it verifies both containers are running and the MCP endpoint
answers (any non-5xx HTTP status, token not sent) within `DEPLOY_VERIFY_TIMEOUT`
seconds (default 90; must be a whole number of seconds). On failure, or if
`pull` or `up` fails, it restores the previous `.deploy.env` and commit
(restarting previous images if something had been started) and exits non-zero.
If the rollback's own restart fails, the script says so and the stack needs a
manual `scripts/compose-prod.sh up -d`. Verification does not prove OpenD login.
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

Existing client sessions do not survive the restart. Clients holding a session from
before it must reconnect; a stale session can surface as request-parameter
errors rather than an authentication failure.

## Stop the deployment

```sh
systemctl --user stop moomoo.service
cd "$HOME/moomoo"
./scripts/compose-prod.sh down       # do not add -v; preserve device tokens
```

This retains the named volume. Account changes and deliberate token removal
require a separate, explicit storage-cleanup procedure; deleting a similarly
named directory in the checkout does not remove the Docker volume.
