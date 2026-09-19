# Design: Paper Execution Journal

## Context

The server currently operates in `READ_ONLY` mode by default, enforcing all restrictions
in `TradingPolicy` and `TradeService` before OpenD gateway calls. While read-only operations
require no local database, automating order placement in `SIMULATE` paper trading introduces
concurrency, network uncertainty, client retries, and failure modes across process and network
boundaries.

Existing tools offload blocking calls via `run_blocking` (thread pool) while serving stateless
Streamable HTTP requests. Gateway connections are process-owned. OpenD credentials and device
tokens reside in `opend-data`. The execution journal must integrate with this architecture
without disrupting stateless transport, process-owned connections, or read-only deployments.

Prerequisite contracts from `harden-trading-safeguards` establish fail-closed limits,
required `trd_env`, account allowlisting, and dispatch-boundary error reporting. This design
builds directly upon those contracts.

## Goals / Non-Goals

**Goals:**
- Provide application-local persistent execution storage (`ExecutionStore`) using Python's
  built-in `sqlite3`.
- Enforce strict duplicate prevention and idempotency based on caller-supplied `operation_id`.
- Commit pre-dispatch intent to disk before calling the broker gateway.
- Ensure database write transactions are never held across broker network calls.
- Distinguish local lifecycle states from authoritative broker statuses.
- Handle storage and network failures fail-closed, preserving uncertainty rather than reporting
  misleading failures or allowing unsafe retries.
- Isolate execution storage to a dedicated Docker volume (`execution-data`), separate from OpenD's
  `opend-data`.
- Keep `READ_ONLY` deployments entirely free of database dependencies.

**Non-Goals:**
- REAL trading execution or storage (reserved for a distinct future proposal).
- Telegram approvals or interactive human-in-the-loop workflows.
- External database engines (Postgres, MySQL, Redis).
- Exposing raw SQL or file handles to MCP tools or agents.
- Automatic resubmission or speculative replay of unresolved operations.
- Complex multi-leg combos or algorithmic order strategies in version 1.

## Decisions

### Decision 1: SQLite via `sqlite3` Encapsulated in `ExecutionStore`

- **Choice**: Use Python standard library `sqlite3` encapsulated behind an `ExecutionStore`
  service interface. MCP tools call `trade_service`, which delegates to `ExecutionStore`.
  No SQL queries or connection handles are exposed to MCP tools or agents.
- **Rationale**: Python's `sqlite3` is self-contained, requires no external daemon, and is
  ideal for application-local single-node persistence. Encapsulating behind `ExecutionStore`
  enables clean unit testing with in-memory SQLite instances (`:memory:`) and avoids polluting
  the tool layer with database mechanics.
- **Alternatives Considered**:
  - *PostgreSQL / MySQL*: Adds operational complexity, external network dependencies, and
    failure modes unjustified for single-tenant VPS deployment.
  - *File-based JSON / Pickle log*: Lacks ACID transactions, atomic row locks, and crash
    durability guarantees.
  - *SQLAlchemy / ORM*: Unnecessary heavyweight dependency; standard `sqlite3` with simple,
    explicit SQL is easily auditable, fast, and dependency-free.

### Decision 2: Schema Design, Uniqueness Constraints, and Request Normalization

- **Schema Structure**:
  - `journal_meta`: Key-value metadata table (`schema_version`, `created_at`, `environment`).
  - `execution_operations`:
    - `operation_id` (TEXT PRIMARY KEY): Stable caller-provided identifier.
    - `account_id` (TEXT NOT NULL): Target simulated account.
    - `trd_env` (TEXT NOT NULL): Execution environment (strictly `'SIMULATE'`).
    - `operation_type` (TEXT NOT NULL): `'PLACE_ORDER'`, `'MODIFY_ORDER'`, `'CANCEL_ORDER'`.
    - `request_fingerprint` (TEXT NOT NULL): SHA-256 hash of normalized request parameters.
    - `request_payload` (TEXT NOT NULL): JSON-encoded canonical parameters (code, side, qty, price, type, tif).
    - `local_state` (TEXT NOT NULL): Lifecycle state (`INITIATED`, `PENDING_SUBMIT`, `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED`, `FAILED`).
    - `broker_order_id` (TEXT): Broker-assigned order ID (nullable).
    - `broker_status` (TEXT): Authoritative status reported by broker (nullable).
    - `error_category` (TEXT): `NONE`, `CLIENT_ERROR`, `BROKER_REJECTED`, `NETWORK_TIMEOUT`, `STORAGE_ERROR`.
    - `error_detail` (TEXT): Diagnostic message or gateway error string.
    - `created_at` (TEXT NOT NULL): ISO-8601 UTC timestamp.
    - `updated_at` (TEXT NOT NULL): ISO-8601 UTC timestamp.
  - `execution_transitions`:
    - Audit log recording each state transition with timestamp, previous state, new state, and reason.
