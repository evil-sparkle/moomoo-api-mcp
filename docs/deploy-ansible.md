# Application-owned Ansible deployment and CI/CD

Moomoo uses the same deployment arrangement as `ccxt-mcp-go`: the application
repository owns the playbook and role, CI publishes a release record, and a
separate deployment workflow rolls out its immutable image digests. Host
provisioning remains outside this release workflow.

The [production branch guide](deployment-branches.md) explains `DEPLOY_BRANCH`,
temporary hotfix releases, feature synchronization and the return to `main`.

Native deployment supports **READ_ONLY**, **SIMULATE** and **REAL** on Linux
AMD64 with the application user's rootless Docker daemon and named OpenD/state
volumes. Set the public expected mode explicitly and match it in the protected
server configuration. Paper, REAL and controls use the same overlays and stable
volume names as production. Existing state mounts remain attached when trading
is disabled so inspection and recovery can remain available.

The tunnel is an independent selection. Selecting `false` removes a previous
tunnel during the verified rollout; selecting `true` requires a separate
`MCP_RESTRICTED_AUTH_TOKEN` and the server-side tunnel settings. The primary MCP
credential is never forwarded to the tunnel. Client permissions still remain
bounded by the deployment's trading policy.

## What a release does

1. CI tests the application and native deployment, builds or retags both images,
   and saves `moomoo-deployment-<attempt>/deployment.json`. The public record
   binds both ECR digests to the full commit, CI run and successful attempt.
2. `auto-deploy.yml` accepts successful **push** CI from this repository's
   selected `DEPLOY_BRANCH` (`main` when unset).
   It checks the webhook's exact run/attempt and skips a superseded revision.
   It dispatches the same `deploy.yml` used for a manual rollback.
3. `deploy.yml` independently rechecks the CI record and manifest, and checks
   the current selected-branch revision again for automatic requests. The
   controller workflow itself stays on `main`. Deployment requests share one
   concurrency group and never cancel an in-progress rollout.
4. Ansible requires an explicit single-host limit and non-root execution. It
   uses a shared deployment lock, exports an allowlist of public files from the
   exact source commit and installs a separate release directory. It never
   copies a workstation credential file or writes the server's `.env`.
5. The host helper checks configuration file ownership and mode 0600, then
   lets Compose resolve it on the server. Each rollout saves the complete
   resolved model as `resolved-compose.json` at mode 0600. This private file
   contains credentials: keep it on the server and exclude it from support
   bundles, Git, logs and artifacts. Commands suppress raw errors and settings.
6. Existing container project and mount metadata determine the persistent
   volume names. New paper, REAL or controls storage is added only when selected;
   existing volumes remain attached across mode changes. The selected image
   validates the complete application configuration without networking, then
   checks journal/control schemas on read-only mounts before replacing any
   container. Missing journals require their explicit creation flags; deployment
   never silently initializes or resets them.
7. Inside MCP, checks verify unauthenticated rejection, authenticated
   initialization, required inspection tools and the exact selected trading
   mode. Runtime checks require the selected image, non-root OpenD identity,
   selected state volumes and host `127.0.0.1:8000` with no published OpenD port.
8. A failed start, MCP check or selected tunnel check restores the previous
   release's frozen configuration. First migration freezes the actual running
   container configuration and any existing tunnel before changes. Rollback
   therefore does not depend on an edited `.env`. The workflow still fails
   after rollback. State is preserved; rollback does not undo a schema upgrade.
9. OpenD readiness is reported separately: quote login and quote/trade probes
   must succeed for the ready result. A broker login/availability problem keeps
   a verified MCP application running with a readiness warning.
10. The verified release becomes `current`. Ansible installs and enables the
    user's `moomoo-ansible.service`, then disables legacy `moomoo.service`
    startup. Rootless Docker and user lingering must already be configured.

## One-time deployment access

Create the **`vultr-production`** environment in this repository. CCXT's
environment settings belong to its repository and do not carry over here.
Configure these public variables with independently verified metadata:

| Variable | Value |
| --- | --- |
| `TS_CLIENT_ID` | Tailscale OIDC client authorized for this repository/environment |
| `TS_AUDIENCE` | That OIDC client's audience |
| `VPS_HOST` | The application's verified Tailscale IPv4 address |
| `VPS_USER` | The existing non-root application/Docker user |
| `VPS_PORT` | Its ordinary SSH port over Tailscale |
| `VPS_HOST_KEY` | The pinned public known-hosts entry for the exact host and port |
| `MOOMOO_COMPOSE_PROJECT` | The running Moomoo container's existing Compose project |
| `MOOMOO_ENV_FILE` | Absolute path to its existing protected configuration file |
| `MOOMOO_EXPECTED_MODE` | Exact `READ_ONLY`, `SIMULATE` or `REAL`; defaults to `READ_ONLY` |
| `MOOMOO_TUNNEL_ENABLED` | Explicit `true` to enable/preserve the tunnel; `false` to disable it |

The environment also needs the operator-installed deployment secret
`VPS_SSH_KEY`. The workflow loads it into a temporary SSH agent and removes
that agent/session afterward. It never receives the Moomoo login credentials,
trade password, MCP token, AWS pull credentials or tunnel runtime key.

Authorize the ephemeral **`tag:moomoo-deploy`** identity for only this server's
ordinary SSH port in the tailnet policy. Pin the SSH host key; do not replace
host verification with key scanning at deployment time. Grant the deployment
public key to this application's existing user. Existing ECR pull access and
the credential helper stay server-side.

The AWS image-publishing OIDC configuration already used by `ci.yml` remains
the publishing path. No new cloud resource or IAM mutation is part of this
repository change. Access setup and a live rollout are separate from local
validation of the implementation.

