# Design: One Container, Two Supervised Processes

## Context

Three arrangements of these two programs have now been considered. They are
easy to confuse, so they are named here once:

| Arrangement | Networking | Status |
| --- | --- | --- |
| Shared namespace | `moomoo-mcp` ran with `network_mode: service:opend` | Abandoned. Restarting OpenD destroyed the namespace `moomoo-mcp` lived in; its published port answered nothing and its gateway connection was refused forever. |
| Separate containers on a bridge | Two namespaces, joined by `trading-net`, MCP dials `opend:11111` | **Deployed today.** The restart failure is gone and asserted against. |
| One container, two processes | One namespace, one process boundary, MCP dials `127.0.0.1:11111` | Proposed here. |

The third is being proposed against the second, not against the first. The
lifecycle argument that motivated moving off the first does not distinguish the
second from the third, because the second already solved it. What distinguishes
them is exposure of an unauthenticated listener, which the second widens and the
third closes.

## Goals / Non-Goals

**Goals**

- OpenD's API reachable only from inside its own container.
- No dependence on `-api_ip`, a flag never verified against a real OpenD build.
- Keep the deployed property that an OpenD restart costs a live client nothing.
- One deployable unit: one image, one service, one restart policy.

**Non-Goals**

- Reconciling account, position or order state after a restart. A restarted
  stack still has to establish what happened while it was gone before unattended
  execution resumes, and this change does not do that.
- Making a retry of an uncertain order safe. It is not, before or after.
- Independent upgrade of gateway and server. This change gives that up
  deliberately; see the trade-off below.

## Decision 1: supervision policy

This is the decision the change turns on, and it is where this design departs
from the obvious one.

The simple policy — *if either required process exits, stop the other and exit
the container, letting Docker restart the unit* — is easy to reason about and
easy to get right. It is also a regression here. `docs/state-and-restarts.md`
records the current cost of an OpenD restart to a connected client: nothing. The
MCP endpoint keeps answering, the SDK reconnects every six seconds, replays its
quote subscriptions, re-asserts the gateway lock, and `check_health` reports
`degraded` in the meantime. `scripts/smoke-test.sh` restarts the gateway against
a live MCP session and asserts the client survives. Under the simple policy,
every OpenD crash instead takes the endpoint away for the ~30s OpenD needs to
log back in, plus process start. The thing the current topology exists to
provide would be traded away as a side effect of packaging.

So the policy is asymmetric:

| Event | Behaviour |
| --- | --- |
| OpenD exits | Restart OpenD in place, MCP untouched. Bounded: N attempts within a window, with backoff. |
| OpenD keeps failing past the bound | Stop MCP cleanly, exit non-zero. Docker replaces the container. |
| MCP exits | Stop OpenD cleanly, exit non-zero. Docker replaces the container. |
| Gateway up, broker connection unavailable | Nothing. Report `degraded`, let the SDK reconnect. |
| SIGTERM/SIGINT | Forward to both, wait bounded, escalate to SIGKILL, exit. |
| Container starts | Both start; OpenD first, but MCP does not wait on it. |

MCP is not restarted in place because there is nothing to preserve by doing so:
the server is stateless over HTTP, holds no session a restart could protect, and
does not dial the gateway until a request arrives. Replacing the whole container
is the simpler path and costs a client one failed call, which is what a
`moomoo-mcp` restart costs today.

The last row matters for a subtlety: MCP's lifespan runs per request, not at
process start. A freshly started container has not dialled OpenD and will not
until a client calls it. The supervisor must therefore never infer MCP health
from gateway activity, and must not gate MCP's start on OpenD being reachable.

**`init: true` does not implement any of this.** It reaps zombies. The paired
policy, the signal forwarding and the bounded OpenD restart are the supervisor's
job, and the supervisor is a program that has to be tested — not a shell line
that backgrounds one process and execs the other. That shape is what makes a
dead OpenD invisible to Docker's restart policy in the first place.

**Alternative considered — the symmetric policy.** Rejected for the reason
above: it converts a routine gateway restart into a client-visible outage. It
stays a reasonable first cut for a deployment that never had the asymmetric
property to begin with; this one has it, has documented it, and tests it.

## Decision 2: supervisor implementation