- **Normalization and Fingerprinting**:
  - The request payload is canonicalized (keys sorted, whitespace stripped, floats formatted consistently)
    and hashed into `request_fingerprint`.
  - When an `operation_id` is re-submitted:
    - If `request_fingerprint` matches: return existing operation state / stored receipt.
    - If `request_fingerprint` differs: raise conflict error (`409 Conflict`), refusing execution.
- **Alternatives Considered**:
  - *Server-generated UUIDs*: If the server generates the ID, network drops before response
    delivery cause the client to retry with a new ID, completely defeating duplicate prevention.
    The caller/agent must own the `operation_id`.

### Decision 3: Transaction Boundaries and Broker Call Decoupling

- **Rule**: A database write transaction must NEVER remain open across an OpenD network call.
- **Lifecycle Flow**:
  1. *Step 1 (Pre-Dispatch Commit)*: In an immediate SQLite transaction, insert the operation in state
     `PENDING_SUBMIT`. The transaction commits to disk (WAL frame synced).
  2. *Step 2 (Gateway Network Call)*: Call OpenD gateway (`trade_ctx.place_order`). No database lock is held.
  3. *Step 3 (Post-Dispatch Commit)*:
     - On gateway success: Update operation to `ACKNOWLEDGED`, record `broker_order_id`, and commit.
     - On gateway definite rejection: Update operation to `FAILED` (`BROKER_REJECTED`) and commit.
     - On gateway timeout / network disconnect: Update operation to `UNKNOWN_OUTCOME` (`NETWORK_TIMEOUT`)
       and commit.
     - If Step 3 database write fails: Flag execution engine as `HALTED`, preserve in-memory receipt, and
       log critical alert.
- **Rationale**: Holding an open database transaction across network I/O risks indefinite locks, SQLite
  `busy` errors, and deadlock if network latency spikes.

### Decision 4: Concurrency Control and Thread Ownership with `run_blocking`

- **Threading Model**:
  - `run_blocking` runs functions in `anyio.to_thread.run_sync`, dispatching to worker threads.
  - Python's `sqlite3.Connection` objects cannot be safely used across threads concurrently.
  - `ExecutionStore` will manage per-thread connections (via `threading.local` or a bounded connection
    pool) or instantiate connections with `check_same_thread=False` protected by an internal
    `threading.Lock()`.
- **Busy Timeout**:
  - All connections configure `PRAGMA busy_timeout = 5000` (5 seconds, configurable via
    `MOOMOO_EXECUTION_BUSY_TIMEOUT_MS`).
  - If a transaction cannot obtain a lock within 5 seconds, it fails closed with `STORAGE_ERROR`,
    never blocking the event loop indefinitely.

### Decision 5: Durability Settings and WAL Mode

- **SQLite PRAGMAs**:
  - `PRAGMA journal_mode = WAL;` (Write-Ahead Logging): Allows concurrent readers and single writer,
    significantly reducing contention.
  - `PRAGMA synchronous = NORMAL;` (or `FULL` for pre-dispatch commit): In WAL mode, `NORMAL`
    synchronizes WAL frames at checkpoints; `FULL` syncs on each commit. To guarantee pre-dispatch
    durability across power/OS crashes, `synchronous = FULL` is preferred during the pre-dispatch
    record.
  - `PRAGMA foreign_keys = ON;`: Enforces referential integrity between operations and transitions.

### Decision 6: Schema Migrations and Fail-Closed Initialization

- **Versioning via `PRAGMA user_version`**:
  - The database stores an integer schema version in `PRAGMA user_version`.
  - Target version for this change: `1`.
- **Startup Integrity Checks**:
  - If database file does not exist:
    - In fresh deployment: Automatically initialize schema version 1.
    - If configured path is expected to exist (e.g. flag `MOOMOO_EXECUTION_REQUIRE_EXISTING=1`), fail
      startup to prevent silent creation of empty storage that drops duplicate history.
  - If database file exists:
    - If `user_version == 1`: Accept.
    - If `user_version < 1`: Run forward migrations.
    - If `user_version > 1`: Fail closed! Newer schema cannot be safely read by an older server binary.
  - If database is corrupt: `PRAGMA quick_check;` fails -> server refuses to start in `SIMULATE` mode.

### Decision 7: Container Persistence and Docker Volume Separation

- **Volume Topology**:
  - OpenD state: `${OPEND_DATA_DIR:-opend-data}:/home/opend/.com.moomoo.OpenD`
  - Execution journal state: `execution-data:/var/lib/moomoo-mcp/data`
