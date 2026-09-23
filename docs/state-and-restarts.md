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
| Paper execution journal and recovery audit | Optional `execution-data` volume | yes | yes; fresh admission epoch and recovery review | yes; preserve the same volume |
| OpenD device authorization, remembered login | `opend-data` volume | yes | yes | **yes** |
| OpenD's live login to Moomoo | OpenD process memory | no — re-logs in, ~30s | no | no |
| OpenD trade unlock | OpenD process memory | no — comes back locked | no | no |
| Execution halt (`ARMED`/`HALTED`) | MCP server process memory | yes | **no — a new process starts `ARMED`** | no |
| Gateway connections, quote subscriptions | MCP server process memory | yes — the SDK reconnects and replays | no — reopened on the next request | no |

The OpenD authorization volume prevents every deploy from demanding another
interactive login with an SMS code. The separate paper journal preserves execution
identity, outcomes and recovery obligations. Both SIMULATE and REAL deployments
use that same paper journal; READ_ONLY does not open it. Real-order journaling is
not implemented in Stage 2. See [persistent paper execution](paper-execution.md)
for the optional overlay, initialization, consistent backups and recovery rules.
Never replace or reset a journal to clear an unresolved execution.

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
takes — and on reconnect replays the quote subscriptions it held and re-asserts
the gateway lock at rest. The address never moves now, because it is loopback.

One exception: if a write is in flight and holding the gateway deliberately
unlocked, the reconnect skips its lock request. Locking underneath a write
would make it fail on a locked gateway; the write's own re-lock covers that
window instead.

## The unlock lifecycle, and the execution halt

There is one unlock path, and it lasts for one order.

In `REAL` mode with a stored trade credential (`MOOMOO_TRADE_PASSWORD` or
`MOOMOO_TRADE_PASSWORD_MD5`), the server keeps the gateway **locked at rest**:
it issues a lock when it connects and after every reconnect, unlocks
immediately before dispatching a single order, and re-locks immediately after.
`READ_ONLY` locks at rest too, and never unlocks at all. `SIMULATE`, and `REAL`
without a stored credential, leave the gateway alone — the latter because a
server that cannot unlock again would strand an operator who unlocked by hand.

There is no startup unlock. There used to be, and it was a second unlock path
that the SDK then replayed after every reconnect, so the gateway stayed
unlocked for the life of the process — exactly what the lock guards against.

**The halt.** If the re-lock after an order fails, the gateway may still be
unlocked and nothing has confirmed otherwise. The service moves to `HALTED`:

| From | Event | To |
| --- | --- | --- |
| `ARMED` | a just-in-time re-lock fails | `HALTED`, recording the time and the lock error |
| `HALTED` | a just-in-time re-lock fails again | `HALTED`, keeping the original start time |
| `HALTED` | `lock_trade` fails | `HALTED`, keeping the original start time |
| `HALTED` | `lock_trade` succeeds | `ARMED` |
| `ARMED` | `lock_trade` succeeds or fails | `ARMED` |

While `HALTED`, REAL placements, combo placements and `NORMAL`/`ENABLE`
modifications are refused before any gateway request. Cancellations, and
`CANCEL`/`DISABLE`/`DELETE` modifications, stay allowed: an operator facing a
halt still has to be able to reduce exposure.

**Only `lock_trade` clears it.** Two things that look like recovery are not:

- a just-in-time re-lock that succeeds later only undoes an unlock the server
  made a moment earlier, and says nothing about why the earlier one failed;
- a lock after a reconnect happens without anyone seeing the halt at all.

Requiring an explicit lock-only call makes recovery a visible, logged act. A
`lock_trade` the gateway refuses leaves the halt exactly as it was and reports
`halt_cleared: false` — it never looks like a recovery it did not achieve.

An acknowledged order whose re-lock fails still returns its receipt, alongside
`gateway_relock_error` and `execution_halted: true`. Losing an order identifier
because the lock afterwards did not take would be strictly worse than reporting
both facts.

**It does not survive a restart.** The state is process memory: a new process
starts `ARMED` and locks the gateway at rest when it connects. This is not an
operator pause, and nothing here anticipates one.

`check_health` reports `execution_halted`, and when halted also `halted_since`
and `halt_error`. It reads memory — no gateway request — and reading it never
clears the halt. The halt does not change the connectivity `status`: a halted
server whose gateway is perfectly reachable is still connected, and collapsing
the two would hide which of the two problems the deployment has.

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
stream, and no server-initiated notifications outside a request. Responses are
also plain JSON (`json_response=True`, for clients that only read JSON), so
logging notifications a tool emits during a call are dropped rather than
delivered on that call's response.
This applies to the Streamable HTTP transport when configured statelessly; `MCP_TRANSPORT=sse` is unchanged.

## Why the gateway connections belong to the process

Worth knowing when reading logs, and easy to get wrong: the MCP lifespan is not
a process-level startup hook. It runs inside `Server.run()`, which the session
manager calls **once per session** — and once per *request* when the server is
stateless.

So a freshly started `moomoo-mcp` has not dialled OpenD at all and will not
until a client sends its first request. A server nobody has called shows no
gateway activity, and that is correct rather than broken.

Because of that, the services are built once for the process and shared across all
requests, instead of being built in the lifespan. Building them per lifespan
opened a fresh pair of OpenD connections for every client, waited the trade
connect timeout each time, and closed them when that client left — and under
stateless Streamable HTTP it would have done all of that per tool call.

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
in constant time. Stateless mode changed nothing about this.

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

- **Crash recovery is proven against CI's stub only.** The real OpenD has now
  run in this image on the VPS (2026-09-18): it logs in with the remembered
  token from the existing volume, reports `API Listening Address:
  127.0.0.1:11111`, and `check_health` reaches `connected` with quote and trade
  both `ok`. Operator stops and restarts were exercised there too. What has not
  been done live is killing a process: the real OpenD, to watch the supervisor
  restart it in place, or the MCP server, to watch the container restart.
- **Whether OpenD honours `-api_ip` or merely defaults to loopback is still
  unknown**, and no longer matters: the real gateway reports that it listens
  on `127.0.0.1:11111`, which is the property the deployment depends on.
- **Whether OpenD's unlock is gateway-wide or per-connection is unconfirmed.**
  The SDK points at gateway-wide — the unlock request carries no connection
  scoping, and OpenD can answer a later unlock with "already unlocked" — but
  this was never checked against a live gateway. It decides whether sharing one
  trade context fixed a real race or merely tidied one away.
- **Whether `lock_trade` can succeed on this gateway is unverified.** The SDK
  resolves a REAL account before issuing a lock, not only before an unlock, so
  a gateway that cannot resolve one produces a failing lock — and `lock_trade`
  is the only exit from `HALTED`. Confirmed in the SDK source; not yet checked
  against the live gateway. See
  `openspec/changes/harden-trading-safeguards/verification.md`.
- **The old ~10s stop was never explained.** The two-container MCP server took
  ~10s to stop in CI, which looked like the stop grace period expiring into a
  SIGKILL, and it was not reproduced outside the container. Under the
  supervisor it does not happen on the VPS: `compose stop` takes about a second
  with OpenD logged in, exit code 0, and nothing needs SIGKILL (measured
  2026-09-18). It has not been re-measured in CI, and the original cause is
  still unknown. If it comes back, note that `SUPERVISOR_STOP_TIMEOUT_SECONDS`
  and Docker's stop grace period are both 10s by default, so a child that
  ignores SIGTERM for that long gets the supervisor itself killed (exit 137)
  before its own SIGKILL escalation runs.
