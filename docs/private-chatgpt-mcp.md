# Private ChatGPT tunnel: official-client Compose integration

The owner approved the managed official-client approach on 2026-10-07. Use the
unmodified, integrity-verified v0.0.14 release; this project maintains no tunnel
client fork. Normal deployment remains tunnel-free. Explicit Compose selection
retains authentication, server-enforced READ_ONLY, legacy-service exclusivity and
bounded authenticated startup checks. PR #38 remains a draft implementation.

## Managed deployment assumptions and known limitations

The configuration trusts the genuine OpenAI HTTPS endpoint at
`https://api.openai.com` with certificate verification and the controlled Docker
host. It fixes the private MCP URL and strips inherited proxy, custom-CA and
endpoint/configuration overrides. Runtime key permissions are Tunnels Read + Use;
OpenAI admin, brokerage and operator credentials do not enter the tunnel.

The preserved synthetic tests demonstrate upstream redirect credential diversion,
direct-client proxy behavior and omitted authentication on doctor/redirected MCP
requests. The owner accepts those conditional limitations for this fixed managed
setup. Proxy filtering does not fix the upstream redirect behavior. No malicious
redirect from the real OpenAI endpoint or actual credential exposure is established.
Normal authenticated forwarding, preflight, negative auth, rotation, isolation and
recovery through the managed image remain mandatory acceptance checks.

The direct-client runners retain their FAIL/INCONCLUSIVE verdicts. CI validates
and publishes their sanitized reports separately from required managed integration;
malformed/incomplete reports or fixture errors fail reporting. Historical evidence
is unchanged. Live OpenAI, VPS, ChatGPT web and native iPad acceptance remain
PENDING; implementation and synthetic CI do not demonstrate those milestones.

## Intended managed deployment

Compose is the recommended replacement: the existing `moomoo-mcp` container still
contains exactly two supervised processes (MCP and OpenD). Explicit selection of
`docker-compose.chatgpt.yml` adds `chatgpt-tunnel`, a separate unprivileged container.
Default deployment requires no tunnel credentials or setup. Keep the same Compose
project, OpenD volume, journal volumes, and host publication `127.0.0.1:8000:8000`.
OpenD stays `127.0.0.1:11111` **inside the brokerage container**; it is never
published. The sidecar reaches only `http://moomoo-mcp:8000/mcp` over Docker DNS.
It shares neither network nor PID namespaces, publishes no ports, and mounts no
brokerage state, journals or Docker socket. The production bridge allows outbound
connectivity. Only disposable test networks prohibit internet egress.

The overlay opts the MCP server into the exact Host `moomoo-mcp:8000` using
`MCP_ALLOW_CHATGPT_TUNNEL_HOST=1`. The preflight separately requires
`--allow-compose-mcp`; unexpected Hosts and Origins remain rejected. Preflight
rejects redirects and unapproved destinations; the managed client excludes
inherited proxies. The ordinary MCP bearer protects initialization,
discovery and calls. It is distinct from the limited OpenAI runtime key. Never
supply an operator token, broker login, unlock material or OpenAI admin key.

## Build and protect the inputs

These are operator preparation instructions. This implementation request does not
execute a production migration or read actual credential files.
Build `scripts/build-tunnel-image.sh` using the selected Docker context. Only its
enumerated public files enter the build context. The image uses the unchanged
reviewed release manifest, verifies the archive before executing the binary, and
pins the slim base image by digest. There is no runtime download. Record the local
immutable image ID and the reviewed source commit; reuse that ID for recreation.
The daemon runs as numeric UID/GID `10002:10002`, with a read-only root, no
capabilities, no-new-privileges and a bounded private runtime tmpfs.

Keep existing master credentials root:root mode `0600`. The existing no-echo
`scripts/install_private_chatgpt_credential.py` remains the master input helper;
its terminal restoration and atomic writes are unchanged. A root-owned tunnel-id
file is also required. Do not put credentials in YAML, build arguments, images,
process arguments, shell history, logs, or tracked files.

Provision staged copies under
`/var/lib/moomoo-chatgpt-tunnel/compose-secrets/<deployment-id>`. Run
`scripts/stage_tunnel_secrets.py` as root with an explicit local Docker Unix socket
and immutable image ID. First run it without directory arguments: its disposable
marker measures the host mapping of UID/GID 10002 and cross-checks the UID/GID maps.
Do not guess subuid offsets or assume Compose secret uid/gid/mode overrides work.
Rootful and rootless mappings differ; verify on the actual daemon host.

