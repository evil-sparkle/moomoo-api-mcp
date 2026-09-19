# Design

## Context

See `proposal.md` for the motivation. This design builds on the contracts introduced
by `harden-trading-safeguards`, which is a prerequisite and lives on the same branch.

### What Stage 1 already establishes

- One pre-dispatch sequence per mutation: validate, resolve the account, check the
  halt, assess limits, then unlock-dispatch-relock.
- `trd_env` is required and never inferred; account resolution refuses ambiguity.
- Three outcomes at the dispatch boundary: *not sent*, *outcome unknown (possibly
  sent)*, *acknowledged*. Only `RET_OK` counts as an acknowledgement, and no message
  claims a request "reached" the gateway.
- An in-memory `ARMED`/`HALTED` execution state, cleared only by `lock_trade`.

The journal persists what Stage 1 classifies. It does not reclassify it.

### Provider facts this design depends on

These shape the design and are **unverified** until task group 1 runs against a real
paper account. Each is recorded under the decision it affects.

- **Moomoo documents paper orders as `DAY` only.** Version 1 therefore accepts only
  `DAY`, and no recovery path may assume an order can outlive its trading day.
- **Moomoo paper provides no deal query.** Recovery therefore relies on order and
  history-order observations. `get_deals` must not appear in any recovery path.
- Whether `order_list_query` returns a terminal paper order later the same day, and
  for how long a history query retains it, is unverified and bounds how late
  reconciliation can still succeed.

### The prerequisite's GTC task conflicts with the first fact

Stage 1 task 1.2 proposes placing a **GTC** limit order in `SIMULATE` and querying it
on a later trading day. If paper is `DAY` only, that order cannot be placed as
described, so the task cannot answer its question.

This is flagged, not inherited and not dropped. Task 1.4 asks the underlying
question — how long a paper order remains queryable after it reaches a terminal
state — in a form a `DAY`-only provider can answer. The prerequisite's owner decides
what happens to task 1.2 itself.

## Goals / Non-Goals

**Goals:**

- At most one application-level SDK mutation invocation per admitted operation
  identifier, under the assumptions in *Assumptions and limits* below.
- Intent is durable before dispatch; uncertainty is durable after it.
- No automatic replay, ever, by any path: not on retry, not on reconnect, not on
  restart.
- `READ_ONLY` operation is completely database-independent.
- The policy and store stay pure and unit-testable. Gathering broker facts is the
  trade service's job.

**Non-Goals:**

- Exactly-once execution across SQLite and the broker. It is not achievable here and
  is not claimed.
- A general operator pause/resume facility. The recovery gate is recovery-specific.
- REAL execution storage, approvals, or a notification outbox.
- Restructuring `TradeService` beyond the paths this change needs.

## Decisions

### 1. `sqlite3` behind an `ExecutionStore` boundary

Python's standard `sqlite3`, wrapped by `ExecutionStore`. No SQL string, connection
or path escapes the boundary; tools call `trade_service`, which calls the store.

- *Alternative:* an external engine (PostgreSQL, MySQL) or Redis. Rejected: a second
  process and a network dependency on a single-tenant VPS, to protect a write rate
  measured in orders per hour.
- *Alternative:* an append-only JSON or pickle log. Rejected: no atomic
  reserve-or-conflict, which is the one primitive this change actually needs.
- *Alternative:* SQLAlchemy. Rejected: a heavyweight dependency for perhaps a dozen
  explicit statements.

### 2. Connections, transactions and locking

- **Short, worker-owned connections.** Stage 1 tools run blocking work through
  `tools/offload.py` `run_blocking`, on `anyio` worker threads. A `sqlite3.Connection`
  is not safe to share across threads, and a long-lived one would outlive the worker
  that made it. Each unit of journal work opens a connection, does its work, and
  closes it. No pool, no `threading.local`, no `check_same_thread=False`.
- **Explicit transactions.** `isolation_level=None`, with explicit `BEGIN IMMEDIATE`
  for writes. The write lock is taken at `BEGIN`, not at first write, so a conflict
  surfaces immediately instead of midway through a transaction.
