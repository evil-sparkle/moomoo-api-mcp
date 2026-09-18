## 1. Review (this change)

- [ ] 1.1 Review each requirement against the code it cites in `design.md` §
      Evidence, and confirm it describes behaviour on `main` rather than a new
      promise.
- [ ] 1.2 Confirm every **gap** in the evidence tables is acceptable to leave
      specified-but-unproven, or promote it to § 3 before approval.
- [ ] 1.3 Decide the three open questions in `design.md`, or accept them as
      tracked follow-ups.
- [ ] 1.4 Confirm no overlap with `refactor-single-container-deployment`: this
      change must not touch `Isolated OpenD Gateway Network`, `Session State
      Persistence`, `Non-Root Container Execution` or `Paired Process
      Supervision`.

## 2. Apply (after approval; documentation only)

- [ ] 2.1 `openspec validate update-container-restart-resilience --strict
      --no-interactive` passes.
- [ ] 2.2 Archive with `openspec archive update-container-restart-resilience
      --yes`, in a separate PR, updating `container-deployment`,
      `trade-unlock` and creating `transport-sessions`.
- [ ] 2.3 `openspec validate --all --strict --no-interactive` passes after the
      archive.

No code, configuration or deployment step belongs in this change.

## 3. Follow-up work (separate changes; not part of this proposal)

Found while drafting. Each is either a runtime change, which this
documentation-only change must not make, or a test that turns a **gap** or
**manual** item in `design.md` into automated evidence.

- [ ] 3.1 Test: pin `stateless_http=True`, and assert over HTTP that a foreign
      `mcp-session-id` is processed rather than rejected. Today this rests on
      one manual measurement.
- [ ] 3.2 Test: after `restart moomoo-mcp`, the smoke test waits for the MCP
      server to reconnect to the gateway, as it already does after `restart
      opend`.
- [ ] 3.3 Test: a REAL-mode startup whose trade connection is not ready skips
      auto-unlock. The nearest existing test runs in READ_ONLY.
- [ ] 3.4 Test: the initial-connect path logs a refused READ_ONLY lock and
      keeps the connection. Only the reconnect path is tested.
- [ ] 3.5 Runtime: order-command errors do not distinguish "outcome unknown"
      (transport lost) from "rejected by the broker". Consider a distinct error
      that tells the caller to query orders before retrying.
- [ ] 3.6 Stale wording outside the specs:
      `tests/test_compose_topology.py:158-165` and `scripts/smoke-test.sh:139-141`
      still say MCP sessions live in server memory, and the `unlock_trade` tool
      docstring (`tools/account.py:357-359`) says the unlock is "maintained for
      the session".
- [ ] 3.7 `.env.example` lists `OPEND_VERSION`, `OPEND_TAG`, `OPEND_URL` and
      `OPEND_SHA256` as optional overrides, but no Compose file forwards them as
      build args, so setting them has no effect. Either forward them or remove
      the block. If forwarded, a host `.env` could override the reviewed pin,
      which the integrity requirement would then need to address.
- [ ] 3.8 Process: add a PR-description line stating whether the change affects
      a documented behaviour or architectural contract. The bug-fix exemption
      in `openspec/AGENTS.md` skips the proposal, not the spec-impact check, and
      `openspec validate --strict` checks structure only, so it cannot catch a
      stale spec. The stale checksum passed it.