Create root-owned `0700` staging parents. On each necessary parent grant only
traversal ACLs to the measured runtime UID and, for rootless Docker, the daemon
owner. Do not grant write access, default/inheritable ACLs or access to master
credentials. The helper takes `--master-directory` and `--staging-directory`,
rejects symlinks/unsafe writable parents, and atomically writes the three fixed
files with mapped ownership and mode `0400`. Each file is mounted read-only at
`/run/secrets/`. The root provisioning helper is not a root tunnel daemon.
Root and the Docker controller remain trusted administrators.

## Operator migration sequence

1. Review official provenance, required managed image checks and the accepted
   upstream limitations; choose the existing READ_ONLY deployment to migrate.
2. Stop and disable `moomoo-chatgpt-tunnel.service`; confirm it is inactive before
   starting the Compose replacement. Never run both for the same tunnel.
3. Confirm the existing MCP deployment is READ_ONLY. Record the existing Compose
   project, image identities and volume names; do not create a replacement project.
4. Provision the mapped files and validate access with the actual image/UID.
5. Record selection using `scripts/tunnel_deployment.py select --image <image-id>
   --secret-directory <protected-directory> --project <existing-project>`.
   `.chatgpt-deploy.json` contains only non-secret selection metadata and survives
   deployment checkout/rollback. It is not a credential file.
6. After preparation and review, recreate MCP with the selected
   overlay to apply the Host opt-in; preserve its volumes. Start only the tunnel
   service after MCP is reachable. A selected overlay refuses SIMULATE/REAL mode.

The startup manager retries transient MCP failures for at most 90 seconds. It
requires authenticated initialize, tools/list and READ_ONLY health before launching
any polling client. Degraded OpenD can pass this gate; it is not account acceptance.
The ordinary bearer is **not permanently read-only**: stop/disable the tunnel
before changing MCP to SIMULATE or REAL.

## Operations, diagnostics and rotation

Use the saved wrapper selection consistently. `scripts/compose-prod.sh exec
chatgpt-tunnel python /opt/tunnel/runtime.py diagnostics` checks the listener on the
tunnel container's own loopback and separately reports liveness, client startup
readiness and authenticated MCP availability. The official `/readyz` endpoint can
stay green during a control-plane outage; it is not proof of successful polling
or end-to-end forwarding. Diagnostics explicitly report that limitation. No health/admin port is published. Raw client output is suppressed;
manager diagnostics contain only fixed messages, not keys, Authorization headers,
account results or raw MCP bodies. Official `doctor` currently fails unauthenticated
OAuth metadata discovery against the authenticated MCP endpoint; it does not replace
the authenticated startup gate or constitute a passing compatibility result.

Child exit or three failed bounded liveness probes makes the manager terminate
and exit nonzero for Docker restart. Control-plane readiness loss alone does not
restart a healthy process. SIGTERM/SIGINT are forwarded, with a ten-second grace
before forced termination; Compose allows twenty seconds. MCP availability and
broker-backed availability are separate conditions.

Rotate runtime key and MCP bearer separately. Stop the tunnel, atomically update
the root master with the no-echo helper, stage the new mapped copy, then **force
recreate** the sidecar with the same image/project. Restarting a process or container
can retain a bind mount of the old inode. Verify new authenticated traffic and
old-value rejection without printing either value. For MCP bearer rotation,
coordinate the MCP recreation and existing local clients (including ZeroClaw)
before recreating the tunnel. Rotation is incomplete without this behavioral proof.

## Disable and rollback

`scripts/tunnel_deployment.py disable` stops/removes only `chatgpt-tunnel` and then
clears its selection. Do not use `docker compose down` or delete volumes to stop a
tunnel. MCP and ZeroClaw remain usable. Before selecting an older commit without
overlay support, disable the tunnel; deployment refuses an unsupported rollback
while selection exists. Keep recorded OpenD/journal volume identities unchanged.

For legacy rollback, first stop/remove the Compose tunnel and clear selection;
only then restore/start the legacy systemd unit with the original root-only masters.
The legacy assets below are retained for migration/rollback, not a second required
service. Restoring a legacy mechanism does not waive any known client security issue
or constitute live product acceptance.

## Verification boundary