- **Bounded lock waits.** `PRAGMA busy_timeout` is set from configuration, with a
  bounded default. A lock that cannot be acquired within it fails closed as a
  storage error. Nothing waits unbounded, and nothing blocks the event loop.
- **No transaction spans broker I/O.** Structurally enforced: the store exposes
  commit-shaped operations, never an open handle a caller could hold across a call.

### 3. Rollback journaling with `DELETE`, and `synchronous = EXTRA`

Version 1 uses the rollback journal (`PRAGMA journal_mode = DELETE`) with
`PRAGMA synchronous = EXTRA`. Not WAL.

- **Why not WAL.** WAL's benefit is concurrent readers alongside a writer. There is
  one writer here and an almost idle read path, so there is nothing to win. Its cost
  is real: WAL adds `-wal` and `-shm` sidecar files, and a backup or restore that
  copies only `execution.db` can silently lose committed transactions still in the
  WAL. Operators back up and restore this volume by hand. A single self-contained
  file is the property worth having.
- **Why `EXTRA`.** In rollback-journal mode `EXTRA` syncs the containing directory
  after the journal is deleted, so a commit survives a power loss that `FULL` alone
  may not. The pre-dispatch commit is exactly the write where that matters.
- **Cost.** Two fsyncs per commit and no reader concurrency. At this write rate that
  is not a consideration.
- `PRAGMA foreign_keys = ON` for the transition log.

### 4. Execution identity is established by the caller, before sending

The operator or deterministic caller establishes the **complete operation token**
before the request is sent. The server never generates, defaults or synthesizes one.

- A server-generated identifier is regenerated by the retry it is meant to suppress,
  so it suppresses nothing.
- A retry preserves the token. Same token and same canonical request returns the
  stored state. Same token, different contents, is a conflict, refused.
- The token is opaque to the server: a bounded-length, non-empty printable string.
  The server does not parse it or infer structure from it.

- *Alternative:* server-generated UUIDs returned to the caller. Rejected as above.
- *Alternative:* derive identity from the request contents alone. Rejected: two
  genuinely intended identical orders would be indistinguishable from a retry.

### 5. Canonicalization, and decimal strings for prices

The canonical request is a deterministic serialization of the fields that define the
operation: environment, account, operation type, and the operation's own parameters.
It is hashed into a fingerprint, and the canonical form is stored alongside it so a
conflict can be explained rather than merely asserted.

**Prices are accepted and stored as decimal strings**, and canonicalized by decimal
value rather than by binary float. A price that arrives as a float is converted at
the tool boundary, where the original text is still available.

- Identity must not depend on whether `350.0`, `350.00` and a float repr agree, and a
  journal is a poor place to discover that `0.1 + 0.2` has a tail.
- The value the broker receives is unchanged; this governs identity and storage.
- *Alternative:* round to a fixed number of decimal places. Rejected: the correct
  number is instrument-dependent, and getting it wrong silently merges two distinct
  orders.

### 6. Modification identity is the caller's patch

Stage 1 has `modify_order` fetch the existing order and assess the merged result.
The journal stores **two distinct records**:

- the caller's **original patch** — the fields actually supplied — which is what the
  fingerprint is computed over;
- the **merged broker request** that was dispatched, stored for audit.

A price-only modification is therefore identified by its price alone. If a partial
fill changes the order's remaining quantity between the first attempt and the retry,
the merged request differs while the patch does not, and the retry is correctly
recognized as the same operation.

Fingerprinting the merged request would reinterpret a price-only retry using a newly
observed quantity and refuse it as a conflict — precisely when the caller most needs
the retry to work.

### 7. The submission boundary

1. Admit the operation: reserve the identifier, or return the stored state, or
   refuse the conflict. One `BEGIN IMMEDIATE` transaction.
2. Commit the intent and the **dispatch marker** — the record that an invocation is
   about to happen — and close the transaction.
3. Run Stage 1's pre-dispatch sequence and make **one** SDK mutation invocation.
4. Record the outcome in a new, short transaction.

