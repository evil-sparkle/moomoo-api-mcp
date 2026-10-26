## 1. Review (this change)

- [x] 1.1 Review each requirement against the code it cites in `design.md` §
      Evidence, and confirm it describes behaviour on `main` rather than a new
      promise. Reviewed 2026-09-19 against `main` at `5fc1b57`. Three
      corrections, all to match the code:
      - Notifications: the draft said they ride the call's response. Since
        `dd066e7` (`json_response=True`) the SDK drops them. Now specified as
        dropped, by decision; the README, [State and restart reference](../../../../docs/deploy-vps.md#state-and-restart-reference) and the
        `server.py` comment said the same wrong thing and are fixed too.
      - Authentication: now conditional on `MCP_AUTH_TOKEN`, as the middleware is.
      - Process-owned connections open on the first request served, not "the
        first request that needs them": the lifespan runs for every request.
- [x] 1.2 Confirm every **gap** in the evidence tables is acceptable to leave
      specified-but-unproven, or promote it to § 3 before approval. Accepted
      2026-09-19: the testable gaps are already § 3.1-3.4; the rest (killed
      processes against the real OpenD, REAL-mode paths, a lost order
      response) stay listed in `design.md`.
- [x] 1.3 Decide the three open questions in `design.md`, or accept them as
      tracked follow-ups. Accepted as follow-ups 2026-09-19: § 3.10-3.12.
- [x] 1.4 Confirm no conflict with `refactor-single-container-deployment`,
      in names or in behaviour. This change must not edit `Isolated OpenD
      Gateway Network`, `Session State Persistence`, `Non-Root Container
      Execution` or `Paired Process Supervision`. It must also not promise
      anything those requirements contradict. The one contradiction found so
      far is already reconciled: "MCP is never restarted by a gateway restart"
      against "an exhausted retry budget restarts the container". See
      `design.md` § Decisions. Rechecked 2026-09-19 against the canonical
      spec after that change was archived: no further conflict.
- [x] 1.5 Confirm the evidence tables in `design.md` cite the single-container
      code and tests on `main`, not the two-container baseline they were first
      drafted against. Done in #15 and extended with live results in #17.

## 2. Apply (after approval; documentation only)

- [x] 2.1 `openspec validate update-container-restart-resilience --strict
      --no-interactive` passes. It passes today with the pinned CLI
      (`@fission-ai/openspec` 1.13.1); CI's `openspec` job runs the `--all`
      form on every push.
- [x] 2.2 Archive `refactor-single-container-deployment` first: its
      `Paired Process Supervision` requirement is the one this change's
      gateway-restart requirement defers to. Done on 2026-09-19, after its
      verification items (2.6, 6.2, 6.3) passed on the live deployment:
      `changes/archive/2026-09-19-refactor-single-container-deployment`.
- [x] 2.3 Archive with `openspec archive update-container-restart-resilience
      --yes`, in a separate PR, updating `container-deployment`,
      `trade-unlock` and creating `transport-sessions`.
- [x] 2.4 `openspec validate --all --strict --no-interactive` passes after the
      archive.

No code, configuration or deployment step belongs in this change.

## 3. Follow-up work (separate changes; not part of this proposal)

Found while drafting. Each is either a runtime change, which this
documentation-only change must not make, or a test that turns a **gap** or
**manual** item in `design.md` into automated evidence.

- [x] 3.1 Test: pin `stateless_http=True` and `json_response=True`, and assert
      over HTTP that a foreign `mcp-session-id` is processed rather than
      rejected and that a call is answered with one JSON response. Pinned and
      asserted in `TestStatelessStreamableHTTP` in `tests/test_server.py`.
- [x] 3.2 Test: after the MCP process is killed and the container restarts,
      the smoke test waits for the new MCP server to reach the new gateway, as
      it already does after a gateway-only restart. Also assert that
      `initialize` returns no `mcp-session-id`. Today the smoke test accepts
      either.
- [x] 3.3 Test: a REAL-mode startup whose trade connection is not ready skips
      auto-unlock. The nearest existing test runs in READ_ONLY. Pinned and
      asserted in `TestStartupTradingMode.test_real_mode_unready_connection_skips_auto_unlock`
      in `tests/test_server.py`.
- [x] 3.4 Test: the initial-connect path logs a refused READ_ONLY lock and
      keeps the connection. Only the reconnect path is tested. Pinned and
      asserted in `TestReadOnlyGatewayLock.test_a_refused_lock_on_initial_connect_is_logged_and_keeps_the_connection`
      in `tests/test_services/test_trade_service.py`.
- [x] 3.5 Runtime: order-command errors do not distinguish "outcome unknown"
      (transport lost) from "rejected by the broker". Consider a distinct error
      that tells the caller to query orders before retrying.
- [x] 3.6 Stale wording outside the specs: the `unlock_trade` tool docstring
      (`tools/account.py:358`) says the unlock is "maintained for the session".
      The rewritten compose tests and smoke test for the single container no
      longer say sessions live in server memory. Updated to state that unlock
      state is maintained on the OpenD gateway.
- [x] 3.7 `.env.example` lists `OPEND_VERSION`, `OPEND_TAG`, `OPEND_URL` and
      `OPEND_SHA256` as optional overrides, but no Compose file forwards them as
      build args, so setting them has no effect. Either forward them or remove
      the block. If forwarded, a host `.env` could override the reviewed pin,
      which the integrity requirement would then need to address.
- [x] 3.8 Process: add a PR-description line stating whether the change affects
      a documented behaviour or architectural contract. The bug-fix exemption
      in `openspec/AGENTS.md` skips the proposal, not the spec-impact check, and
      `openspec validate --strict` checks structure only, so it cannot catch a
      stale spec. The stale checksum passed it.
- [x] 3.9 Runtime, if wanted: notifications. With `json_response=True` a
      tool's `ctx.info` / `ctx.warning` never reaches an HTTP client. Options:
      move anything a caller needs into tool results (it may already be
      there), or make JSON responses a setting so SSE-capable clients get
      notifications again. The spec would then need a MODIFIED requirement.
- [x] 3.10 Decide: should REAL mode hold a standing startup unlock at all?
      (`design.md` § Open Questions.)
- [x] 3.11 Decide: should a refused READ_ONLY lock surface in `check_health`
      rather than the log only?
- 3.12 **Deferred, non-blocking (approved 2026-10-26):** establish live
      cross-connection unlock scope. Accept the documented OpenD-wide behavior
      for the current design; the live scope experiment has not passed.


### Follow-up reconciliation — 2026-10-25

The descriptions above retain the original findings. Current disposition:

- 3.2: `scripts/smoke-test.sh` rejects session IDs and now waits for replacement
  MCP gateway connections after observing a new gateway start. The final isolated
  Docker smoke test passed on 2026-10-25 using the synthetic gateway; this is
  container lifecycle evidence, not live broker acceptance.
- 3.5: superseded by paper/REAL execution journals, structured dispatch outcomes,
  and reconciliation. See `services/paper_execution.py`, `real_execution.py`,
  `order_errors.py` and their tests; unknown outcomes are never blindly replayed.
- 3.7: `docker-compose.yml` forwards all four build arguments. `.env.example`
  documents local-build-only scope and reviewed default pins; production pulls
  prebuilt images. Covered by `tests/test_environment_contract.py`.
- 3.8: `.github/pull_request_template.md` now prompts for documented behavior and
  architectural contract impact, including bug fixes, plus release impact.
- 3.9: decision closed: retain JSON-only responses and dropped notifications.
  `transport-sessions` and `TestStatelessStreamableHTTP` specify/test this behavior;
  callers consume structured results. SSE remains a separate optional feature.
- 3.10: resolved by `trade-unlock` and server startup: no startup unlock; stored
  REAL credentials unlock only just in time for mutations.
- 3.11: health now reports `gateway_lock_at_rest` as the latest connect/reconnect
  attempt and completion time, separately from connectivity and execution halt.
  It does not claim current lock state or expose raw provider messages.
- 3.12: deferred, non-blocking; live cross-connection scope remains unverified. Official documentation
  confirms the documented scope is OpenD-wide. After the user supplied the trade
  credential locally, an isolated hotfix gateway successfully locked, unlocked,
  and re-locked through two distinct SDK connections, with zero order mutations.
  Neither unlock response distinguished an already-unlocked gateway, so this
  verifies credential-based unlock/relock but does not independently prove scope.
  The temporary gateway was stopped and the original paper container restored.
  See `finish-restart-resilience-followups/unlock-verification.md` for the separate
  documentary, build, and runtime evidence. Do not infer scope from mock results
  or from two successful unlock calls alone.
