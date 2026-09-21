# Proposal

## Why

The server can already refuse an unsafe order. It cannot yet remember that it sent
one.

Stage 1 (`harden-trading-safeguards`) makes the dispatch boundary visible in errors:
a mutation reports *not sent*, *outcome unknown (possibly sent)*, or *acknowledged*.
That is the right classification, and it is all held in memory. Nothing survives the
process. So today:

- A caller that retries after a lost response has no way to say "this is the same
  order I already asked for". The server cannot tell a retry from a second order.
- An *outcome unknown* result is returned to the caller and then forgotten. Nothing
  records that an operation is unresolved, and nothing stops the next mutation from
  being admitted while it stays that way.
- A restart erases the fact that a request was ever in flight. The
  `container-deployment` requirement *Restart Recovery Does Not Replay Trading
  Commands* correctly forbids replay, but there is no record to reconcile against
  either.

This change adds that memory, for paper trading only. It introduces a persistent,
application-local SQLite execution journal that owns operation identity, suppresses
duplicate submission, commits intent before dispatch, and preserves uncertainty
until it is explicitly reconciled.

It is deliberately scoped to `SIMULATE`. The point of a paper phase is to exercise
these recovery paths under fault injection where the cost of being wrong is zero.
Nothing here enables, certifies or prepares REAL execution.

## Prerequisite

`harden-trading-safeguards` is a prerequisite, not merely a related change.

This proposal is written directly against that change's delta specs, on the same
branch, rather than against `main`. Where both changes touch a requirement, the
delta here carries the Stage 1 text forward and layers the journal on top, so the
two can be archived in order without the second silently reverting the first. The
requirements reconciled this way are `Support Placing Orders`, `Support Modifying
Orders` and `Support Cancelling Orders`.

The journal depends on these Stage 1 contracts and does not restate them:

- explicit `trd_env` on every mutation, with no default and no inference;
- account resolution that refuses to choose between eligible accounts;
- fail-closed order limits and order-value validation;
- the three-outcome dispatch boundary, which the journal persists rather than
  redefines.

**Dependency order.** Safeguard code and specs land → recheck and validate this
proposal against those landed contracts → separately authorize implementation →
automated acceptance tests → separately authorize paper-provider validation.

### An unresolved conflict with the prerequisite

Stage 1 task 1.2 proposes placing a **GTC** limit order in `SIMULATE` and querying
it on a later trading day, to learn whether `order_list_query` still returns it.

Moomoo documents paper orders as **DAY only**. If that documentation holds, the
prerequisite's task cannot be performed as written: there is no GTC paper order to
leave open overnight.

This proposal does not silently inherit that task, and does not silently drop it
either. It is recorded here and in `design.md` as a conflict for the prerequisite's
owner to resolve, and task 1.4 below asks the same question in a form that a
DAY-only provider can answer.

## What Changes

- **A persistent execution journal, behind one boundary.**
  - A new `ExecutionStore` wraps the standard library `sqlite3`. No SQL, connection
    or file handle reaches the tool layer or an agent.
  - Exactly one executor process is permitted; enforced via a non-blocking process
    lockfile.
  - It is opened only under an explicit `SIMULATE` policy. `READ_ONLY` operation
    stays entirely database-independent: no file is opened, created or required.
- **Caller-owned execution identity and admission epochs.**
  - Every paper mutation carries a caller-supplied, opaque `operation_id` alongside
    a fresh per-start `admission_epoch` that the caller preserves across retries.
    The server never generates, defaults, parses, or rewrites an identifier.
  - Tokens are looked up among existing journal rows across all epochs first: matching
    retries return stored state without redispatch; conflicting requests are refused.
  - Unknown tokens carrying a non-current epoch are refused without rewrite,
    preventing restored older backups from silently admitting retries of live orders.
  - In-flight retries receive an immediate bounded response rather than waiting
    unbounded or launching concurrent dispatch.
