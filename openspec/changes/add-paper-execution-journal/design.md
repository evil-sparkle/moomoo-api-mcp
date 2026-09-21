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
- An in-memory `ARMED`/`HALTED` execution state for REAL relock halt, cleared only
  by `lock_trade`.

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
- Two-phase dispatch lifecycle: admission and intent persisted first, pre-dispatch
  safety checks run and refusals recorded as `NOT_SENT` without a dispatch marker,
  followed by committed `DISPATCHING` marker before serialized SDK invocation.
- No automatic replay, ever, by any path: not on retry, not on reconnect, not on
  restart.
- Paper journal blocking operates independently of Stage 1's REAL relock halt;
  `lock_trade` never clears journal failures.
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

### 2. Single executor process, worker-owned connections, and locking

- **Single executor process enforcement.** Exactly one server process writes to the
  journal. Enforced at startup using an advisory exclusive OS file lock
  (`fcntl.flock(LOCK_EX | LOCK_NB)`) on a dedicated lockfile (`execution.lock`) in the
  same directory as `execution.db`. If the lock cannot be acquired, startup fails
  closed immediately with an error indicating an active executor process already holds
  the journal.
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
- **Serialized execution region.** A service-level execution lock serializes the whole
  region from authoritative preparation through outcome handling: the blocked/gate
  check, the authoritative target-order read, the patch merge, the final safety
  assessment, the dispatch marker commit, the single SDK call, and the outcome commit.
  Only one SDK mutation invocation runs at a time, and no two operations can interleave
  between reading a target order and dispatching against it. No database transaction is
  held across the SDK call.
  - Token validation and existing-identifier lookup stay **outside** this region, so a
    duplicate-identifier retry answers promptly instead of queueing behind an in-flight
    broker round trip.
  - Decision 7 gives the full step order and the lost-update interleaving this scope
    prevents.

### 3. Storage engine, fsync semantics, and consistent backup procedures

Version 1 uses the rollback journal (`PRAGMA journal_mode = DELETE`) with
`PRAGMA synchronous = EXTRA`. Not WAL.

- **Why not WAL.** WAL's benefit is concurrent readers alongside a writer. There is
  one writer here and an almost idle read path, so there is nothing to win. Its cost
  is real: WAL adds `-wal` and `-shm` sidecar files, and a backup or restore that
  copies only `execution.db` can silently lose committed transactions still in the
  WAL. Operators back up and restore this volume by hand. A single self-contained
  file is the property worth having.
- **Why `EXTRA` and POSIX fsync semantics.** In rollback-journal mode `EXTRA` syncs
  the containing directory after the journal file is deleted, ensuring directory
  metadata persists across unexpected host crashes. Durability relies on standard
  POSIX filesystem semantics where `fsync` flushes dirty pages and directory entries
  to non-volatile media. Network filesystems or volume drivers that do not honour
  fsync are explicitly unsupported. No speculative latency or fsync-count claims are
  made.
- `PRAGMA foreign_keys = ON` for relational integrity.
- **Consistent backup procedures.** Backing up an active SQLite database by copying the
  database file directly is unsafe and unsupported. The supported methods are:
  1. **SQLite's own backup path**, on a running process — `VACUUM INTO '<backup_path>'`
     or `sqlite3.Connection.backup()`. This is the default recommendation.
  2. **An offline copy**, subject to the qualification below.
- **The lockfile being unheld does not mean the database is clean.** A crashed process
  releases its `flock` exactly as a clean shutdown does, so an unheld lockfile proves
  only that nothing is running — not that the database was closed consistently. A
  process that died mid-transaction leaves a **hot rollback journal**
  (`execution.db-journal`) which is *required* to bring the database back to a
  consistent state; SQLite warns that copying the database without it can produce an
  invalid copy that appears fine until it is read. An offline copy is therefore valid
  only when all of these hold:
  - the database is cleanly closed or already recovered — no hot journal is present,
    which in rollback-journal mode means opening it once with SQLite to let recovery
    run and complete, then confirming no `-journal` file remains;
  - no writer can start for the **entire** duration of the copy, not merely at the
    moment it begins;
  - if a hot journal is present and cannot be cleared, the journal file is copied
    together with the database, as a set, and restored as a set.

  Copying `execution.db` alone from a volume whose process crashed is the one
  procedure most likely to yield a silently corrupt journal, which is why it is called
  out rather than left implied.

