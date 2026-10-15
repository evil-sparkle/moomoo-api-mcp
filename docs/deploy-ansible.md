# Application-owned Ansible deployment and CI/CD

Moomoo uses the same deployment arrangement as `ccxt-mcp-go`: the application
repository owns the playbook and role, CI publishes a release record, and a
separate deployment workflow rolls out its immutable image digests. Host
provisioning remains outside this release workflow.

The first migration supports an existing **READ_ONLY** stack on Linux AMD64
with the application user's rootless Docker daemon and named OpenD/state
volumes. It refuses REAL/SIMULATE configuration, bind-mounted state, missing
OpenD state and a project mismatch before replacing containers. Keep using the
legacy bootstrap method for those configurations until their deployment
migration is separately implemented and verified.

The first native version preserves an existing enabled tunnel. It refuses an
implicit disable selection when that tunnel still exists. Make any required
legacy tunnel disablement before cutover; native tunnel disablement is outside
this first migration's scope.

## What a release does

1. CI tests the application and native deployment, builds or retags both images,
   and saves `moomoo-deployment-<attempt>/deployment.json`. The public record
   binds both ECR digests to the full commit, CI run and successful attempt.
2. `auto-deploy.yml` accepts successful main **push** CI from this repository.
   It checks the webhook's exact run/attempt and skips a superseded revision.
   It dispatches the same `deploy.yml` used for a manual rollback.
3. `deploy.yml` independently rechecks the CI record and manifest, and checks
   current main again for automatic requests. Deployment requests share one
   concurrency group and never cancel an in-progress rollout.
4. Ansible requires an explicit single-host limit and non-root execution. It
   uses a shared deployment lock, exports an allowlist of public files from the
   exact source commit and installs a separate release directory. It never
   copies a workstation credential file or writes the server's `.env`.
5. The host helper checks credential **file metadata** and lets Compose consume
   the existing file. Resolved settings stay in memory; raw command failures,
   response bodies and credentials are suppressed. This resolution runs on the
   server, not in GitHub Actions.
6. It verifies the existing container's project and mount metadata, and pins
   those same external volume names into the new Compose overlay. It pulls the
   selected digests before recreating containers. It never deletes volumes or
   initializes a new journal.
7. Checks inside MCP verify rejection of unauthenticated requests, authenticated
   initialization, required read-only tools and READ_ONLY mode. Runtime checks
   require the selected image, non-root OpenD identity, unchanged state volumes
   and host `127.0.0.1:8000` with no published OpenD port.
8. A failed start, MCP check or selected tunnel check restores the previous
   release. On the first migration, it uses the untouched legacy Compose
   wrapper. The workflow still fails after rollback so the failed release is
   visible. Recovery preserves state; it does not undo a journal schema upgrade.
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
| `MOOMOO_TUNNEL_ENABLED` | `true` when preserving an existing tunnel; otherwise `false` |

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
current application is READ_ONLY, its configuration file is owned by the
application user at mode 0600, and the running container uses the intended
Compose project and named state volumes. Perform account login and device
verification directly as the operator.

Merge the deployment implementation, wait for its main CI to finish
successfully, and select that CI run in **Vultr deployment**. Use its explicit
successful attempt for the first cutover. Automatic deployment follows later
successful main pushes once deployment access is configured. For manual
dispatch, leave `automatic=false`; the workflow's branch must be `main`.

```sh
gh workflow run deploy.yml --repo evil-sparkle/moomoo-api-mcp --ref main \
  -f ci_run_id='<successful-main-CI-run-id>' \
  -f ci_run_attempt='<successful-attempt>' -f automatic=false
```

The run summary records the selected commit, image digests, source CI run and
deployment outcome. A successful dispatch only requests a deployment; check
the **Vultr deployment** run and its Ansible readiness report for the result.

## Manual rollback

Dispatch the same workflow with an older successful main CI run that contains
`moomoo-deployment-<attempt>` and the native deployment files. Manual rollback
can select an older revision; automatic deployment skips it if it is no longer
main. Failed/in-progress CI, foreign repositories and mismatched attempts are
refused. Tags such as `latest` are never accepted.

CI artifacts are retained for 30 days. ECR image retention is independent: a
record can outlive its image, in which case pulling fails before the current
application is replaced. Check configuration/schema compatibility before
rolling back across application changes. State volumes are reused, not restored
from a historical snapshot.

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
must exist in the controller checkout. Never run the legacy deployment script
concurrently with native deployment or use it to upgrade an already migrated
server. The Ansible lock serializes native callers; the legacy script predates
that lock. A leftover lock after an interrupted controller requires operator
inspection before removal.

## Restarts and verification limits

After cutover, use `systemctl --user restart moomoo-ansible.service` to restart
the selected release. Persistent OpenD and journal volumes survive recreation.
The legacy checkout and its private configuration remain available for first
cutover recovery; automatic upgrades never modify its Git checkout.

Local tests run the actual playbook against disposable files and Docker command
stubs, and separately render the production definition through real Compose.
MCP probes run against a synthetic local HTTP service. These checks establish
deployment/rollback behavior, not a logged-in broker or a live VPS rollout.

For a selected tunnel, local liveness/readiness verifies only the client on
the server. A hosted ChatGPT tool call is still required to prove forwarding.
Broker availability, ECR publication, GitHub environment access and hosted
ChatGPT acceptance must be checked live after deployment.
