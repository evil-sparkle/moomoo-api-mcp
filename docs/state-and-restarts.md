# State, restarts and credentials

What state this stack holds, where each piece of it lives, what survives which
restart, and who can read it. Read `deploy-vps.md` for how to run the thing;
this is for reasoning about it when something restarts or when you are deciding
how exposed a credential is.

## The shape of it

```
                        ┌──── one container ─────┐
your client  ──HTTP──▶  │ supervisor             │
             127.0.0.1  │   ├── MCP server       │
                  :8000 │   │      │ 127.0.0.1   │
                        │   │      ▼      :11111 │
                        │   └── OpenD  ──────────┼──▶  Moomoo's servers
                        └────────────────────────┘
```

One container running two processes, with `moomoo_mcp/supervisor.py` as its
entry point. The MCP endpoint is published on `127.0.0.1:8000`. The gateway is
published nowhere and listens on container loopback, so its API — which can
place trades once unlocked — is reachable only by the server sharing its
container. Reach the stack from outside the host by tunnelling to port 8000,
never by publishing 11111.

They share a container deliberately. While they were two containers on the
`trading-net` bridge, the gateway had to answer on `0.0.0.0:11111` for the
server to reach it across the namespace boundary, and OpenD's API has no
authentication of its own — so the only thing between it and anything else on
that bridge was that nothing else had been attached yet. The process boundary
does that job now, and the `-api_ip` flag is no longer something a deployment
can widen.

An older arrangement still worth knowing about, because two files carry
assertions against it: `moomoo-mcp` once ran *inside* OpenD's network namespace
(`network_mode: service:opend`). Restarting the gateway destroyed the namespace
the server was living in — its connection was refused forever and the published
port answered nothing. `tests/test_compose_topology.py` reads the compose file
and `scripts/smoke-test.sh` runs it; between them they cover both that failure
and the exposure above.

## Where state lives

| State | Lives in | Survives a gateway process restart | Survives a container restart | Survives container recreation |
| --- | --- | --- | --- | --- |
| MCP bearer token | `.env` on the host, read as an env var | yes | yes | yes |
| MCP session | nothing — the server keeps none | yes | yes | yes |
| OpenD device authorization, remembered login | `opend-data` volume | yes | yes | **yes** |
| OpenD's live login to Moomoo | OpenD process memory | no — re-logs in, ~30s | no | no |
| OpenD trade unlock | OpenD process memory | no — comes back locked | no | no |
| Gateway connections, quote subscriptions | MCP server process memory | yes — the SDK reconnects and replays | no — reopened on the next request | no |

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

The path and the owning uid are load-bearing, not incidental. OpenD wrote those
files as uid 10001 at `/home/opend/.com.moomoo.OpenD`, and the merged image
keeps both so an existing volume is read rather than treated as an empty
directory — which would mean a fresh device authorization over SMS.

`docker compose down -v` deletes the volume. That is the one command that costs
you an interactive re-login; the runbook says never to add `-v` for this reason.

## What each restart costs

**A gateway process dying** does not require a client to reconnect or
re-initialize. That is the precise claim, and it is worth being precise: the
supervisor restarts OpenD in place and the endpoint never stops answering, so
`initialize` stays valid, `tools/list` works and `check_health` answers —
`degraded`. What does not work, for the ~30s OpenD takes to log back in, is
anything that has to reach the gateway: quotes, positions, orders. Those fail
with a bounded connect timeout rather than hanging. The SDK reconnects on its own — every six seconds, for as long as it
takes — and on reconnect replays the quote subscriptions it held, re-asserts the
gateway lock in `READ_ONLY` mode, and replays a REAL deployment's startup unlock
if one was performed. The address never moves now, because it is loopback.

A gateway that cannot be started at all — no account configured, no remembered
token — is treated the same way rather than as a fatal error: the supervisor
says why, starts the server anyway, and `check_health` reports the gateway
unavailable. A container that exited here would crash-loop and take the health
endpoint with it, leaving nothing to ask. A malformed *supervision setting* is
the opposite and does stop the container: it is never an expected state, and it
decides how the policy behaves.

That asymmetry is a policy, not a property of the packaging: restarts are
bounded (`OPEND_MAX_RESTARTS` within `OPEND_RESTART_WINDOW_SECONDS`, with
backoff), and a gateway that keeps failing past that bound stops the server and
exits the container for Docker to replace whole. A gateway that is *running* but
cannot reach Moomoo is not a failure at all — that is the `degraded` that
`check_health` reports, and nothing restarts on it.

Tool calls issued while the gateway is away fail with a connect timeout rather
than hanging — the SDK's connect wait is bounded at 3s and a health check at 5s
— and `check_health` reports `disconnected` or `degraded` until OpenD answers.
Expect ~30s before it goes green, because OpenD has to log in again.

**Restarting the container** costs a client one failed call, plus the ~30s
OpenD needs to log in again before health goes green. The endpoint itself is
served statelessly: no session id is issued, any a client still holds is ignored
rather than rejected, and each request arrives already initialized. There is no
session for a restart to invalidate, so nothing needs reconfiguring afterwards.

**An MCP server process dying** is the same thing: the supervisor stops the
gateway and exits non-zero, and `restart: unless-stopped` replaces the
container. It is deliberately not restarted in place — a stateless server holds
nothing that surviving in place would preserve, and a container that replaces
both processes is the simpler thing to reason about.

**The cost of packaging them together** shows up here: an MCP image update now
restarts OpenD too, so *every deploy* pays that ~30s broker re-login. Two
containers could be upgraded independently; one cannot. That is the price of
taking the gateway's API off the network, and it is worth knowing before a
deploy rather than during one.

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
- The supervisor redacts `-login_pwd_md5` and `-login_account` from the command
  line it logs, so the trade PIN hash does not end up in `docker logs` or
  anything shipping them. `scripts/smoke-test.sh` asserts it stays that way.

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

- **The merged image has never run the real OpenD.** CI stands the binary in
  with a stub, so what has been proven is the supervision policy and the
  networking, not that an Ubuntu 18.04 build of OpenD starts under this image's
  libraries. It is the same base the previous OpenD image used, which is why it
  was chosen, but "same base" is an argument, not a test. Confirm on the next
  deploy that OpenD reports login and `check_health` reaches `connected`.
- **`-api_ip` is still unverified against the real OpenD**, but it now fails
  safe. It is pinned to `127.0.0.1`, which is also OpenD's documented default,
  so the gateway ends up on loopback whether the flag is honoured or ignored —
  where the old `0.0.0.0` depended on the flag working to be reachable at all.
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
- **The MCP server takes ~10s to stop** in CI, which looks like the stop grace
  period expiring into a SIGKILL. Not reproduced outside the container: the
  process exits in under a second as an ordinary process, as PID 1 of its own
  namespace, and with an abandoned SSE stream open. No root cause yet. It costs
  correctness nothing and makes every restart slower than it should be.

  The supervisor now sits between it and Docker, which changes who waits for
  whom but not the underlying cause: `SUPERVISOR_STOP_TIMEOUT_SECONDS` bounds
  how long the supervisor waits before SIGKILL, and Docker's own grace period
  bounds the supervisor. Whether the merged container stops faster, slower or
  the same has not been measured — do that on the next deploy rather than
  assuming this change fixed or worsened it.