- **Two-phase dispatch lifecycle.**
  - Admission and intent are persisted first in state `ADMITTED`.
  - Safety checks (limits, allowlists, target order inspection) run next. Ordinary
    pre-dispatch refusals are durably recorded as `REFUSED` with local disposition
    `NOT_SENT` without ever committing a dispatch marker.
  - Only after all checks pass is `DISPATCHING` committed in a short transaction,
    followed by a serialized SDK mutation invocation without holding a database
    transaction across broker I/O.
- **Modification identity is the patch, not the merged order.**
  - Stage 1 has `modify_order` fetch the existing order and assess the merged
    result. The journal stores the caller's **target order ID and original patch**
    separately from the merged broker request, and fingerprints the patch.
  - A price-only retry is therefore still the same operation after a partial fill
    changed the observed quantity. Fingerprinting the merged request would turn an
    honest retry into a conflict.
- **Decimal-string prices on paper mutations.**
  - Paper mutation prices are accepted as decimal strings and stored verbatim, so
    identity and journal records do not depend on binary float coercion.
- **Uncertainty is preserved, not guessed.**
  - An unresolved operation is never automatically replayed, and never resolved by a
    substitute or replacement order.
  - Attribute/time matches are candidate matches only; automatic association requires
    reliable broker identity or verified provider correlation.
  - Finding a modification or cancellation target order does not prove that the
    mutation succeeded.
  - Reconciliation uses order and history observations. Moomoo paper provides no
    deal query, so nothing in the recovery path may depend on deal records.
- **Independent paper journal blocking and runtime failure handling.**
  - Paper journal blocking operates independently of Stage 1's REAL relock halt.
    Calling `lock_trade` does not clear journal failures.
  - Dispatched operations with unknown outcomes or failed outcome persistence block
    subsequent automated paper mutations. Under no circumstances does execution
    fall back to unjournaled mutations, including unjournaled cancellations.
  - Storage failures fail closed immediately; recovery requires restoring healthy
    storage and re-running startup recovery review.
- **Late local failures are reported honestly.**
  - When a write is acknowledged but the receipt cannot be read or stored, the
    result separates what the broker evidenced from what failed locally, including
    whether the submission state was merely *observed* or *durably stored*.
- **Storage is never silently recreated.**
  - A missing database at a configured path fails closed rather than starting an
    empty journal that has forgotten its duplicate-suppression history.
  - Supported storage requires POSIX fsync durability; raw dirty file copies are
    not supported backups. Consistent backup procedures use SQLite's backup API or
    stopped-process snapshots.
- **Recovery review gate and operator recovery acknowledgement.**
  - Every paper execution-process start requires recovery review before new
    mutations are admitted.
  - A named, operator-only recovery acknowledgement mechanism requires explicit
    authorization, durable justification, and verified evidence.
    Mutation uncertainty remains distinct from recovery disposition: no generic
    "accept risk" override exists. Evidence-backed accounting of a terminal target
    allows review completion without falsely claiming an uncertain mutation
    succeeded. Insufficient evidence keeps execution blocked.
- **Dedicated, optional storage.**
  - An optional execution directory, and a separate `execution-data` volume, distinct
    from OpenD's `opend-data`. The existing authorization storage and the
    single-container deployment architecture are unchanged.

### What this guarantees, precisely

**At most one application-level SDK mutation invocation per admitted operation
identifier**, under the storage, process-isolation and caller assumptions stated in
`design.md`.

This is not exactly-once execution across SQLite and the broker, and the documents
do not claim it is. A process can still die between the commit and the invocation,
leaving an operation that must be reconciled rather than assumed.

**Scope and limitations of restore detection:**
The admission epoch mechanism detects retries of unrecorded operations when an
older backup is restored across process restarts, *provided* the caller preserves the
epoch under which the operation was initiated. It does not detect all restores (e.g.,
if an older backup is restored while callers generate fresh tokens, or if a restore
replaces both process memory and storage). It does not recover missing history from
restored backups. Crucially, the system does not infer `NOT_SENT` from a restored
pre-dispatch (`ADMITTED`) row without establishing journal continuity.

## Scope