See the active OpenSpec independent verification report for exact passes, failures
and untested cases. The container harness uses synthetic files, unique projects,
a simulated control plane, random loopback publications and project-scoped cleanup.
Its gateway is an OpenD network stub, not a broker login. Rootful/rootless permission
checks and normal forwarding cannot stand in for redirect/proxy confinement.
VPS deployment, real OpenAI acceptance, full account/positions acceptance,
ChatGPT web invocation and native iPad acceptance remain **PENDING**.

## Legacy systemd installation reference

The following retained instructions are a legacy migration/rollback reference only.
Use it only as a rollback path with Compose stopped and disabled.

# Private ChatGPT access through OpenAI Secure MCP Tunnel

This optional owner-operated path connects supported OpenAI products to the
existing private MCP deployment. It publishes no new listener. MCP stays on
host loopback at `127.0.0.1:8000`; OpenD stays on container loopback at
`127.0.0.1:11111` and remains unpublished. ZeroClaw and other local clients keep
using the existing endpoint and bearer credential.

The accepted deployment is valid only while the MCP server reports
`MOOMOO_TRADING_MODE=READ_ONLY`. Stop and disable the tunnel before changing
that mode. Enabling SIMULATE or REAL while the tunnel exists requires a separate
reviewed change. Tool annotations and ChatGPT confirmation behavior do not
authorize calls or contain broker risk.

## Reviewed sources and version

These official sources were checked on 2026-09-24:

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels),
  an unversioned OpenAI guide retrieved on that date.
- [Connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt),
  an unversioned OpenAI guide retrieved on that date.
