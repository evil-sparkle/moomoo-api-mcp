## Context

"Session" means at least four different things in this stack, and each has its
own lifetime. Treating them as one "session persistence" concern is how the
specs drifted: one requirement was written for OpenD's device token, and it was
read as if it covered the MCP transport.

| State | Owner | Lifetime | Survives gateway restart | Survives MCP restart | Spec home |
| --- | --- | --- | --- | --- | --- |
| OpenD device authorization, remembered login | `opend-data` volume | until `down -v` | yes | yes | `container-deployment` › Session State Persistence (being modified by `refactor-single-container-deployment`; untouched here) |
| OpenD live broker login | OpenD process | OpenD process | no, re-logs in (~30s) | yes | none; observed only |
| OpenD unlock state | OpenD process, plus the SDK's cached copy in the MCP process | see § Reconnect replay | no, returns locked unless the SDK replays an unlock | only if the unlock is gateway-wide (unconfirmed, see Open Questions) | `trade-unlock` |
| MCP transport session | nobody: stateless | none | n/a | n/a | `transport-sessions` (new) |
| Gateway connections, quote subscriptions | MCP process | MCP process | yes, the SDK reconnects and replays them | no, reopened on the next request | `transport-sessions` (new) |
| Durable trading state (order intent, outcome reconciliation) | does not exist | — | — | — | out of scope; see Non-Goals |

The last row is listed so its absence is on record. Nothing in the stack
remembers which trading commands were issued, so nothing can reconcile one
whose response was lost.

## Goals / Non-Goals

- Goals
  - Make the specs describe what `main` does today, including what it does
    **not** guarantee.
  - Replace the literal checksum with a reviewed-pin requirement that stays
    true across OpenD upgrades without weakening the check.
  - Split restart behaviour by process, so the requirements hold under both the
    current two-container layout and the proposed single container.
  - Show for every requirement which code carries it and which test, if any,
    demonstrates it.
- Non-Goals
  - No runtime change. Where the behaviour looks wrong, that goes in `tasks.md`
    § 3 as follow-up work, not into this change.
  - No durable trading state, no order-outcome reconciliation, no automatic
    retry of trading commands.
  - No claims about live-broker behaviour that no test has exercised.

## Decisions

- **Reference the pin; do not restate it.** The requirement names the property
  (a reviewed, version-controlled pin, checked before extraction, fatal on
  mismatch, whatever the source). The current digest goes in `proposal.md` as
  evidence. Alternative considered: keep a literal digest in the spec and bump
  it with each release. Rejected because two copies of one value are exactly
  what drifted (`d38aad77…` vs `72eaa6e4…`).
  The requirement also says the pin must not be derived at build time. That
  rules out the tempting "fix" of trusting whatever digest the download source
  publishes alongside the archive.
- **Word restart recovery by process, not by container.**
  `refactor-single-container-deployment` would put both processes in one
  container and restart OpenD in place under a supervisor. "The OpenD gateway
  process restarts" is true in both layouts; "the `opend` container restarts"
  is not.
- **Allow failures during downtime explicitly.** The stateless transport and
  the SDK's reconnect remove the need for *manual* recovery. They do not make
  requests immune to an outage. A spec that implied otherwise would be
  unfalsifiable in practice and misleading in an incident.
- **Separate reconnect replay from trade replay.** See the next section. The
  spec permits the SDK to restore connection state and forbids replaying
  orders.
- **Lock on reconnect is requested, not guaranteed.** `_enforce_gateway_lock`
  deliberately swallows a refused lock (`trade_service.py:295-314`). Raising on
  the SDK's reconnect thread would abort the SDK's post-reconnect work and cost
  the connection that just succeeded. The spec follows the code: a lock is
  requested, a refusal is logged as a warning, and the policy layer, which runs
  before anything reaches the gateway, keeps refusing writes and unlocks.
- **A new `transport-sessions` capability.** `configuration` owns the
  `MCP_TRANSPORT` and `MCP_AUTH_TOKEN` variables, but it says nothing about
  session semantics or connection ownership, and no other capability does.
  Putting them in `container-deployment` would tie a transport contract to a
  deployment method; the same server runs outside containers.
- **Keep existing scenario names in MODIFIED blocks.** OpenSpec 1.13 refuses a
  MODIFIED requirement that drops an existing scenario, so renamed scenarios
  keep their old titles with rewritten bodies (for example, `Server starts in
  read-only mode` now describes the lock on initial connection).

### Reconnect replay (what the SDK actually does)

Read from the vendored SDK, `moomoo-api` 10.10.7008,
`moomoo/trade/open_trade_context.py`:

- `unlock_trade(...)` caches `(password, password_md5)` in `_ctx_unlock` after a
  **successful** unlock, and clears it after a **successful** lock (line 162).
- `on_api_socket_reconnected()` replays that cached unlock if one is present,
  then re-subscribes account pushes (lines 37-55). The quote context replays
  its subscriptions the same way.
- Neither hook touches orders.

The server wraps the trade context's hook (`trade_service.py:270-293`). The
wrapper calls the SDK's hook, returns the SDK's result unchanged, and then, in
READ_ONLY mode only, requests a lock. So what a reconnect restores depends on
the mode:

| Mode | Cached unlock at reconnect time | After reconnect |
| --- | --- | --- |
| READ_ONLY | never set (policy refuses unlock) | lock requested by the wrapper |
| SIMULATE | never set (policy refuses unlock) | nothing |
| REAL, before any order | set, if startup auto-unlock succeeded | SDK replays the unlock; gateway unlocked |
| REAL, after a JIT order | cleared by the JIT re-lock | nothing; next order unlocks just in time |

The third row is existing behaviour that this change documents and does not
endorse; see Open Questions.

## Evidence

Legend: **unit**: unit test against a mocked SDK. **smoke**:
`scripts/smoke-test.sh` against real containers with OpenD replaced by a TCP
stand-in that accepts connections and speaks no OpenD protocol. **manual**:
measured by hand once, per the commit message, not automated. **code**: follows
from reading the code; no test. **gap**: not demonstrated anywhere.

Nothing here has been exercised against a live broker. The stand-in gateway
proves networking and restart topology only, not login, subscription
restoration, unlock state or order outcomes.

### container-deployment

| Requirement › scenario | Carried by | Evidence |
| --- | --- | --- |
| Integrity › matches pinned hash | `Dockerfile.opend:34` (`sha256sum -c` chained before `tar`) | success path: every CI image build; mismatch path: **code** |
| Integrity › every download source | `Dockerfile.opend:27-34` (check runs after the fallback `if`) | **code**; fallback never exercised in CI |
| Integrity › expected digest from reviewed config | `docker-compose.yml:3-5` and `.github/workflows/ci.yml:359-367` pass no build args | **code** |
| Integrity › version bump fails closed | by construction | **code** |
| Gateway restart › MCP outlives it | `docker-compose.yml` `depends_on` without `restart: true` | **unit** `test_compose_topology.py:157`; **smoke** `smoke-test.sh:215-220` |
| Gateway restart › endpoint keeps answering | separate network namespaces | **smoke** `smoke-test.sh:204-213` (`tools/list` only) |
| Gateway restart › access resumes | SDK reconnect | **smoke**: new TCP connections reach the restarted stand-in (`:200-202`). **gap**: no gateway-backed call after the restart; no real login |
| Gateway restart › health reports absence | `services/health.py` | **unit** `test_health.py` (status mapping); **gap** during a real restart |
| MCP restart › no re-initialization | `server.py:262` `stateless_http=True` | **smoke** `smoke-test.sh:227-236` |
| MCP restart › connections reopened on demand | `server.py:197-207` `get_services` | **unit** `test_sessions_share_one_set_of_connections`; **gap**: smoke does not check the gateway after an MCP restart |
| No replay › overlapping request may fail | bounded waits: `health.py:34` (3s), `trade_service.py:36` (5s) | permissive; nothing to prove |
| No replay › SDK replays connection state only | `trade_service.py:286-293`; SDK lines 37-55 | **unit** `test_the_sdk_reconnect_work_still_runs_and_is_reported`; order non-replay: **code** |
| No replay › lost order response | no retry around order calls in `trade_service.py` | **code**; **gap** live |

### trade-unlock