The dispatch marker exists so that a process which dies between steps 2 and 4 leaves
a record that says "an invocation may have started", not one that says "nothing
happened". This is the difference between a recoverable state and a lost order.

### 8. Outcome classification

Stage 1's three outcomes are persisted, with local lifecycle states kept strictly
distinct from broker order statuses. Local: `ADMITTED`, `DISPATCHING`,
`ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED`, `REFUSED`. Broker: `SUBMITTED`,
`FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL`, `REJECTED`, and whatever else the
provider reports.

An acknowledgement is never recorded or reported as a fill, and a timeout is never
recorded as a rejection. Stage 1 already refuses to distinguish a broker rejection
from a transport failure by parsing gateway error text; the journal inherits that
refusal rather than re-deriving it.

### 9. Uncertainty is preserved, and reconciled — never replayed

An operation in `UNKNOWN_OUTCOME` is never automatically resubmitted, and never
resolved by placing a substitute or replacement order. Reconciliation is explicit.

- It queries **orders and history orders** for the journal-owned account. It must not
  use a deal query: Moomoo paper does not provide one.
- Matching prefers a recorded broker order identifier. Without one, it matches on
  operation attributes within the operation's own submission window.
- Exactly one match resolves the operation to `RECONCILED`, adopting the broker
  status as authoritative.
- **Zero matches does not resolve it.** An empty result is not proof that the order
  never existed; it is equally consistent with a query that cannot see it yet, a
  retention window that has passed, or an account mismatch. The operation stays
  unresolved.
- Two or more matches do not resolve it either. The system does not guess.

### 10. Late local failures report evidence separately from error

When the broker acknowledged a request but something afterwards failed locally —
the receipt could not be converted, or the outcome could not be stored — the result
separates:

- **what the broker evidenced**: the acknowledgement, and the order identifier if it
  was readable;
- **what failed locally**: conversion or persistence, named as such;
- **how the submission state is held**: whether it was merely **observed** in this
  process, or **durably stored**.

That last distinction is the one an operator acts on. An observed-but-unstored
acknowledgement disappears with the process; a durably stored one does not. Reporting
them identically would make a recoverable situation look like an unrecoverable one,
and the reverse.

A late local failure never reports a clean success, and never reports a safe-to-retry
error.

### 11. Storage lifecycle, admission epochs and restoration

- **Never silently recreate.** Creating the journal is an explicit act, gated by
  configuration. A missing file at a configured path is a startup failure, not an
  invitation to start an empty journal that has forgotten every identifier it ever
  admitted.
- **Schema version** in `PRAGMA user_version`. A newer version than the binary
  understands fails closed without touching the file. An integrity check failure
  fails closed too.
- **Admission epochs.** The store records an epoch identifier, and stamps every
  admitted operation with the epoch that admitted it. On start, the previous epoch is
  retired and a new one begins.

  After a restart, an operation identifier presented for an operation the store
  cannot account for — including one whose row is absent because an older backup was
  restored over the journal — is **refused**, not admitted as new. Admitting it would
  turn a retry of a possibly-live order into a fresh order, which is the exact
  failure this change exists to prevent.
- **Restoration is a first-class event, not a silent one.** A restored journal is
  detectable (its epoch history does not match what this process retired) and
  requires recovery review before new mutations.

### 12. The recovery review gate

**Every paper execution-process start requires recovery review before new
mutations.**

On start, the store enumerates operations that are not in a terminal state. Until
review has run and each has been accounted for, new mutations are refused; existing
reconciliation and all reads stay available.

This is deliberately conservative. Without it, the most dangerous state in the system
— a freshly restored older backup — is also the one that looks most like a clean,
empty, ready-to-trade journal.

It is a recovery-specific gate. It is not an operator pause/resume facility, it has
no manual "pause" entry point, and nothing here anticipates Stage 4's storage.

### 13. Storage location and the container

- An optional execution directory, configured by path, on a dedicated
  `execution-data` volume mounted at `/var/lib/moomoo-mcp/data`, owned by uid 10001.
- **`opend-data` is untouched.** Its mount path and owning uid do not change, so the
  `container-deployment` requirement *Session State Persistence* continues to hold
  and no device re-authorization is triggered. The single-container supervision
  architecture is unchanged; no process is added.
