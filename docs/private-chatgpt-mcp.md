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