- [`openai/tunnel-client`](https://github.com/openai/tunnel-client) stable tag
  `v0.0.14`, published 2026-09-01, tag commit
  `0f870e50a973fa820d4c409000059e181e8d242b`.
- The tag's `configuration.md`, `architecture.md`, `connectors.md`,
  `permissions.md`, and `deployment/systemd-vm.md`. The then-current `master`
  documentation at `cce7a8226654c432ce53c7e05e17a9825a34b6e3` was inspected
  for drift; this integration uses behavior present in the stable tag.

The pinned Linux amd64 archive and digest are in
`deploy/tunnel-client/release.json`. The archive includes a Cloudflare
companion, but this configuration does not enable or start it. The daemon makes
outbound HTTPS requests to `api.openai.com:443` under `/v1/tunnels/*`; it needs no
inbound route, firewall opening, Tailscale Funnel, or native TLS on Python.

Tunnel-client v0.0.14 sends `mcp.extra_headers` on ordinary MCP traffic and
`mcp.discovery_extra_headers` on discovery and startup probes. Both use the same
file-backed ordinary MCP Authorization header. Connector-forwarded headers apply
later, so a conflicting Authorization value gets a 401. Do not retry without
authentication.

The client drops forwarded Host and derives `127.0.0.1:8000` from the configured
URL. It can forward Origin. This server accepts no nonempty Origin, so an
unexpected Origin fails with 403. Do not add a wildcard or disable DNS rebinding
protection. If an eligible product test proves a stable Origin is required,
propose one exact allowlist entry in a separate reviewed change.

## Security boundary

The service runs as the dedicated `moomoo-tunnel` Unix identity. It gets only:

- a runtime API key restricted to Tunnels **Read** and **Use** for the intended
  tunnel;
- the tunnel ID associated with the intended Platform organization and ChatGPT
  workspace; and
- the complete ordinary local header value `Bearer <MCP_AUTH_TOKEN>`.

The daemon gets no Tunnels Manage permission, OpenAI administration key,
`MCP_OPERATOR_TOKEN`, brokerage login material, trade-unlock credential, Docker
socket, repository write access, or OpenD access. Tunnel managers use a separate
interactive administration path; those credentials never enter this unit.

Systemd exposes the two credential copies only to the service. Their root-owned
source files must be mode `0600`. The non-secret config directory is
`root:moomoo-tunnel` mode `0750`, and its YAML is mode `0640`, so the service can
traverse and read the config without gaining read access to either credential
source. The MCP credential file contains the whole HTTP header, including the
`Bearer ` prefix. Never put either credential in argv, an environment file, a
tracked file, diagnostic output, terminal scrollback, or a ticket.

Tool annotations help a product describe or confirm operations. They are
advisory. No supported per-connection ChatGPT tool allowlist was found in the
reviewed documentation. Server-enforced `READ_ONLY` is the authorization
boundary, and tests prove mutation methods do not reach broker writes in that
mode. `get_stock_quote` and `get_order_book` are marked mutating because they
auto-subscribe the shared quote connection.

## Local preflight

The verifier reads Authorization from a protected file and never accepts it on
argv. It prints milestone names only, never account data, response bodies, or
credential values.

Startup-safe mode validates initialize, `tools/list`, and `check_health`, and
requires `trading_mode=READ_ONLY`. An honest `degraded` or `disconnected` OpenD
status can pass while the supervised gateway recovers:

```console
python3 scripts/private_chatgpt_preflight.py \
  --mode startup-safe \
  --authorization-file /path/to/restricted/mcp-authorization
```

Full mode also discovers accounts, finds one exact owner-selected decimal-string
ID, and requests positions. It fails if OpenD or the account is unavailable.
Treat account IDs as private operational data and never commit them:

```console
python3 scripts/private_chatgpt_preflight.py \
  --mode full \
  --authorization-file /path/to/restricted/mcp-authorization \
  --trd-env SIMULATE \
  --account-id '<OWNER_SELECTED_ACCOUNT_ID>'
```

The default URL is `http://127.0.0.1:8000/mcp`; non-loopback URLs are refused. A
PASS proves concrete JSON-RPC results rather than merely HTTP 200.

## Owner installation procedure

These are owner instructions. Repository automation does not run them and this
change does not deploy anything.

1. Confirm Linux amd64. Download the archive named in the release manifest, then
   verify it without installing:

   ```console
   python3 deploy/tunnel-client/install.py \
     --archive /path/to/tunnel-client-v0.0.14-linux-amd64.zip \
     --verify-only
   ```

   The installer checks SHA-256 before extracting or executing the binary, then
   requires `tunnel-client --version` to report `0.0.14`.

2. Create the identity and install reviewed files:

   ```console
   sudo useradd --system --home-dir /var/lib/moomoo-chatgpt-tunnel \
     --create-home --shell /usr/sbin/nologin moomoo-tunnel
   sudo python3 deploy/tunnel-client/install.py \
     --archive /path/to/tunnel-client-v0.0.14-linux-amd64.zip
   sudo install -d -o root -g moomoo-tunnel -m 0750 \
     /etc/moomoo-chatgpt-tunnel
   sudo install -o root -g moomoo-tunnel -m 0640 \
     deploy/tunnel-client/tunnel-client.yaml \
     /etc/moomoo-chatgpt-tunnel/tunnel-client.yaml
   sudo install -o root -g root -m 0755 \
     scripts/private_chatgpt_preflight.py \
     /usr/local/libexec/private_chatgpt_preflight.py
   sudo install -o root -g root -m 0755 \
     scripts/install_private_chatgpt_credential.py \
     /usr/local/libexec/install_private_chatgpt_credential.py
   sudo install -o root -g root -m 0644 \
     deploy/tunnel-client/moomoo-chatgpt-tunnel.service \
     /etc/systemd/system/moomoo-chatgpt-tunnel.service
   ```

3. Write the non-secret tunnel identifier, replacing only the placeholder:

   ```console
   printf '%s\n' 'CONTROL_PLANE_TUNNEL_ID=<OWNER_TUNNEL_ID>' | \
     sudo install -o root -g moomoo-tunnel -m 0640 /dev/stdin \
       /etc/moomoo-chatgpt-tunnel/tunnel-id.env
   ```

4. Create each credential through the installed helper. It requires an
   interactive terminal, disables echo before reading one line, restores the
   terminal on success, error, or interruption, and atomically creates a
   `root:root` mode `0600` source file. Enter the complete MCP header, including
   the `Bearer ` prefix. Values never appear in argv or terminal scrollback:

   ```console
   sudo /usr/local/libexec/install_private_chatgpt_credential.py \
     control-plane-api-key
   sudo /usr/local/libexec/install_private_chatgpt_credential.py \
     mcp-authorization
   ```

   Verify the installed boundary without displaying either credential:

   ```console
   sudo -u moomoo-tunnel test -r \
     /etc/moomoo-chatgpt-tunnel/tunnel-client.yaml
   sudo -u moomoo-tunnel test ! -r \
     /etc/moomoo-chatgpt-tunnel/control-plane-api-key
   sudo -u moomoo-tunnel test ! -r \
     /etc/moomoo-chatgpt-tunnel/mcp-authorization
   ```

5. Confirm the existing deployment still has one container, only loopback MCP,
   no OpenD host port, and a passing startup-safe preflight. Then load and start
   only the optional unit:

   ```console
   sudo systemctl daemon-reload
   sudo systemctl enable --now moomoo-chatgpt-tunnel.service
   sudo systemctl status moomoo-chatgpt-tunnel.service
   ```

   The unit runs local preflight and `tunnel-client doctor --explain` first. A
   failed prerequisite leaves this unit failed and does not stop the MCP
   container.

## Health, diagnostics, and failure matrix

`http://127.0.0.1:8080/healthz` is process liveness. `/readyz` means the client
can poll and dispatch tunnel work. Loopback `/ui` and `/metrics` are diagnostics;
never proxy or publish them.

Use this order without reading credential source files:

```console
/opt/openai/tunnel-client/v0.0.14/tunnel-client --version
curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null
curl --fail --silent http://127.0.0.1:8080/readyz >/dev/null
sudo systemctl status moomoo-chatgpt-tunnel.service
sudo journalctl -u moomoo-chatgpt-tunnel.service --since=-15m
```

| Failure | Expected evidence | Local service impact | Coverage |
| --- | --- | --- | --- |
| MCP stopped/restarting | Preflight reports unreachable; tunnel calls fail | OpenD supervision is unchanged; stateless calls recover without an old session ID | Automated local-process fixture |
| Tunnel process exits | Health/readiness disappear; systemd restarts it | Loopback MCP remains available and authenticated | Simulated tunnel-process fixture; owner systemd check PENDING |
| OpenAI control plane unavailable | Liveness may be 200 while readiness is 503 | Loopback MCP remains available; no fabricated tunnel success | Simulated tunnel-process fixture; owner official-client check PENDING |
| OpenD unavailable | Health says degraded/disconnected; startup-safe proves READ_ONLY; full mode fails | Existing recovery continues; no write dispatch | Automated fixture |
| Conflicting Authorization | Local MCP returns 401 | No anonymous retry or broker call | Automated fixture |
| Unexpected Origin | Local MCP returns 403 | Host/Origin protection remains enabled | Automated fixture plus owner web check |
| Account/workspace ineligible | Tunnel cannot be selected or associated | Local and tunnel checks stay separate | Owner-operated, PENDING |

## Secret rotation

Rotate the runtime key with the same no-echo helper command for
`control-plane-api-key`, which atomically preserves root ownership and mode
`0600`, then restart this unit. Failure affects only the tunnel.

The MCP bearer is shared with local clients. Coordinate its rotation:

1. Stop the optional tunnel unit.
2. Rotate `MCP_AUTH_TOKEN` through the existing deployment secret procedure.
3. Update ZeroClaw and other clients through their existing secret paths.
4. Replace `mcp-authorization` with the complete new header through the no-echo
   helper command above.
5. Restart MCP, run startup-safe and full local preflight, then start the tunnel
   and confirm readiness.

Never create an unauthenticated interval. `MCP_ALLOW_UNAUTHENTICATED_HTTP` is not
an integration workaround.

## Staged acceptance record

Repository tests complete isolated portions of stages 1 and 2. The owner records
product checks without copying secrets or private account results.

| Stage | Required observation | Status after this change |
| --- | --- | --- |
| 1. Local MCP | initialize, tools/list, authenticated READ_ONLY health, selected account and positions result | PENDING owner full preflight |
| 2. Tunnel runtime | pinned version, doctor PASS, health 200, readiness 200, restart recovery | PENDING owner host run |
| 3. OpenAI eligibility | intended Platform organization and ChatGPT workspace associated; principals have Read + Use; developer mode allowed by policy | PENDING owner/account prerequisite |
| 4. ChatGPT web | tunnel selected, tools discovered, health and authorized positions invoked, mutation refused | PENDING owner credentialed test |
| 5. Native iPad | same private app discovered and health plus an authorized read invoked in the native app | PENDING; support and eligibility unverified |

A successful web connection completes only stage 4. It does not prove native
iPad support. Do not assume that a developer-mode connection made on the web
appears in the native app.

## Rollback

Rollback removes only the optional host integration:

```console
sudo systemctl disable --now moomoo-chatgpt-tunnel.service
sudo systemctl reset-failed moomoo-chatgpt-tunnel.service
```

After preserving needed redacted diagnostics, remove the unit, its two
credential files, non-secret config, preflight and credential-helper copies, and
pinned binary; then run `sudo systemctl daemon-reload`. Revoke the runtime key
and remove the tunnel association through the owner's OpenAI administration
process.

Rollback does not run Compose, remove volumes, edit Tailscale or firewall
settings, touch OpenD state, alter the paper journal, change ZeroClaw, or modify
the MCP bearer used by remaining local clients.