- A `READ_ONLY` deployment needs neither the volume nor the variables.

### 14. Health reporting

Health reports journal state — enabled or not, schema version, unresolved operation
count, whether recovery review is outstanding — from memory and the store, **without
a gateway request**, and without changing the top-level connectivity status. It sits
alongside Stage 1's `execution_halted` and does not replace it. Health never clears
the gate and never resolves an operation.

## Assumptions and limits

The guarantee — at most one application-level SDK mutation invocation per admitted
operation identifier — holds only under these assumptions. They are stated so that a
reader can check them rather than infer them.

1. The caller preserves the operation identifier across retries. A caller that
   generates a new identifier per attempt defeats the mechanism entirely.
2. The journal file's storage honours fsync. A filesystem or volume driver that lies
   about durability breaks the pre-dispatch commit.
3. Exactly one server process writes the journal at a time.
4. The journal is not edited, replaced or restored underneath a running process.

What is **not** guaranteed:

- Exactly-once execution. A crash between the dispatch marker and the SDK invocation
  leaves an operation that must be reconciled, not assumed.
- That reconciliation always resolves. Zero matches leaves the operation unresolved
  by design.
- Anything about REAL execution.

## Risks / Trade-offs

- **[Risk]** The provider facts above are unverified. Paper may not be `DAY` only, or
  may expose a deal query.
  → **Mitigation:** task group 1 checks each before implementation. A deal query, if
  it exists, is additive to reconciliation, not a change of approach.
- **[Risk]** Reconciliation depends on the retention of terminal paper orders. If a
  provider drops them quickly, a late reconciliation finds zero matches.
  → **Mitigation:** zero matches is already specified as unresolved, so the failure
  is safe, not silent. Task 1.4 measures the window.
- **[Trade-off]** The recovery review gate makes every restart require attention,
  including routine ones.
  → **Mitigation:** accepted, and deliberate, for a paper phase whose purpose is to
  exercise recovery. A quiet restart with no unresolved operations reviews to empty.
- **[Trade-off]** Refusing an identifier from a retired epoch will occasionally
  refuse a legitimate retry after a restore.
  → **Mitigation:** intended. Refusing a valid retry costs a manual check; admitting
  a retry of a live order as new costs a duplicate order.
- **[Risk]** Required `operation_id` and decimal-string prices break existing agent
  call sites.
  → **Mitigation:** paper-only, and the failure is a loud missing-argument error, not
  a silent behaviour change.
- **[Risk]** `synchronous = EXTRA` doubles commit latency.
  → **Mitigation:** two fsyncs against a broker round trip is not measurable.
- **[Trade-off]** A fake broker proves the journal's logic, not the provider's
  behaviour.
  → **Mitigation:** stated explicitly in `tasks.md`. Fake-broker success marks no
  provider capability verified.

## Migration Plan

1. Land `harden-trading-safeguards`, including its implementation and specs.
2. Re-check these delta specs against the landed Stage 1 specs, and re-run strict
   validation. The three reconciled requirements are the ones to check first.
3. Run task group 1 against a real paper account to verify the provider facts, and
   resolve the GTC conflict with the prerequisite's owner.
4. Obtain separate authorization for implementation.
5. Add the `execution-data` volume and the new variables. `READ_ONLY` deployments
   need no change and continue with no database.
6. Run the automated suites, including container tests.
7. Obtain separate authorization for paper-provider validation, then run the manual
   checks.
8. **Rollback:** redeploy the previous image. The `execution-data` volume is left
   unmounted and intact; `opend-data` is untouched throughout.

## Validation status

The pinned CLI was available when these artifacts were written, and
`npx -y @fission-ai/openspec@1.13.1 validate --all --strict --no-interactive` was run
against the whole `openspec/` tree, including this change and its prerequisite.

Strict validation checks document structure. It does not check that these
requirements are correct, that the provider behaves as assumed, or that the
prerequisite's contracts are correctly carried forward. Those remain review
obligations, and task 1.0 re-runs validation once Stage 1 lands.