### 4. Execution identity, admission epochs, and token evaluation order

The operator or deterministic caller establishes the **complete operation token**,
consisting of a caller-owned `operation_id` and a server-issued `admission_epoch`.
The server never generates, defaults, parses, or rewrites an identifier.

- The `operation_id` is opaque to the server: a non-empty, printable string within a
  bounded length (e.g., 1–64 characters). It is scoped to the journal database (and
  its bound paper environment and accounts). Once admitted, the ID is immutable.
- The `admission_epoch` is a fresh, unique token generated per server process start
  (e.g., a timestamped UUID stored in the journal's epochs table). The caller obtains
  the active epoch (via system health or admission query) and must preserve both
  `operation_id` and `admission_epoch` across retries.
- **Token evaluation order upon mutation submission:**
  1. **Lookup existing identifier first:** The store queries for `operation_id` across
     all recorded epochs.
     - If found:
       - Compare the canonical request (and caller patch).
       - If identical:
         - If current state is `DISPATCHING`: return an immediate bounded in-flight
           response (`IN_FLIGHT`), refusing concurrent dispatch without blocking.
         - If in any terminal or stored state (`ACKNOWLEDGED`, `UNKNOWN_OUTCOME`,
           `RECONCILED`, `REFUSED`, `TERMINAL_ACCOUNTED`): return the stored outcome.
           No further SDK call occurs.
       - If canonical request differs: refuse as a `CONFLICT`, naming the differing
         fields. Same-ID "corrected" requests are strictly refused.
  2. **Enforce current-epoch rule for new admission:**
     - If the `operation_id` is NOT found in the journal:
       - Verify the caller's supplied `admission_epoch` against the server's current
         fresh `admission_epoch`.
       - If the caller's epoch does not match the active epoch: **REFUSE**. An
         unknown identifier carrying a non-current epoch indicates a retry of an
         operation not present in this database instance (such as after an older
         backup restore).
       - **Never refresh or rewrite an old token to retry it.** Callers must not
         re-stamp an old operation ID with the new epoch to bypass the refusal.
       - If the epoch matches: Admit the operation as new in state `ADMITTED`.
- **Scope and limitations of restore detection:**
  This epoch check detects when an older restored backup is missing rows that were
  admitted in a later session, preventing their honest retries from being re-executed
  as new orders. However:
  - It does *not* detect all restores (e.g., if a caller generates fresh tokens, or
    if a restore happens without changing epochs, or if a snapshot restores both
    process memory and disk).
  - It does *not* recover missing history or rebuild lost journal rows.
  - Crucially: **The server SHALL NOT infer `NOT_SENT` from a restored pre-dispatch
    row without establishing journal continuity.** If a database was restored from
    backup, an `ADMITTED` row cannot be assumed to have never reached dispatch in
    the unrecorded period.

### 5. Canonicalization, frozen account binding, and strict decimal prices

The canonical request is a deterministic serialization of the fields defining the
operation: environment, account, operation type, and parameters. It is hashed into a
fingerprint, and stored alongside the canonical text for conflict diagnosis.

- **Frozen binding is the concrete account, resolved before persistence.** The
  trading environment and the **concrete broker account** are bound at admission. The
  placeholder `"0"` is never persisted as a binding: account resolution (Decision 7,
  Step 3) runs first, and only its concrete result is written. Persisting `"0"` and
  substituting the resolution afterwards would either mutate a binding described as
  immutable, or change the canonical request after it was fingerprinted.
  - A retry of a known identifier uses the **recorded** binding and does not resolve
    the account again. A later change to the eligible-account set therefore cannot
    retarget an admitted operation, nor turn an identical retry into a newly resolved
    request.
  - An admitted operation cannot be retried against a different account.
- **Strict decimal-string prices.** Paper mutation prices are accepted **exclusively**
  as decimal strings, and stored verbatim. Comparison uses decimal arithmetic.
  - A numeric (JSON number) price on a paper mutation is **refused**, not converted.
    There is no conversion path. By the time a tool sees a JSON number it is already a
    binary float and the caller's original text is gone, so "convert at the boundary"
    cannot be implemented as written; offering it as an alternative would license an
    implementation that silently reintroduces coercion artefacts.
  - Guarantees `350.0` and `350.00` evaluate to the same value, while `350.01` and
    `350.02` conflict, with zero binary float coercion artefacts.
  - A malformed decimal string is refused for the same reason.

### 6. Modification identity is target order ID plus the caller's patch

Stage 1 has `modify_order` fetch the existing order and assess the merged result.
The journal stores **two distinct records**:

- the caller's **target order ID and original patch** — the target order identifier,
  the modification action, and the fields actually supplied — which define the
  operation's identity and fingerprint;
- the **merged broker request** dispatched to OpenD, stored strictly for audit.

A price-only modification is identified by the target order ID and its price patch
alone. If a partial fill changes the order's remaining quantity between the initial
dispatch and a retry, the merged request changes but the patch does not, so the retry
is correctly recognized as the same operation.

### 7. Two-phase dispatch lifecycle, transition table, and crash behavior

The dispatch lifecycle separates identity resolution, intent admission, safety
validation and the broker dispatch marker, and serializes everything from
authoritative preparation through outcome handling:

1. **Step 1: Token and Schema Validation.** Outside any lock, validate the operation
   token's form and the request schema, including strict decimal-string prices.
   Nothing is persisted.
2. **Step 2: Existing-Identifier Lookup.** In a short read, look up the token across
   all epochs. A known token resolves here — stored state, `IN_FLIGHT`, or conflict —
   **using its recorded account binding**, never a fresh resolution. This step stays
   outside the service execution lock so an in-flight retry answers promptly even
   while another operation holds the lock.
3. **Step 3: Account Resolution (new operations only).** Resolve `acc_id` to a
   concrete broker account under the simulated-account allowlist and the
   exactly-one-eligible rule. A request that cannot resolve to exactly one concrete
   account is refused here, before anything is persisted.
4. **Step 4: Immutable Admission.** In a short `BEGIN IMMEDIATE` transaction, persist
   the operation in state `ADMITTED` with its canonical request, its patch, and the
   **concrete resolved account** as its frozen binding. `"0"` is never persisted as a
   binding.
5. **Step 5: Acquire the Service Execution Lock.** Everything from here to outcome
   handling runs under one lock, so no two operations interleave between reading a
   target order and dispatching against it.
6. **Step 6: Authoritative Preparation and Final Safety Assessment.** Under the lock:
   verify the journal is not blocked and the recovery gate is clear; read the target
   order authoritatively (for modification and cancellation); merge the caller's
   patch over that reading; and run the Stage 1 assessment — order value validation,
   price limits, notional caps — against the request that will actually be sent, plus
   the paper v1 scope check.
   - If ANY check fails: in a short transaction, transition `ADMITTED` → `REFUSED`
     with disposition `NOT_SENT` and record the reason. **No dispatch marker is ever
     committed.** Release the lock.
7. **Step 7: Dispatch Commitment.** Still under the lock, in a short transaction,
   commit `ADMITTED` → `DISPATCHING` (the dispatch marker), and close the transaction
   before initiating broker I/O.
8. **Step 8: Single SDK Mutation Invocation.** Execute exactly one SDK mutation
   invocation. No database transaction is open during broker I/O.
9. **Step 9: Outcome Handling,** then release the lock.

**Why the lock opens at Step 5 rather than Step 7.** A lock that starts at the
dispatch marker leaves the target-order read and merge unserialized, which permits a
lost update. Two operations modify the same order, initially quantity 10 at price 50:
A reads it and prepares a reduction to quantity 5; B reads the same snapshot and
prepares a price change to 80; A dispatches, leaving quantity 5 at price 50; B then
dispatches its already-merged request and restores quantity 10 at price 80. B was a
price-only modification, yet it silently undid A's quantity reduction — contradicting
the rule that omitted fields keep the existing order's values. Dispatching only B's
patch does not fix it either: the order that receives the patch is then no longer the
order that was assessed. The authoritative read, the merge and the assessment must sit
inside the same serialized region as the dispatch.

Step 2 deliberately stays outside that region. Duplicate-identifier lookups must not
queue behind an in-flight dispatch, or an honest retry would block for the length of a
broker round trip.
   In a new short transaction:
   - If gateway returns `RET_OK`: transition to `ACKNOWLEDGED` and record broker
     receipt (order ID).
   - If gateway returns an error, times out, or SDK raises: transition to
     `UNKNOWN_OUTCOME` with error details.
   - If broker acknowledged but local outcome persistence fails: report broker
     evidence separately from persistence failure (observed vs durably stored), and
     transition paper journal state to `JOURNAL_BLOCKED`.

#### State Transition Table

| Initial State | Event / Trigger | Target State | Local Disposition | Broker Marker Committed? |
| --- | --- | --- | --- | --- |
| None | Admission request (valid epoch, unknown ID, account resolved to exactly one concrete account) | `ADMITTED` | `PENDING_CHECKS` | No |
| `ADMITTED` | Safety check or limit check failure, under the execution lock | `REFUSED` | `NOT_SENT` | No |
| `ADMITTED` | Authoritative preparation and safety checks pass, under the execution lock | `DISPATCHING` | `IN_FLIGHT` | Yes |
| `DISPATCHING` | Gateway returns `RET_OK` + receipt committed | `ACKNOWLEDGED` | `ACKNOWLEDGED` | Yes |
| `DISPATCHING` | Gateway error code, SDK timeout, or network drop | `UNKNOWN_OUTCOME` | `POSSIBLY_SENT` | Yes |
| `DISPATCHING` | Gateway `RET_OK`, but outcome persistence fails | `UNKNOWN_OUTCOME` | `OBSERVED_NOT_STORED` | Yes |
| `DISPATCHING` | Process crash before outcome commit | `UNKNOWN_OUTCOME` | `POSSIBLY_SENT` (at restart) | Yes |
| `UNKNOWN_OUTCOME` | Automatic reconciliation with reliable broker ID | `RECONCILED` | `RECONCILED` | Yes |
| `UNKNOWN_OUTCOME` | Operator recovery acknowledgement with terminal proof | `TERMINAL_ACCOUNTED` | `OPERATOR_ACCOUNTED` | Yes |

#### Crash Windows and Recovery Semantics

1. **Crash between Admission (`ADMITTED`) and Dispatch Commit (`DISPATCHING`):**
   - No dispatch marker was committed.
   - *If journal continuity is verified* (same unbroken process run): The operation
     was never dispatched (`NOT_SENT`).
   - *If journal continuity is unverified* (e.g., restored database): The state
     cannot be presumed `NOT_SENT`; it must be evaluated under recovery review.
2. **Crash between Dispatch Commit (`DISPATCHING`) and SDK Invocation:**
   - The journal holds `DISPATCHING`. At startup recovery, this transitions to
     `UNKNOWN_OUTCOME` (reason `CRASH_IN_DISPATCH`). The operation is treated as
     possibly sent and blocks mutations until reconciled.
3. **Crash during SDK Invocation:**
   - The broker may or may not have received/executed the order. At restart, recovered
     as `UNKNOWN_OUTCOME`. Requires order query reconciliation or operator accounting.
4. **Crash between SDK Invocation and Outcome Commit:**
   - Broker may have acknowledged. At restart, recovered as `UNKNOWN_OUTCOME`.
     Reconciliation checks broker orders to locate the receipt.

### 8. Outcome classification and recovered lifecycle-state semantics

Local lifecycle states (`ADMITTED`, `DISPATCHING`, `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`,
`RECONCILED`, `REFUSED`, `TERMINAL_ACCOUNTED`) remain strictly distinct from broker
order statuses (`SUBMITTED`, `FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL`, `REJECTED`).

- An acknowledgement is never recorded as a fill.
- A gateway error or timeout is never recorded as a rejection.
- A partial fill is never collapsed into a complete fill or cancellation.
- Upon process restart, any operation left in `DISPATCHING` transitions to
  `UNKNOWN_OUTCOME` with a restart annotation, halting automated paper mutations.

### 9. Reconciliation: candidate matches vs reliable proof

An operation in `UNKNOWN_OUTCOME` is never automatically resubmitted, and never
resolved by placing a substitute or replacement order. Reconciliation is explicit.

- It queries **orders and history orders** for the journal-owned account. It must not
  use a deal query: Moomoo paper does not provide one.
- **Candidate Matching vs Proof of Ownership:**
  - Attribute/time matching (matching code, side, quantity, price within submission
    window) produces **candidates**, NOT proof of journal ownership.
  - Automatic association to `RECONCILED` requires **reliable broker identity** (a
    recorded broker order ID matching the broker's receipt) or **verified provider
    correlation** (a broker-returned client tag or unique remark matching the
    operation token).
  - If unrelated identical broker orders exist on the paper account (e.g., orders
    placed manually via the Moomoo app), attribute matching alone MUST NOT
    automatically adopt or resolve the journal operation.
- **Modifications and Cancellations:**
  - Finding the target order ID at the broker does **NOT** prove that a modification
    or cancellation mutation succeeded.
  - A cancellation finding a filled order indicates the cancel raced a fill, not that
    the cancel succeeded.
  - A modification finding an order with modified attributes requires verifying that
    the change originated from this journal mutation rather than an external action.
- Zero matches leaves the operation unresolved. Two or more matches leaves it
  unresolved.

### 10. Late local failures report evidence separately from error

When the broker acknowledged a request but something afterwards failed locally — the
receipt could not be converted, or the outcome could not be stored — the result
separates:

- **what the broker evidenced**: the acknowledgement, and the order identifier if
  readable;
- **what failed locally**: conversion or persistence, named as such;
- **how the submission state is held**: whether it was merely **observed** in memory
  or **durably stored**.

A late local failure never reports a clean success, never reports a safe-to-retry
error, and blocks subsequent automated paper mutations until resolved.

### 11. Storage lifecycle, admission epochs, and restore limitations

- **Never silently recreate.** Creating the journal is an explicit act gated by
  configuration. A missing file at a configured path fails closed.
- **Schema version** in `PRAGMA user_version`. Incompatible versions and failed
  integrity checks fail closed.
- **Restored backups with missing rows AND stale existing rows.**
  When an older backup is restored:
  - Missing rows (admitted after the backup was taken) carry old epochs and are
    unknown to the database; if retried, they are refused under the epoch rule.
  - Stale existing rows (which may have progressed to terminal states in real life but
    are stored as pre-terminal in the backup) are detected during startup recovery
    review, keeping the gate closed until reconciled against broker order history.
- **There is no rollback detector, and none is claimed.** The protection is not that
  the server recognizes a restore as such. Nothing inside a restored file can prove it
  was restored, because whatever the previous run recorded lives in the file that was
  replaced. The protection is the combination of two unconditional rules:
  - **every** process start requires recovery review, whether or not anything was
    restored; and
  - an unknown token carrying a non-current epoch is refused rather than admitted as
    new.

  Together these make a restore safe without detecting it. Any wording that implies the
  server compares a restored journal's epoch history against "what this process
  retired" describes a detector that does not exist and cannot be built from the data
  available, and is not part of this design.
- **Storage failure recovery.** If SQLite experiences I/O errors or lock exhaustion,
  the store marks itself in a failed state. Concurrent and subsequent mutations fail
  closed immediately without attempting broker I/O. Recovery requires restarting the
  process with healthy storage and completing recovery review.

### 12. Recovery review gate and operator recovery acknowledgement

**Every paper execution-process start requires recovery review before new
mutations.**

On start, the store enumerates operations outside terminal states. Until review has
run and each is accounted for, new mutations are refused; existing reconciliation and
reads stay available.

#### Named Operator Recovery Acknowledgement Mechanism

When automatic reconciliation cannot resolve an uncertain operation, it must be
resolved through an explicit, operator-only mechanism:

- **Entry point:** `acknowledge_recovery`, an operator-only administrative interface.
- **Authorization: a separate operator capability the trading agent does not hold.**
  Stage 1 establishes one bearer token for the HTTP transports (`MCP_AUTH_TOKEN`), and
  that token is what the trading agent presents. Recovery acknowledgement therefore
  requires a **distinct** operator credential — a separate secret
  (`MCP_OPERATOR_TOKEN`) that is never provisioned to the agent — and the interface is
  refused to any principal authenticated only by the agent's token.
  - A request bearing a **valid trading-agent token is refused**, whatever
    `operator_id`, `reason` and `evidence_reference` it supplies. Supplying a
    human-looking name in the arguments is not authorization; it is an argument.
  - The audited operator identity is **derived from the authenticated principal**. A
    supplied `operator_id` is accepted only if it matches that principal, and is
    otherwise refused rather than recorded. An audit trail whose identity is chosen by
    the caller records nothing worth keeping.
  - Where no separate operator credential is configured, the interface is
    unavailable — not open. Paper execution then stays blocked until one is
    configured, which is the fail-closed direction.
  - This is a missing security contract being specified, not a claim that an
    implemented bypass exists. No implementation exists yet.
- **Binding to what was reviewed:** an acknowledgement names, and is validated
  against, the **current recovery epoch** and the **operation state the operator
  observed**. If either has changed since the review — a later reconciliation moved
  the operation, or the process restarted into a new recovery epoch — the
  acknowledgement is refused and the operator reviews the current state. This stops a
  stale disposition from releasing a gate that has since changed meaning.
- **Required parameters:**
  - `operation_id`: target operation.
  - `operator_id`: must match the authenticated principal.
  - `recovery_epoch` and `observed_state`: what was reviewed.
  - `resolution`: `TERMINAL_ACCOUNTED` or `CONFIRMED_NOT_SENT`.
  - `reason`: durable human-readable justification.
  - `evidence_reference`: verifiable broker evidence reference.
  - the accounted facts below, for `TERMINAL_ACCOUNTED`.
- **Separation of mutation uncertainty from recovery disposition:**
  The system strictly forbids a generic "accept risk and mark reconciled" override.
  If a mutation's actual success at the broker is unproven, the journal does NOT mark
  the mutation as succeeded.
- **`TERMINAL_ACCOUNTED` is accounting, not closure.** A finished order and closed
  exposure are different facts, and the earlier wording conflated them: a buy order for
  100 shares that reaches `FILLED_ALL` is terminal, and the account now holds a
  100-share position. The order ended; the exposure did not.

  `TERMINAL_ACCOUNTED` therefore requires evidence-backed accounting of the target's:
  - final broker status;
  - **filled quantity** and average fill price;
  - **remaining executable quantity** (zero for a terminal order, recorded explicitly);
  - **resulting position or other account effects** attributable to the operation.

  It does **not** require the account to be flat. It requires an accurate record of
  what remains. The broker's order response reports filled quantity and average price
  separately from order status, so the recovery contract records them separately too.
- **No post-close absence shortcut.** The previous clause admitted "confirmed absent by
  end-of-day order history" without ever defining what establishes that confirmation.
  Reconciliation is explicit that an empty query is not proof of absence, and the
  recovery path must not become a back door around it. An absence disposition
  (`CONFIRMED_NOT_SENT`) requires provider-verified proof that is strictly stronger
  than "the order was not returned" — for example a broker-side statement or audit
  record that positively enumerates the account's orders for the session. A timestamp,
  a trading-close boundary, or an operator's assertion alone does not qualify, and an
  empty post-close history query alone leaves the operation unresolved.
  - Version 1 does not assume such proof exists. Task 1.6 establishes whether the paper
    provider offers any; until it does, `CONFIRMED_NOT_SENT` is unavailable and such
    operations stay unresolved.
- **Insufficient evidence keeps execution blocked.**
- **Durable Audit Record, committed before release:**
  Every acknowledgement is written to a dedicated `recovery_audit` table recording the
  operation ID, the authenticated operator identity, resolution, reason, evidence
  reference, the accounted facts, recovery epoch, observed and previous state, and
  timestamp. **The audit row and the disposition commit in the same transaction, and
  that transaction commits before the gate is re-evaluated.** A gate that could open
  before its justification is durable would lose exactly the record that explains why
  it opened.
- **Precise Gate-Release Conditions:**
  The recovery review gate is released if and only if EVERY operation in the journal
  is in a terminal state (`ACKNOWLEDGED`, `REFUSED`, `RECONCILED`, or
  `TERMINAL_ACCOUNTED`).

### 13. Independent paper-journal blocking and runtime failures

Paper execution journal blocking is an independent safety mechanism, completely
separate from Stage 1's `execution_halted` (REAL relock halt):

- Local paper journal states: `READY`, `REVIEW_PENDING`, `JOURNAL_BLOCKED`.
- When an operation enters `UNKNOWN_OUTCOME`, outcome persistence fails, or storage
  fails, the journal transitions to `JOURNAL_BLOCKED`.
- While `JOURNAL_BLOCKED`, all subsequent automated paper mutations are refused.
- **No unjournaled cancellation fallbacks:** Under no circumstances does the system
  fall back to unjournaled mutations. Even if an order requires cancellation, it must
  not bypass the journal.
- **`lock_trade` does NOT clear journal failures.** Stage 1's `lock_trade` resets the
  relock halt for REAL trading; it has no effect on paper journal blocking.
- **Release rules are per blocking reason.** "Reconciliation or operator review clears
  the block" is only true for the uncertainty reasons, and saying it unqualified
  contradicts Decision 11's requirement that storage failure needs a restart. The block
  records **why** it was entered, and only the matching release applies:

  | Blocking reason | Entered when | Released by |
  | --- | --- | --- |
  | `UNRESOLVED_OUTCOME` | an operation entered `UNKNOWN_OUTCOME` | successful reconciliation of that operation, or an authorized operator acknowledgement |
  | `OUTCOME_NOT_STORED` | the broker acknowledged but the outcome write failed | authorized operator acknowledgement only; reconciliation alone does not clear it, because the unstored fact is what is in doubt |
  | `STORAGE_FAILED` | SQLite I/O error, lock exhaustion, or integrity failure | **restarting the process with healthy storage, then completing recovery review.** Neither reconciliation nor an operator acknowledgement clears it in the running process |

  A successful reconciliation in a storage-failed process therefore clears nothing: the
  store cannot be trusted to have recorded it. Where several reasons are active, the
  block persists until every one of them is released.
- Health state precedence is defined in Decision 14, so a process that is both blocked
  and holding unreviewed operations reports one unambiguous state.
- `READ_ONLY` mode remains completely database-independent: no database file is
  opened, verified, or required.

### 14. Storage location, container deployment, and health reporting

- Optional execution directory on dedicated `execution-data` volume mounted at
  `/var/lib/moomoo-mcp/data`, owned by unprivileged uid 10001.
- `opend-data` is untouched, preserving session state and device authorization.
- Health reports journal state, schema version, active admission epoch, operation
  counts and storage reachability without issuing gateway calls, alongside Stage 1's
  `execution_halted`.
- **State precedence.** More than one condition can hold at once — an unresolved
  outcome both blocks the journal and leaves a non-terminal operation — so the reported
  state is the **first** match in this order, and it is always exactly one value:

  1. `DISABLED` — journaled paper execution is not configured.
  2. `JOURNAL_BLOCKED` — any blocking reason from Decision 13 is active.
  3. `REVIEW_PENDING` — the startup recovery gate has not been cleared.
  4. `READY` — the gate is clear and nothing is blocking.

  `JOURNAL_BLOCKED` outranks `REVIEW_PENDING` because it is the condition that needs
  an operator's attention first, and because its release rules are narrower.
- **Three different non-terminal populations, reported separately.** A single
  "non-terminal count" conflates normal operation with a fault. Health reports:
  - `in_flight` — operations in `DISPATCHING` right now. Expected during normal
    operation, and not on its own a reason for `REVIEW_PENDING`.
  - `awaiting_review` — non-terminal operations found at startup that the recovery gate
    is still holding.
  - `blocking` — operations whose state is a live blocking reason, with that reason.

  A `READY` journal may legitimately report a non-zero `in_flight`. It may not report a
  non-zero `awaiting_review` or `blocking`.

## Assumptions and limits

The guarantee — at most one application-level SDK mutation invocation per admitted
operation identifier — holds only under these assumptions:

1. The caller preserves the `(operation_id, admission_epoch)` token across retries.
2. The filesystem honours POSIX fsync semantics.
3. Exactly one server process writes to the journal at a time, enforced by the
   process lockfile.
4. The journal is not replaced, restored or modified underneath a running process.

What is **not** guaranteed:

- Exactly-once execution across SQLite and the broker.
- That reconciliation always resolves automatically. Zero matches or candidate-only
  matches leave the operation unresolved by design.
- Automatic restore detection for arbitrary restores (e.g., if fresh tokens are
  generated or process state is restored synchronously).
- Inferring `NOT_SENT` from an `ADMITTED` row after a restore without establishing
  journal continuity.
- Anything about REAL execution.

## Risks / Trade-offs

- **[Risk]** Unverified provider facts (DAY-only, deal query absence).
  → **Mitigation:** Task group 1 checks them before implementation.
- **[Risk]** Retention of terminal paper orders limits reconciliation window.
  → **Mitigation:** Candidate-only or zero matches safely leaves operation unresolved
  for operator review. Task 1.4 measures retention.
- **[Trade-off]** Recovery review gate requires attention on every restart.
  → **Mitigation:** Accepted and deliberate for a safety-first paper phase. Quiet
  restarts review to empty.
- **[Trade-off]** Refusing non-current epoch tokens refuses retries after restore.
  → **Mitigation:** Intended to prevent duplicate submissions when historical rows
  are missing.
- **[Risk]** Process lockfile stale after ungraceful container crash.
  → **Mitigation:** Advisory `fcntl.flock` locks are automatically released by the
  kernel when the process terminates.

## Migration Plan

1. Land `harden-trading-safeguards`.
2. Re-check delta specs against landed Stage 1 specs.
3. Run task group 1 against a real paper account to verify provider facts and resolve
   the GTC conflict.
4. Obtain separate authorization for implementation.
5. Add `execution-data` volume and configuration.
6. Run automated suites (`U01`–`U18`, `C01`–`C04`).
7. Obtain separate authorization for paper-provider verification (`M01`–`M04`).
8. Rollback: Redeploy previous image. Volume remains unmounted; `opend-data` intact.

## Validation status

Strict validation (`openspec validate add-paper-execution-journal --strict --json`)
validates structural correctness. Landed contracts and provider behaviors will be
verified in sequence.
