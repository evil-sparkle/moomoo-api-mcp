# State, restarts and credentials

What state this stack holds, where each piece of it lives, what survives which
restart, and who can read it. Read `deploy-vps.md` for how to run the thing;
this is for reasoning about it when something restarts or when you are deciding
how exposed a credential is.

## The shape of it

```
your client  ──HTTP──▶  moomoo-mcp  ──TCP──▶  opend  ──▶  Moomoo's servers
             127.0.0.1:8000         opend:11111
```

Two containers, each with its own network namespace, joined by the `trading-net`
bridge. `moomoo-mcp` publishes the MCP endpoint on `127.0.0.1:8000`; `opend`
publishes nothing at all, so its API — which can place trades once unlocked — is
reachable only from that bridge. Reach it from outside the host by tunnelling to
port 8000, never by publishing 11111.

They share no network namespace deliberately. When they did, restarting `opend`
destroyed the namespace `moomoo-mcp` was living in: its gateway connection was
refused forever and the published port answered nothing, so a gateway restart
took the MCP endpoint down with it. `tests/test_compose_topology.py` asserts the
namespaces stay separate, and `scripts/smoke-test.sh` restarts both containers
and checks a client survives each.

## Where state lives

| State | Lives in | Survives `restart opend` | Survives `restart moomoo-mcp` | Survives container recreation |
| --- | --- | --- | --- | --- |
| MCP bearer token | `.env` on the host, read as an env var | yes | yes | yes |
| MCP session | nothing — the server keeps none | yes | yes | yes |
| OpenD device authorization, remembered login | `opend-data` volume | yes | yes | **yes** |
| OpenD's live login to Moomoo | OpenD process memory | no — re-logs in, ~30s | yes | no |
| OpenD trade unlock | OpenD process memory | no — comes back locked | yes | no |
| Gateway connections, quote subscriptions | moomoo-mcp process memory | yes — the SDK reconnects and replays | no — reopened on the next request | no |

The only row that is genuinely persistent is the third, and it is the one that
matters: without it every deploy would demand a fresh interactive login with an
SMS code.

### The `opend-data` volume

OpenD itself runs entirely inside its container — binary, process, config, logs.
One directory is the exception:

```yaml
- ${OPEND_DATA_DIR:-opend-data}:/home/opend/.com.moomoo.OpenD
```

That path inside the container is a window onto storage outside it. OpenD writes
its device-authorization and remembered-login files there believing it is an
ordinary directory; the bytes land on the host.

By default it is a *named* volume: identified by the name `opend-data` (Compose
prefixes the project, so `moomoo-api-mcp_opend-data`) with Docker choosing where
to put it. Under rootless Docker that is inside the deploy user's own storage,
not `/var/lib/docker`. Ask rather than guess:

```sh
docker --context rootless volume inspect moomoo-api-mcp_opend-data \
  --format '{{.Mountpoint}}'
```

Setting `OPEND_DATA_DIR` makes it a bind mount at a path you choose instead.

A container **restart** keeps the same container, so even files written outside
the volume would survive it. The volume earns its keep on **recreation** — a new
image, a changed setting, `down` then `up` — which discards the container and
everything written inside it. Every deploy recreates the container, so every
deploy would otherwise lose the device token.

`docker compose down -v` deletes the volume. That is the one command that costs
you an interactive re-login; the runbook says never to add `-v` for this reason.

## What each restart costs

**Restarting `opend`** costs a client nothing. The MCP server keeps running and
its endpoint never stops answering. The SDK reconnects on its own — every six
seconds, for as long as it takes — and on reconnect replays the quote
subscriptions it held, re-asserts the gateway lock in `READ_ONLY` mode, and
replays a REAL deployment's startup unlock if one was performed. It re-resolves
`opend` on each attempt, so it follows the gateway even when the container is
recreated at a different address.

Tool calls issued while the gateway is away fail with a connect timeout rather
than hanging — the SDK's connect wait is bounded at 3s and a health check at 5s
— and `check_health` reports `disconnected` or `degraded` until OpenD answers.
Expect ~30s before it goes green, because OpenD has to log in again.

**Restarting `moomoo-mcp`** costs a client one failed call. The endpoint is
served statelessly: no session id is issued, any a client still holds is ignored
rather than rejected, and each request arrives already initialized. There is no
session for a restart to invalidate, so nothing needs reconfiguring afterwards.