- **Isolation**:
  - Completely separate volumes ensure OpenD SMS/device authorizations and trading journal records
    can be backed up, restored, or wiped independently.
  - Directory `/var/lib/moomoo-mcp/data` is created in `Dockerfile` and owned by `uid 10001:gid 10001`.
- **Backup and Restore**:
  - SQLite online backup API (`sqlite3.Connection.backup`) or `sqlite3 /var/lib/moomoo-mcp/data/execution.db ".backup '/backup/execution.db'"` produces a consistent snapshot.
  - Restoring an older backup introduces risk of missing operations that were actually executed at the broker.
    Therefore, restoring a backup requires an explicit operator reconciliation flag and refuses automated
    mutations until verified.

### Decision 8: Reconciliation Strategy and Halt Policy

- **Outcome Uncertainty**:
  - An operation in `UNKNOWN_OUTCOME` represents an unconfirmed network state.
  - Resubmission is strictly prohibited.
  - The reconciliation routine executes `get_orders` and `get_deals` for the account.
  - Matching criteria:
    - Match by `broker_order_id` if known.
    - If `broker_order_id` is unknown, match by order attributes (code, side, qty, price, order type,
      submission time window, and remark if supported).
  - If exactly one broker order matches: adopt its broker status, link `broker_order_id`, and mark `RECONCILED`.
  - If zero or multiple ambiguous orders match: keep `UNKNOWN_OUTCOME`, do not guess, and transition
    execution state to `HALTED`.
- **Execution Halt**:
  - When in `HALTED` state:
    - New placements and modifications are rejected.
    - Cancellations and read-only queries remain permitted.
    - Recovery requires operator resolution or explicit reconciliation.

### Decision 9: Extension Points for Future Phases

- **Telegram Approvals (Stage 3)**:
  - `execution_operations` table includes a nullable `approval_id` / `approval_state` column. In this
    change, it defaults to `NOT_REQUIRED`. Future changes can enforce required approval before transition
    to `PENDING_SUBMIT`.
- **Operator Notifications and Outbox (Stage 4)**:
  - An `outbox_events` table can hook into state transitions (`UNKNOWN_OUTCOME`, `HALTED`) without
    modifying the core execution journal schema.

## Risks / Trade-offs

- **[Risk] Database Lock Contention under Concurrency**
  → *Mitigation*: Enable WAL mode (`PRAGMA journal_mode=WAL`), set `PRAGMA busy_timeout=5000`, and
  ensure database transactions are strictly scoped to microseconds around inserts/updates, never
  held across network calls.
- **[Risk] Network Drop After Broker Acceptance**
  → *Mitigation*: The operation is persisted in `PENDING_SUBMIT` prior to the call. If the connection
  drops, it transitions to `UNKNOWN_OUTCOME`. Automated reconciliation queries verify whether the
  order exists at the broker before any further action.
- **[Risk] Disk Full or Database Unwritable Mid-Flight**
  → *Mitigation*: If storage fails before dispatch, the mutation is rejected cleanly and not sent.
  If storage fails after dispatch, the execution engine halts, records the in-memory outcome in logs,
  and refuses new mutations until storage is restored.
- **[Risk] Stale Backup Restored Overwrite**
  → *Mitigation*: Restored database timestamps and operation sequences are verified against the
  broker's daily order/deal list upon startup before resuming execution.
- **[Risk] Silent Promotion to Real Trading**
  → *Mitigation*: The execution database schema explicitly records `trd_env='SIMULATE'`. The trading
  policy strictly blocks `REAL` operations in `SIMULATE` mode and ensures future live trading will
  use an isolated, distinct live store.

## Migration Plan

1. **Docker Compose & Deployment**:
   - Add `execution-data` named volume to `docker-compose.yml`.
   - Update `Dockerfile` to create `/var/lib/moomoo-mcp/data` with ownership `10001:10001`.
2. **Configuration Updates**:
   - Add `MOOMOO_SIMULATE_ACC_IDS` to deployment environment for `SIMULATE` mode.
   - Existing `READ_ONLY` deployments require no environment or volume changes and continue running
     without initializing a database.
3. **Rollback Strategy**:
   - If rolled back to the previous image, the `execution-data` volume is simply unmounted.
   - The `opend-data` authorization volume remains untouched and intact throughout.

## Open Questions

- *OpenD Remark Propagation in SIMULATE*: Does Moomoo OpenD reliably preserve and return user remarks
  in `order_list_query` in paper trading across all markets (US, HK)?
  *Handling in this plan*: We do not assume remark is a broker-enforced uniqueness key. Matching in
  reconciliation relies on exact attribute matching and timestamps, with remark used as secondary
  corroboration where available.