A small Python module in the application package (`moomoo_mcp.supervisor`, run
as the image's `CMD`), not `s6-overlay`, `supervisord` or a shell script.

- It is testable in the existing suite, with the existing runner, against fake
  child processes. `s6` and `supervisord` would each be a new runtime dependency
  configured in a language the repo does not otherwise use, and the policy above
  is the part that needs testing — not process plumbing in general.
- The policy is maybe a hundred lines: spawn, `waitpid`, backoff, signal
  forwarding, bounded shutdown. Both general-purpose supervisors would need
  configuration of comparable length to express the asymmetry.
- It is PID 1, so it must reap orphans and must not rely on default signal
  dispositions. `init: true` can stay as a belt-and-braces reaper; it does not
  replace the module.

Risk: PID 1 semantics are easy to get subtly wrong, and the failure mode is a
container that will not stop. Mitigated by testing the policy directly, and by
`tasks.md` requiring an observed bounded stop rather than an assumed one. Note
that `moomoo-mcp` already takes ~10s to stop in CI for reasons nobody has root
caused; this change will be measured against that, not credited with it.

## Decision 3: base image and interpreter

`ubuntu:22.04`, with uv downloading a standalone interpreter.

OpenD ships as an Ubuntu 18.04 build with its own shared libraries and needs
`LD_LIBRARY_PATH=/opt/moomooOpenD`; it is the component with real opinions about
the base. Python is the portable one. Building on `python:3.12-slim` (Debian
bookworm) to inherit the interpreter would put OpenD's glibc and libssl
expectations on an untested distribution to save a download.

This flips `UV_PYTHON_DOWNLOADS` from `never` to `automatic`, which is a real
loss: the build stops being hermetic with respect to the interpreter. The
alternative, Ubuntu 22.04's system Python 3.10, satisfies `requires-python
>=3.10` but is older than the 3.12 the current image runs and older still than
the 3.14 `openspec/project.md` describes. A build-stage download pinned by
version keeps the resolved interpreter explicit; `uv.lock` continues to pin
everything above it.

The OpenD download, checksum verification and version pins move across
unchanged. They are the one part of `Dockerfile.opend` that must not be
paraphrased in the merge.

## Decision 4: identity and the data volume

Both processes run as a single unprivileged uid, and it must be OpenD's
existing one.

`opend-data` is the only genuinely persistent state in the stack, and losing it
costs an interactive login with an SMS code. Its files were written by uid 10001
at `/home/opend/.com.moomoo.OpenD`. Keeping the uid, the path and the mount
identical means the existing volume is read as-is; changing any of them risks
OpenD starting against what looks like an empty directory and asking for device
authorization again.

Both current images already use uid 10001 (`opend` and `appuser` respectively),
so a single uid 10001 named `opend` with home `/home/opend` preserves ownership
without renumbering. The MCP application code is owned by it and read-only to
it; nothing in the MCP server needs to write outside `/tmp`.

Requiring one uid is a genuine reduction in separation: a compromise of the MCP
process can now read OpenD's device token directly rather than needing a
container escape first. Against that, the token was already readable by anyone
who could become the deploy user, and the MCP process could already drive the
gateway through the unauthenticated API. The exposure this closes — an
unauthenticated trading API on a shared bridge — is the larger one.

## Trade-offs

| Given up | Gained |
| --- | --- |
| Independent deployment: an MCP image update restarts OpenD, costing a fresh broker login | One unit to build, ship, start and reason about |
| Container-level filesystem and resource boundaries between the two programs | The gateway API leaves every network it does not need |
| A hermetic interpreter (`UV_PYTHON_DOWNLOADS=never`) | OpenD keeps a base it is known to run on |
| Compose doing the process management | A policy that can be stated and tested, instead of one that is implied |

The first row has a cost this repo can quantify: every MCP deploy now pays
OpenD's ~30s re-login. That is the price of the change and it should be stated
in the runbook, not discovered.

## What this change does not make safe

Restated because packaging invites the opposite assumption: a container that has
just restarted has a gateway that is logged in, locked, and knows nothing about
what the previous process was doing. Orders placed before the restart may have
filled, partially filled, or not left the building. Establishing that before
unattended execution resumes is separate work, and the supervision policy above
neither performs it nor excuses skipping it.