What stateless gives up is state this server does not keep: no resumable event
stream, and no server-initiated notifications outside a request. Logging
notifications emitted during a tool call still ride that call's own response.
This applies to the streamable-HTTP transport; `MCP_TRANSPORT=sse` is unchanged.

## Why the gateway connections belong to the process

Worth knowing when reading logs, and easy to get wrong: the MCP lifespan is not
a process-level startup hook. It runs inside `Server.run()`, which the session
manager calls **once per session** — and once per *request* when the server is
stateless.

So a freshly started `moomoo-mcp` has not dialled OpenD at all and will not
until a client sends its first request. A server nobody has called shows no
gateway activity, and that is correct rather than broken.

Because of that, the services are built once for the process and shared by every
session, instead of being built in the lifespan. Building them per lifespan
opened a fresh pair of OpenD connections for every client, waited the trade
connect timeout each time, and closed them when that client left — and under
stateless HTTP it would have done all of that per tool call.

Sharing has a second effect worth knowing about: there is one gateway behind
those connections and one unlock state on it, so the just-in-time unlock
serializes against a single trade context. Concurrent REAL orders from different
clients queue for the duration of an unlock → order → re-lock cycle rather than
interleaving.

## Credentials

### What is stored, and where

| Secret | Where | Notes |
| --- | --- | --- |
| Moomoo login password | **nowhere** | `MOOMOO_LOGIN_BY_REMEMBER=1` uses OpenD's remembered token instead |
| OpenD device token | `opend-data` volume | written by OpenD, which describes it as encrypted |
| Trade PIN (MD5) | `.env`, passed to the container as an env var | MD5 of six digits is obfuscation, not protection |
| MCP bearer token | `.env`, passed to the container as an env var | checked per request, see below |

Authentication is not session-based and never was: `BearerAuthMiddleware`
compares the `Authorization` header against `MCP_AUTH_TOKEN` on every request,
in constant time. Stateless sessions changed nothing about this.

### What protects them

- The login password is never stored, which is the strongest measure here.
- OpenD runs as an unprivileged user (uid 10001), so its files are not
  root-owned.
- Rootless Docker runs the whole daemon as the deploy user, so the volume lives
  in that user's home and a container escape lands as that user.
- Port 11111 is published nowhere.
- `.env` is mode 0600, git-ignored, and a `gitleaks` pre-commit hook scans for
  secrets heading into a commit.

### What does not

- **Nothing here encrypts the volume.** Its contents are protected by file
  permissions and by whatever OpenD does itself. Anyone who can read the deploy
  user's files, or become that user, can copy the device token.
- **Secrets reach the container as environment variables**, so they are visible
  to anything that can `docker inspect` the container or read
  `/proc/<pid>/environ` as that user. A file-mounted credential would be
  stronger; this is the weakest link in the current setup.
- The VPS disk is not encrypted by anything in this repo.

In short: the security of these credentials rests on the deploy user's account
and the host's filesystem permissions, not on cryptography in this repository.

## Known gaps

Things believed but not proven, and things left undone. Kept here so they are
not rediscovered from scratch.

- **`-api_ip` is unverified against the real OpenD.** The smoke test replaces
  OpenD's entrypoint with a stand-in, so the `-api_ip=${OPEND_API_IP:-0.0.0.0}`
  flag never runs in CI; the unit test asserts the flag's text, not that the
  gateway honours it. If OpenD ignored it, the MCP server could not reach
  `opend:11111` on a real deployment. Worth confirming on the next deploy that
  `check_health` reaches `connected`.
- **Whether OpenD's unlock is gateway-wide or per-connection is unconfirmed.**
  The SDK points at gateway-wide — the unlock request carries no connection
  scoping, and OpenD can answer a later unlock with "already unlocked" — but
  this was never checked against a live gateway. It decides whether sharing one
  trade context fixed a real race or merely tidied one away.
- **REAL-mode startup unlock can be skipped.** It only runs if the trade
  connection is established within the startup window, and OpenD needs ~30s to
  log in, so a cold start routinely misses it. Impact is limited because each
  REAL order performs its own just-in-time unlock. Fixing it needs the trade PIN
  available to CI as a repository secret.
- **`moomoo-mcp` takes ~10s to stop** in CI, which looks like the stop grace
  period expiring into a SIGKILL. Not reproduced outside the container: the
  process exits in under a second as an ordinary process, as PID 1 of its own
  namespace, and with an abandoned SSE stream open. No root cause yet. It costs
  correctness nothing and makes every restart slower than it should be.