## First cutover and normal deployment

Complete the [initial host/OpenD setup](deploy-vps.md) first. Confirm the
selected expected mode matches the protected configuration, its file is owned
by the application user at mode 0600, and the running container uses the intended
Compose project and named state volumes. Perform account login and device
verification directly as the operator.

Select the application branch with `DEPLOY_BRANCH`, wait for its publishing CI
to finish successfully, and select that CI run in **Vultr deployment**. Use its
explicit successful attempt for the first cutover. Automatic deployment follows
successful pushes to the selected branch once deployment access is configured.
Manual CI publication requires a separate deployment dispatch. For that
dispatch, leave `automatic=false`; the controller workflow's branch must be `main`.

```sh
gh workflow run deploy.yml --repo evil-sparkle/moomoo-api-mcp --ref main \
  -f ci_run_id='<successful-selected-branch-CI-run-id>' \
  -f ci_run_attempt='<successful-attempt>' -f automatic=false
```

The run summary records the selected commit, image digests, source CI run and
deployment outcome. A successful dispatch only requests a deployment; check
the **Vultr deployment** run and its Ansible readiness report for the result.

## Manual rollback

To restore the exact settings of a previously verified native release, run its
public release identifier through the current helper on the server:

```sh
release_root="$HOME/.local/share/moomoo-api-mcp"
ls "$release_root/releases"
python3 "$release_root/current/scripts/ansible_deployment.py" rollback '<revision>-<configuration-id>'
```

The helper holds the deployment lock, validates the selected private snapshot and
storage, starts its frozen configuration and verifies authenticated MCP before
atomically selecting it for future starts. Failure restores the current release.
It never reloads edited `.env` settings or rewinds/deletes journal data. Select a
release from this same project; older releases without frozen snapshots are not
eligible for this operation.

Dispatching the workflow with an older successful selected-branch CI run deploys
that older image **with current server settings**. This is an image redeployment,
not restoration of a historical configuration. Automatic deployment skips a
revision that is no longer the selected branch's current head. Failed/in-progress
CI, foreign repositories and mismatched attempts are refused. Tags such as
`latest` are never accepted.

CI artifacts are retained for 30 days. ECR image retention is independent: a
record can outlive its image, in which case pulling fails before the current
application is replaced. Check configuration/schema compatibility before
rolling back across application changes. State volumes are reused, not restored
from a historical snapshot.

## Migration compatibility lifetime

Only first cutover reconstructs the legacy runtime, including its optional tunnel,
and validates `previous-compose.json` before changing containers. Once `current`
identifies a verified native release, later rollouts use that release's existing
snapshot for failure recovery and do not reconstruct legacy configuration.

After cutover, a native restart, and a frozen-release rollback have been verified
on the host, remove the first-cutover capture/restore branch and retire the legacy
deployment scripts/service in a cleanup PR. Keep normal native rollback and its
private release snapshots. Do not remove migration support before that evidence
exists; this code change does not itself perform a live cutover.

## Local controller entrypoint

The controller needs Git, Python 3.10+ and `ansible-core==2.21.5`. Supply a
private local inventory with only the verified connection metadata and a JSON
settings file containing **public** release/state selection. Keep credentials
on the server. Example settings are:

```json
{
  "moomoo_mcp_revision": "<40-character-source-commit>",
  "moomoo_mcp_image": "<registry>/moomoo-api-mcp@sha256:<64-character-digest>",
  "moomoo_mcp_tunnel_image": "<registry>/moomoo-api-mcp@sha256:<64-character-tunnel-digest>",
  "moomoo_mcp_project": "<existing-compose-project>",
  "moomoo_mcp_env_file": "/home/<application-user>/moomoo/.env",
  "moomoo_mcp_expected_mode": "READ_ONLY",
  "moomoo_mcp_tunnel_enabled": false
}
```

```sh
cd deployments/moomoo
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook deploy.yml --limit vultr \
  -i /absolute/path/to/public-inventory.ini \
  -e @/absolute/path/to/public-release-settings.json
```

Use settings from a verified successful CI manifest. The selected source commit
must exist in the controller checkout. Each deployment gets a fresh release
identity, including configuration-only changes. Prior private snapshots remain
unchanged for rollback.

Native releases, direct native lifecycle commands, the current legacy helper,
and systemd lifecycle commands share
`~/.local/share/moomoo-api-mcp/.deployment-lock`. Ansible holds it for the whole
transaction and delegates ownership to its subprocesses with a private token;
other callers fail before changing runtime state. Set `MOOMOO_DEPLOY_LOCK_ROOT`
consistently if using a custom deployment root. Older legacy versions without
this helper must not run concurrently. An interrupted controller can leave a
lock: inspect the deployment and confirm no owner remains before removing it.

## Restarts and verification limits

After cutover, use `systemctl --user restart moomoo-ansible.service` to restart
the selected release. Start/restart uses its frozen configuration and repeats
offline application/storage validation under the shared lock. Editing `.env`
does not alter a running or saved release; deploy again to validate and apply
configuration changes. Persistent OpenD and journal volumes survive recreation.
Automatic upgrades never modify the legacy checkout.

Local tests run the actual playbook against disposable files and Docker command
stubs, and separately render the production definition through real Compose.
MCP probes run against a synthetic local HTTP service. These checks establish
deployment/rollback behavior, mode and tunnel transitions, private snapshots,
and exclusion of concurrent lifecycle changes. They do not establish a
logged-in broker or a live VPS rollout.

For a selected tunnel, local liveness/readiness verifies only the client on
the server. A hosted ChatGPT tool call is still required to prove forwarding.
Broker availability, ECR publication, GitHub environment access and hosted
ChatGPT acceptance must be checked live after deployment.

