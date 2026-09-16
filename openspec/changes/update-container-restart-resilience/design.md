## Context

The stack is a client talking HTTP to `moomoo-mcp`, which talks to `opend`,
which talks to Moomoo. Restarting either container used to cost the person at
the other end something: a gateway restart used to take the MCP endpoint down
with it, and an MCP server restart invalidated every client session. On a
remote VPS both meant hands-on recovery.

Four pull requests fixed that, each scoped as a bug fix:

- **#1** gave the containers separate network namespaces on `trading-net`, and
  made the READ_ONLY gateway lock re-assert on every reconnect.
- **#2** added the container smoke test, and removed a `depends_on: restart:
  true` that was restarting the MCP server on every gateway bounce — undoing
  half of #1.
- **#4** made the streamable-HTTP transport stateless and moved the gateway
  connections from the session to the process.
- **#5** documented the result in `docs/state-and-restarts.md`.

This change writes the resulting contract into the specs. The decisions below
were made during that work; they are recorded here because the reasoning is not
recoverable from the diffs.

## Goals / Non-Goals

- **Goals**: state what a client may rely on across a restart of either
  container; fix a requirement that names a checksum the build does not use;
  describe the session and connection model a reader would otherwise have to
  infer from SDK internals.
- **Non-Goals**: no code change; no new guarantee beyond what ships today; no
  claim about durability of an in-flight call during a restart — that call
  fails, and the spec says so.

## Decisions

**Decision: serve streamable HTTP statelessly rather than persisting sessions.**
MCP sessions live in the server process's memory, so a restart invalidated them
and the client's next call was answered with 404. Stateless mode issues no
session id, ignores any the client still holds, and treats each request as
initialized — there is nothing left for a restart to invalidate.

- *Alternative: persist session state.* The SDK keeps sessions in memory with
  no store to point elsewhere; this would have meant carrying a fork.
- *Alternative: rely on the client.* The MCP spec requires a client receiving
  404 to re-initialize. Well-behaved clients recover on their own, but the ones
  that do not leave a person reconnecting a remote endpoint by hand, which is
  the interruption worth removing.
- *Cost, accepted*: no resumable event stream and no server-initiated
  notifications outside a request. This server keeps neither. Logging
  notifications emitted during a tool call still ride that call's response.
  The SSE transport is unchanged.

**Decision: open gateway connections once per process, not once per session.**
The MCP lifespan runs inside `Server.run()`, which the session manager calls
once per session — and once per *request* when stateless. Building the services
there opened a fresh pair of OpenD connections for every client, waited the
trade connect timeout each time, and closed them when that client left; under
stateless HTTP it would have done all of that per tool call. Stateless was only
affordable once the connections moved to the process.

A visible consequence worth stating rather than leaving as a surprise: a server
no client has called has not dialled OpenD at all, so its logs show no gateway
activity. That is correct, not broken.

**Decision: verify the OpenD binary against the checksum pinned in the
Dockerfile, not a literal repeated in the spec.** The duplicate is what went
stale. Naming `Dockerfile.opend` as the single source keeps the requirement
true across version bumps, and the requirement stays verifiable: a build that
does not check, or checks something else, violates it.

## Open questions

Two claims behind these requirements have not been verified against a live
gateway. Neither is asserted as fact in the deltas.

- **Is OpenD's unlock gateway-wide or per-connection?** The SDK points at
  gateway-wide — the unlock request carries no connection scoping, and OpenD can
  answer a later unlock with "already unlocked" — but it was never confirmed.
  It decides whether sharing one trade context fixed a real race between two
  clients or merely tidied one away. `Serialized Unlock Windows` is therefore
  written as a guarantee about this server's own behaviour, which is true
  either way.
- **Does the real OpenD honour `-api_ip`?** The smoke test replaces OpenD's
  entrypoint with a stand-in, so the flag never runs in CI, and the unit test
  asserts its text rather than its effect. If the gateway ignored it, the MCP
  server could not reach `opend:11111` on a real deployment. Worth confirming
  on the next deploy that `check_health` reaches `connected`.