| Requirement › scenario | Carried by | Evidence |
| --- | --- | --- |
| Read-only lock › on initial connection | `trade_service.py:268` | **unit** `test_connecting_locks_the_gateway` |
| Read-only lock › after SDK reconnect | `trade_service.py:288-291` | **unit** `test_reconnecting_locks_the_gateway_again`, `test_the_sdk_reconnect_work_still_runs_and_is_reported`, `test_the_real_sdk_class_allows_the_hook` (real SDK class); **gap** against a live gateway |
| Read-only lock › refused lock reported | `trade_service.py:309-314` | **unit** `test_a_refused_lock_does_not_break_the_reconnect` (reconnect path); connect path shares the function: **code** |
| Read-only lock › writes and unlocks refused | `trading_policy.py` `check_write`, `check_unlock` | **unit** `test_read_only_refuses_every_write_tool` (asserts no SDK call), `TestWriteMatrix`, `test_unlock_permission_follows_mode` |
| Read-only lock › other modes do not lock | `trade_service.py:306-307` | **unit** `test_other_modes_are_left_alone` |
| Auto-unlock › plain / MD5 in REAL | `server.py:70-118, 176-178` | **unit** `test_auto_unlock_with_plain_password`, `test_auto_unlock_with_md5_password`, `test_real_mode_with_password_unlocks` |
| Auto-unlock › password outside REAL | `server.py:176`, `check_unlock` | **unit** `test_password_does_not_unlock_in_read_only`, `…_in_simulate` |
| Auto-unlock › no password | `server.py:84-89` | **unit** `test_skip_unlock_when_no_env_vars` (no unlock); log wording: **code** |
| Auto-unlock › trade connection not ready | `server.py:176` (`trade_ctx is not None`) | **code**; the nearest test runs in READ_ONLY, where no unlock would happen anyway |
| Auto-unlock › failure | `server.py:113-118` | **unit** `test_graceful_failure_on_unlock_error`, `test_failed_unlock_does_not_change_the_mode` |
| Manual unlock › in REAL | `tools/account.py:320` → `TradeService.unlock_trade` | **unit** `test_unlock_trade` (tool) |
| Manual unlock › refused outside REAL | `trade_service.py:647` | **unit** `test_unlock_tool_is_refused_outside_real_mode` (SIMULATE); READ_ONLY via `test_unlock_permission_follows_mode` |
| Env fallback › scenarios in REAL | `tools/account.py` | **unit** `test_unlock_trade_env_vars` |
| Env fallback › does not bypass mode | policy checked before credentials are used | **code** |

### transport-sessions

| Requirement › scenario | Carried by | Evidence |
| --- | --- | --- |
| Stateless › no session id issued | `server.py:262` | **smoke** (initialize returns no id; `smoke-test.sh:105-116`); **gap**: no unit test pins `stateless_http=True` |
| Stateless › no prior initialize needed | `server.py:262` | **smoke** `smoke-test.sh:231-236`; **manual** (`f6d8a92`) |
| Stateless › foreign session id ignored | `server.py:262` | **manual** only (`f6d8a92`); smoke never sends one because none is issued |
| Stateless › session id is not authentication | `server.py:29-46` per-request middleware | **unit** `test_streamable_http_app_rejects_missing_auth_token`; the with-session-id case: **code** |
| Stateless › notifications ride the call | MCP SDK stateless mode | **manual** (`f6d8a92`) |
| Process-owned › fresh process has not dialled | `server.py:197-207` (lazy) | **code**; smoke relies on it (`smoke-test.sh:17-22`) |
| Process-owned › shared across requests and clients | `server.py:121-207` | **unit** `test_sessions_share_one_set_of_connections` (lifespan level); **manual**: one gateway connection per process (`f6d8a92`) |
| Process-owned › ending a session leaves them open | same | **unit**, same test (`close` not called) |
| Process-owned › released once at exit | `server.py:206, 210-220` | **unit** `test_shutdown_is_idempotent` |
| Process-owned › unreachable gateway | `server.py:157-170` | **unit** `test_startup_survives_total_gateway_failure`; `test_event_loop.py:112` |

## Risks / Trade-offs

- Specifying a behaviour turns it into a contract. The weakest items above
  (foreign session id ignored, notifications on the call's response) rest on a
  single manual measurement. If an MCP SDK upgrade changed stateless mode, no
  test would notice. Adding those tests is in `tasks.md` § 3.
- The permissive scenarios (a request "MAY fail") cannot be violated, which is
  the point. They exist so nobody reads the recovery requirements as a promise
  of uninterrupted service.

## Migration Plan

None. Documentation only. On approval the change is archived and the three
specs are updated. There is no deploy step.

## Open Questions

- **Should REAL mode hold a standing startup unlock at all?** Auto-unlock
  leaves the gateway unlocked until the first JIT re-lock, and the SDK replays
  that unlock on every reconnect in the meantime. JIT unlock already covers
  every order command, so the standing unlock mainly serves REAL reads that
  need one. This change documents the behaviour and does not decide the
  question.
- **Should a refused READ_ONLY lock surface in `check_health`?** Today it goes
  to the log only. The policy layer makes it harmless to writes, but an operator
  cannot see it without reading logs.
- **Is OpenD's unlock gateway-wide or per connection?** Still unconfirmed
  (`docs/state-and-restarts.md` § Known gaps). It determines whether the
  quote connection or another client could observe a REAL unlock.