**Version 1 covers:** US stock and ETF instruments; whole-share quantities; `BUY`
and `SELL`; `NORMAL` limit orders; `DAY` time in force; regular trading hours only;
price and total-quantity modifications; individual cancellations; and reconciliation
of journal-owned orders.

Every provider-dependent part of this scope stays unavailable until verified against
a real paper account (task group 1).

### Non-goals

- **REAL execution.** Nothing here enables or certifies it. Paper records can never
  be promoted into live execution.
- **Stage 3**, prepared orders bound to a trusted Telegram approval.
- **Stage 4**, a persistent operator pause, a notification outbox, readiness
  reporting.
- Combo, market, stop and trailing-stop mutations; multi-leg packages; fractional
  shares; extended-hours routing; GTC and other non-`DAY` time in force.
- Bulk "cancel all" operations, and reconciliation of orders the journal does not
  own.
- An external database engine, a database service, or a second process.
- Strategy generation or autonomous investment decisions.

## Capabilities

### New Capabilities

- `execution-journal`: operation admission and execution identity, request
  canonicalization and modification identity, two-phase pre-dispatch commitment,
  transition lifecycle, outcome classification, late-failure reporting, candidate vs
  proof reconciliation, storage lifecycle, single executor process locking, admission
  epochs, paper-journal blocking, and the recovery review gate with operator
  recovery acknowledgement.

### Modified Capabilities

- `trading-policy`: `READ_ONLY` stays database-independent; `SIMULATE` mutations
  never fall back to unjournaled execution; independent paper-journal blocking not
  cleared by `lock_trade`; a simulated-account allowlist; and the version 1 paper
  scope restriction, which fails closed rather than bypassing the journal.
- `order-placement`: carries Stage 1's required `trd_env`, explicit account
  resolution and dispatch-boundary reporting forward, and adds the required
  `operation_id` with `admission_epoch`, two-phase commitment, pre-dispatch refusal
  without dispatch marker, bounded in-flight retry response, and duplicate suppression
  for paper placements.
- `order-modification`: the same, for modifications and cancellations, plus target
  order ID and patch identity, candidate reconciliation rules, refusal to infer
  mutation success from target presence, and factual recording when a cancellation
  races a fill.
- `container-deployment`: a dedicated `execution-data` volume, its ownership,
  single-process enforcement, consistent backup procedures, and restoration
  behaviour, leaving `opend-data` untouched.
- `system-health`: reports journal state (`READY`, `REVIEW_PENDING`,
  `JOURNAL_BLOCKED`, `DISABLED`) without a gateway request, alongside Stage 1's
  `execution_halted`.

## Impact

- **Code**:
  - new `services/execution_store.py`: connections, schema, process lockfile,
    admission epochs, reservation, transitions, audit log, queries;
  - new `services/execution_identity.py`: canonicalization and fingerprinting;
  - `services/trade_service.py`: admission, pre-dispatch checks, commitment,
    serialized dispatch, outcome recording, reconciliation, independent journal
    blocking, operator recovery acknowledgement, and the recovery gate around the
    Stage 1 dispatch path;
  - `services/trading_policy.py`: the simulated-account allowlist, independent
    journal failure state, and v1 scope;
  - `settings.py` (introduced by Stage 1): journal configuration;
  - `services/health.py`, `services/base_service.py`: journal state;
  - `tools/trading.py`: `operation_id`, `admission_epoch`, operator recovery
    acknowledgement tool, and decimal-string prices;
  - `tools/system.py`: journal state in health output.
- **Tests**: new unit/integration suites `U01`–`U18`, container tests `C01`–`C04`,
  and separately authorized manual checks `M01`–`M04`. See `tasks.md` for the
  scenario-to-test traceability table.
- **Configuration and operations**: `.env.example`, `docker-compose.yml`,
  `Dockerfile`, `docs/state-and-restarts.md`, `docs/deploy-vps.md`, and the
  `openspec/config.yaml` context.
- **Agent (ZeroClaw)**: paper write tool calls must carry a stable `operation_id`
  and active `admission_epoch` that survive a retry, and prices as decimal strings.
- **Dependencies**: none. Standard library `sqlite3` and `fcntl` only.
