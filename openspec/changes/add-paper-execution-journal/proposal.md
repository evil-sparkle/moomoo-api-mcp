# Proposal: Add Paper Execution Journal

## Why

Before any real-money execution can be contemplated, automated trading requires an
execution infrastructure that guarantees reliable order identity, duplicate suppression,
and crash recovery. Network drops, gateway restarts, and client retries must never cause
unintended duplicate orders or unhandled execution states.

This change introduces a minimal, persistent, SQLite-backed execution journal for a
dedicated `SIMULATE` paper-trading phase. The goal is to build and verify robust execution
behavior and recovery under automated fault injection, while keeping the default
`READ_ONLY` deployment completely independent of database storage.

### Prerequisites and Dependency

- **Prerequisite**: `openspec/changes/harden-trading-safeguards` (currently pending in
  worktree `t3code/accdafb2`). That change repairs essential safety contracts: fail-closed
  order limits, explicit account routing, removal of startup auto-unlock, and a clear
  dispatch-boundary error taxonomy (`not sent`, `outcome unknown`, `acknowledged`).
- This change (`add-paper-execution-journal`) builds strictly on top of those safeguards
  contracts and does not duplicate them.
- Because `harden-trading-safeguards` is pending, this proposal records an explicit
  dependency: before implementation of this change begins, its delta specs must be
  re-checked and reconciled against the final specs landed by the safeguards change.

### Non-goals

- **REAL execution or REAL orders**: Live execution remains a separate future proposal
  requiring explicit operational approval. Passing paper tests SHALL NOT automatically enable
  REAL execution.
- **Telegram approval or button workflows**: Human confirmation / interactive approval
  mechanisms belong in a subsequent follow-on change.
- **General operator pause/resume and notification delivery**: An operator outbox or alerting
  pipeline belongs in a separate follow-on change. (A fail-closed execution halt caused by
  unresolved submissions or unavailable journal storage is included here, as recovery cannot
  be safe without it.)
- **Strategy generation or autonomous investment decisions**: The server remains an execution
  gateway for client-initiated operations.
- **External database servers**: No PostgreSQL, MySQL, Redis, or market-data warehouse.
- **Complex or conditional orders in v1**: Combo orders, market orders, stop orders, and
  trailing stop orders are excluded from v1 paper journaling and are explicitly refused
  in the journaled paper configuration.

## What Changes

- **Application-local SQLite Execution Journal**:
  - Introduce an `ExecutionStore` boundary wrapping Python's standard `sqlite3` library.
  - The database is initialized and used only when `MOOMOO_TRADING_MODE=SIMULATE`. Ordinary
    `READ_ONLY` mode does not initialize, open, or require a database.
  - Durable persistence of operations, normalized requests, submission attempts, state
    transitions, broker identifiers, and execution outcomes.
- **Durable Operation Identity and Duplicate Suppression**:
  - Every paper order mutation (`place_order`, `modify_order`, `cancel_order`) requires a
    caller-supplied `operation_id`. Missing identifiers fail explicitly.
  - Calling with an existing `operation_id` and an identical normalized request returns the
    stored outcome without resubmission.
  - Calling with an existing `operation_id` and different request parameters is rejected as a
    conflict.
  - Concurrent requests with the same `operation_id` are serialized using database-level
    concurrency control; exactly one attempt is permitted to dispatch.
- **Pre-Dispatch Journaling and Bounded Recovery**:
  - The operation must be committed to durable storage in state `PENDING_SUBMIT` before the
    gateway network call begins.
  - Database write transactions are NEVER held open during broker network calls.
  - Broker responses are recorded in the journal. Local states (`PENDING_SUBMIT`,
    `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED`, `FAILED`) remain distinct from broker order
    states (`SUBMITTED`, `FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL`, `REJECTED`).
  - Operations left in `UNKNOWN_OUTCOME` (e.g. after a timeout, gateway disconnect, or crash)
    are NEVER automatically resubmitted. They are resolved via explicit reconciliation queries.
    Ambiguous reconciliation leaves the operation unresolved and halts further automated
    mutations on the account until operator review.
- **Strict Simulation Account Isolation**:
  - SIMULATE paper execution requires an explicitly configured `MOOMOO_SIMULATE_ACC_IDS`
    allowlist.
  - Requests targeting REAL or an un-allowlisted account are refused before contacting the
    gateway.
  - Requests targeting REAL are NEVER silently rewritten to SIMULATE.
  - The agent cannot enable REAL trading via any tool parameter.
  - Future REAL execution will require separate storage; paper records can never be promoted
    into live execution records.
