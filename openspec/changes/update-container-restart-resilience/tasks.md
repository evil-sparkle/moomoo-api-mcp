# Tasks: Reconcile Specs With Container Restart Resilience

This change ships no code. Every requirement below was written from an already
merged implementation, so the work is verifying that each one is true and then
archiving the deltas into `openspec/specs/`.

## 1. Verify each requirement against the code that already implements it

- [ ] 1.1 `Binary Download Integrity Verification`: confirm `Dockerfile.opend`
      verifies the download with `sha256sum -c` against a build-time pin, and
      that no other checksum literal remains in `openspec/`
- [ ] 1.2 `Client-Transparent Container Restarts`: confirm `docker-compose.yml`
      gives each service its own network namespace on `trading-net`, publishes
      the MCP port from `moomoo-mcp`, and carries no `depends_on: restart: true`
      — `tests/test_compose_topology.py` asserts all three
- [ ] 1.3 `Stateless Streamable HTTP Sessions`: confirm `FastMCP(...)` is
      constructed with `stateless_http=True` in `src/moomoo_mcp/server.py`
- [ ] 1.4 `Process-Scoped Gateway Connections`: confirm `get_services()` builds
      at most one `AppContext` per process and that `app_lifespan` yields it
      rather than building its own —
      `tests/test_server.py::test_sessions_share_one_set_of_connections`
- [ ] 1.5 `Proactive Lock on Read-Only Startup`: confirm
      `TradeService._watch_reconnects` wraps `on_api_socket_reconnected` and
      that `_enforce_gateway_lock` cannot raise on an SDK thread
- [ ] 1.6 `Serialized Unlock Windows`: confirm `_jit_trade_unlock` holds
      `self._jit_lock` across both the unlock and the re-lock

## 2. Confirm the end-to-end guarantee still holds

- [ ] 2.1 Run `scripts/smoke-test.sh` and confirm the client keeps its endpoint
      across both a gateway restart and an MCP server restart
- [ ] 2.2 Run the unit suite (`uv run pytest`) and the compose topology tests

## 3. Review and archive

- [ ] 3.1 Review the two open questions in `design.md` — neither is asserted as
      fact in the deltas, and both should stay visible until a live gateway
      settles them
- [ ] 3.2 On approval, archive into `openspec/specs/`, creating the
      `mcp-transport` capability and folding the `container-deployment` and
      `trade-unlock` deltas into their existing specs
