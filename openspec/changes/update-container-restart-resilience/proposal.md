# Change: Reconcile Specs With Container Restart Resilience

## Why

Four merged pull requests (#1, #2, #4, #5) made a restart of either container
invisible to a connected MCP client. Each was handled as a bug fix, which
`openspec/AGENTS.md` exempts from the proposal flow — correct per change, but
the four together added up to a capability the specs never learned about.
Searching every capability for `reconnect|stateless|session|restart` finds
nothing describing it.

Two existing requirements are also out of step with the code:

1. `container-deployment` demands the build verify OpenD against
   `d38aad772b296f922e3b270119ca1abbadadc61cd3a45826e1b087a5b79069a5`, but
   `Dockerfile.opend` pins `72eaa6e47b5cb8905306427b5e3679d591408243492e3e7acbc3a7d46f09a0aa`.
   The literal was written into both places by
   `2026-09-11-add-security-hardening`, and the Dockerfile moved on without the
   spec. A spec that mandates a checksum the build does not use is worse than
   silence: it reads as authoritative and is wrong.
2. `trade-unlock` says a READ_ONLY deployment locks the gateway "when the MCP
   server initializes its trade connection." True, but the server now also
   re-asserts that lock on every SDK reconnect — the part that survives an
   OpenD restart. As written, half an implementation satisfies the spec.

This change is documentation only. The behaviour already ships; the specs are
catching up.

## What Changes

- **`container-deployment`**
  - **MODIFIED** Binary Download Integrity Verification: verify against the
    checksum pinned in `Dockerfile.opend` rather than a literal duplicated into
    the spec. Duplication is what rotted; a single source of truth cannot.
  - **ADDED** Client-Transparent Container Restarts: restarting either
    container costs a client at most one failed call, never a reconnection —
    including when the gateway is recreated at a different address.
- **`trade-unlock`**
  - **MODIFIED** Proactive Lock on Read-Only Startup → on every connection,
    covering reconnections and connections established after startup.
  - **ADDED** Serialized Unlock Windows: at most one just-in-time unlock window
    is open at a time.
- **`mcp-transport`** (new capability)
  - **ADDED** Stateless Streamable HTTP Sessions.
  - **ADDED** Process-Scoped Gateway Connections.

No requirement here asks for code that does not exist. Each was written from
the merged implementation and its tests.

## Impact

- Affected specs: `container-deployment`, `trade-unlock`, `mcp-transport` (new).
- Affected code: none. Every requirement is already implemented in
  `src/moomoo_mcp/server.py`, `src/moomoo_mcp/services/trade_service.py`,
  `docker-compose.yml` and `Dockerfile.opend`, and covered by
  `tests/test_compose_topology.py`, `tests/test_server.py`,
  `tests/test_services/test_trade_service.py` and `scripts/smoke-test.sh`.
- Two requirements rest on claims this repository has not verified against a
  live gateway. Both are called out in `design.md` and in
  `docs/state-and-restarts.md` rather than being asserted quietly.