- **Version 1 Paper Execution Scope**:
  - Paper execution in v1 supports single-leg stock and ETF limit orders (`NORMAL`), limit
    modifications, and cancellations.
  - Unsupported mutations (combo orders, market orders, stop orders, trailing stop orders)
    are explicitly refused in the journaled paper configuration rather than bypassing the
    journal.
- **Storage Failure and Integrity Protection**:
  - If journal storage cannot be written before dispatch, the mutation is refused (fail-closed).
  - If storage fails after dispatch, uncertainty is preserved and further mutations are halted;
    it is never reported as a safe-to-retry failure.
  - Missing, corrupt, or incompatible journal storage fails closed on startup; it does not
    silently create an empty database that forgets duplicate-protection history.
- **Container Deployment Isolation**:
  - A dedicated persistent Docker volume (`execution-data`) mounted at `/var/lib/moomoo-mcp/data`,
    owned by uid 10001, distinct from the `opend-data` device authorization volume.
  - Container recreation preserves the execution journal without disturbing OpenD authorization.
- **Health Diagnostics**:
  - `check_health` exposes the execution store state (healthy, degraded, halted, journal path,
    unresolved operations count) while keeping read-only queries usable if the store is degraded.

## Capabilities

### New Capabilities

- `execution-journal`: Persistent execution store for order operations, durable operation
  identity, duplicate suppression, pre-dispatch state transitions, post-dispatch outcome
  recording, and reconciliation workflow.

### Modified Capabilities

- `trading-policy`: Adds `MOOMOO_SIMULATE_ACC_IDS` allowlist validation for SIMULATE mode,
  enforces that READ_ONLY operation does not require or open the database, refuses REAL writes
  without silent rewrites, and ensures paper execution records cannot be promoted to live.
- `order-placement`: Requires `operation_id` for paper order placement; restricts v1 paper
  execution to stock/ETF limit orders (`NORMAL`); explicitly refuses unsupported order types
  (market, stop, trailing stop) in journaled SIMULATE mode; routes placement through
  `ExecutionStore` with pre-dispatch commitment and duplicate suppression.
- `order-modification`: Requires `operation_id` for paper order modification and cancellation;
  routes modifications and cancellations through `ExecutionStore`; ensures racing fills do not
  falsely report successful cancellation.
- `configuration`: Adds configuration variables `MOOMOO_SIMULATE_ACC_IDS`,
  `MOOMOO_EXECUTION_DB_PATH`, and `MOOMOO_EXECUTION_BUSY_TIMEOUT_MS`.
- `container-deployment`: Adds persistent Docker volume `execution-data` separate from
  `opend-data`, sets directory permissions for non-root user (uid 10001), and specifies
  container recreation resilience and backup/restore procedures.
- `system-health`: Extends health reporting with execution journal status (read/write access,
  schema version, unresolved operations, execution state).

## Impact

- **Services**:
  - New `src/moomoo_mcp/services/execution_store.py`: SQLite connection management, schema
    migrations, atomic reservation, state transitions, duplicate detection, and queries.
  - `src/moomoo_mcp/services/trading_policy.py`: SIMULATE account allowlisting and environment
    enforcement.
  - `src/moomoo_mcp/services/trade_service.py`: Integration with `ExecutionStore` for
    pre-dispatch logging, receipt capture, reconciliation queries, and execution halting.
  - `src/moomoo_mcp/services/health.py` and `base_service.py`: Execution store diagnostic probes.
- **Tools**:
  - `src/moomoo_mcp/tools/trading.py`: Accept `operation_id` on `place_order`, `modify_order`,
    and `cancel_order`; reject unsupported operations in SIMULATE mode; pass operation metadata
    to `trade_service`.
  - `src/moomoo_mcp/tools/system.py`: Surface execution journal health diagnostics.
- **Deployment & Configuration**:
  - `docker-compose.yml`: Declare `execution-data` named volume mounted to container storage.
  - `.env.example`: Document `MOOMOO_SIMULATE_ACC_IDS`, `MOOMOO_EXECUTION_DB_PATH`,
    `MOOMOO_EXECUTION_BUSY_TIMEOUT_MS`.
  - `docs/state-and-restarts.md` and `docs/deploy-vps.md`: Document journal lifecycle, backup,
    restore, and reconciliation runbooks.
- **Dependencies**:
  - Standard library `sqlite3` only. No external database drivers or services.
- **Testing**:
  - Unit tests with in-memory / temporary SQLite databases.
  - Integration tests with stateful fake broker and automated fault injection (crashes before
    dispatch, dropped responses, post-dispatch storage errors, concurrent duplicate submissions).
  - Dedicated container persistence and backup restoration tests.
  - Staged manual paper validation plan requiring separate operator authorization.
