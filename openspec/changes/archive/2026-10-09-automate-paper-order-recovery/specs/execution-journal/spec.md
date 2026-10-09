# Spec Delta

## REMOVED Requirements

### Requirement: Recovery Review Gate and Operator Acknowledgement

**Reason**: The service owns recovery and applies audited evidence or the narrowly
defined paper placement absence policy without a separate operator capability.

**Migration**: Existing audit entries remain readable. Use `get_execution`,
`reconcile_execution`, and `check_health` with the normal MCP credential. Legacy
operations without a broker ID or persisted correlation tag remain blocked.

## MODIFIED Requirements

### Requirement: Bounded Reconciliation of Journal-Owned Orders

The system SHALL reconcile journal-owned operations using fresh current orders,
history orders and position evidence. It SHALL never dispatch an order mutation
during reconciliation. Recovery disposition SHALL remain distinct from mutation
success and SHALL be durably audited before any recovery block is released.

#### Scenario: Journal reconciliation ownership contract
- **WHEN** automatic recovery or `reconcile_execution` checks an operation
- **THEN** it SHALL query current orders with cache refresh and history orders for
  the exact recorded SIMULATE account
- **AND** it SHALL not issue paper deal queries
- **AND** it SHALL require the recorded broker order ID or exact persisted tag
- **AND** attribute and time matching alone SHALL not establish ownership

#### Scenario: A unique match with reliable broker identity resolves the operation
- **WHEN** current and historical copies identify one broker order with the recorded
  ID or tag and consistent symbol, side, quantity, price and status
- **THEN** copies SHALL be deduplicated by broker order ID
- **AND** recovery SHALL record its identity, status, fills and current symbol position
- **AND** it SHALL atomically audit recovery and remove that operation's review reasons
- **AND** an open order SHALL not need to become terminal before recovery completes

#### Scenario: Attribute and time matches are candidates, not proof of ownership
- **WHEN** a broker order matches symbol, side, quantity and price but no reliable
  identity or correlation is recorded
- **THEN** it SHALL not be adopted into the journal

#### Scenario: Ambiguous matches do not resolve the operation
- **WHEN** one tag identifies multiple broker IDs or current and historical copies
  disagree, or the matching request fields disagree
- **THEN** recovery SHALL remain blocked and SHALL not count a clean negative round

#### Scenario: Finding modification target order does not prove modification succeeded
- **WHEN** an uncertain modification's identified target matches the requested
  quantity and price, or the target has become terminal
- **THEN** recovery SHALL account for the observed target and current position
- **AND** it SHALL not assert that this modification caused the observed state
- **AND** an active target that does not match SHALL remain pending

#### Scenario: Finding cancellation target order does not prove cancellation succeeded
- **WHEN** an uncertain cancellation's identified target is terminal
- **THEN** recovery SHALL record `TERMINAL_ACCOUNTED`, filled and remaining quantity,
  average fill price and current symbol position
- **AND** it SHALL not assert that the cancellation succeeded or exposure is closed
- **AND** a nonterminal target SHALL remain pending

#### Scenario: An empty result does not resolve the operation
- **WHEN** both required order queries complete successfully with no owned match
- **THEN** recovery SHALL record a clean negative round with actual timestamps
- **AND** it SHALL not record proof that an order never existed
- **AND** failed queries, disconnects and malformed evidence SHALL not count as negatives

#### Scenario: Unrelated identical broker orders do not resolve the operation
- **WHEN** identical symbol/side/quantity/price orders have no recorded identity or tag
- **THEN** they SHALL not be adopted as the journal operation's order

#### Scenario: Reconciliation does not use deal records
- **WHEN** paper reconciliation runs
- **THEN** it SHALL not query or depend on unavailable broker deal records

#### Scenario: Reconciliation sends no order mutation
- **WHEN** reconciliation runs for any operation
- **THEN** it SHALL not place, modify or cancel broker orders

#### Scenario: Orders the journal does not own are not reconciled
- **WHEN** broker queries return orders not identified by a recorded ID or tag
- **THEN** those orders SHALL not be adopted by the journal or resolve an operation

### Requirement: Outcome Classification and Preservation of Uncertainty

The journal SHALL record the outcome of each dispatched operation using the dispatch
boundary defined by `order-placement` › Report the Dispatch Boundary.

#### Scenario: Dispatch outcome classification contract

- **WHEN** a dispatched operation returns or loses its response
- **THEN** the following detailed obligations SHALL hold:

Local lifecycle states SHALL remain distinct from broker order statuses. Local states
are `ADMITTED`, `DISPATCHING`, `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED`,
`REFUSED`, and `TERMINAL_ACCOUNTED`. Broker statuses are those the provider reports,
such as `SUBMITTED`, `FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL` and `REJECTED`.

- An acknowledgement SHALL NOT be recorded or reported as a fill.
- An unknown outcome SHALL NOT be recorded or reported as a rejection.
- An operation in `UNKNOWN_OUTCOME` SHALL NEVER be automatically resubmitted, and
  SHALL NEVER be resolved by dispatching a substitute or replacement order.
- Dispatched operations with unknown outcomes or failed outcome persistence SHALL
  block subsequent automated paper mutations (`JOURNAL_BLOCKED`).
- Journal blocking SHALL operate independently of Stage 1's REAL relock halt
  (`ARMED`/`HALTED`). `lock_trade` SHALL NOT clear paper journal failures.
- Journal blocking SHALL record the reason it was entered, and SHALL be released only
  by the release that matches that reason:
  - an unresolved outcome recorded durably **in this process** is released by
    automatic recovery that accounts for broker evidence or applies the narrowly
    defined Tagged Paper Placement Absence Policy;
  - a failed outcome write keeps the running process blocked until a healthy storage
    restart and automatic recovery accounts for the operation;
  - a dispatch marker recovered at startup with no durable outcome is released only
    by a durably audited automatic recovery decision for that operation;
  - a storage failure is released only by restarting the process with healthy storage
    and completing recovery review. Reconciliation SHALL NOT clear it in the running
    process, because the store cannot be relied on to have recorded the result.

  Where several reasons are active, blocking SHALL persist until every one is
  released.
- When journal blocking is active, order mutations SHALL NOT fall back to
  unjournaled execution. In particular, unjournaled cancellation fallbacks are
  strictly forbidden.

#### Scenario: Acknowledgement is recorded as acknowledged, not filled

- **WHEN** the gateway acknowledges a journaled placement
- **THEN** the journal SHALL record the local state `ACKNOWLEDGED`
- **AND** SHALL NOT record or report the order as filled

#### Scenario: Gateway error code is recorded as unknown, not rejected

- **WHEN** the gateway returns an error code for a journaled mutation
- **THEN** the journal SHALL record the local state `UNKNOWN_OUTCOME`
- **AND** SHALL NOT record the operation as rejected by the broker

#### Scenario: Timeout or dropped response is recorded as unknown

- **WHEN** the SDK raises, times out, or the response is lost during a journaled
  mutation
- **THEN** the journal SHALL record `UNKNOWN_OUTCOME`
- **AND** the caller SHALL be told the outcome is unknown and must be reconciled

#### Scenario: An unresolved operation is never replayed automatically

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** the caller retries it, the gateway reconnects, or the process restarts
- **THEN** the system SHALL NOT resubmit the operation
- **AND** SHALL NOT dispatch a substitute or replacement order for it

#### Scenario: Broker status is recorded separately from local state

- **GIVEN** an operation reached the local state `RECONCILED`
- **WHEN** the broker reported a status for the matching order
- **THEN** the journal SHALL record that broker status as a distinct field
- **AND** the local state SHALL NOT be overwritten by it

#### Scenario: Fills and cancellations remain mutually distinct

- **WHEN** the broker reports a partial fill, a complete fill, or a cancellation for
  a journal-owned order
- **THEN** the journal SHALL record each as a distinct broker status
- **AND** SHALL NOT collapse a partial fill into a complete fill or a cancellation

#### Scenario: Dispatched uncertainty blocks subsequent automated paper mutations

- **GIVEN** an operation enters `UNKNOWN_OUTCOME` and its recovery block has not
  been released by a durably audited automatic recovery decision
- **WHEN** a subsequent paper mutation is requested
- **THEN** the system SHALL refuse the new mutation
- **AND** the refusal SHALL state that paper execution is blocked by unresolved
  operations

#### Scenario: Unjournaled cancellation fallback is forbidden when journal is blocked

- **GIVEN** paper execution is blocked due to an unresolved operation or storage error
- **WHEN** a cancellation request is received
- **THEN** the system SHALL refuse the cancellation
- **AND** SHALL NOT dispatch the cancellation unjournaled

#### Scenario: Reconciliation does not clear a storage-failed journal

- **GIVEN** journal blocking was entered because of a storage failure
- **WHEN** a reconciliation of an operation completes successfully in that process
- **THEN** journal blocking SHALL remain in force
- **AND** release SHALL require restarting with healthy storage and completing
  recovery review

#### Scenario: A failed outcome write is not released by reconciliation alone

- **GIVEN** journal blocking was entered because the broker acknowledged a mutation but
  the outcome write failed
- **WHEN** reconciliation runs in that storage-failed process
- **THEN** journal blocking SHALL remain in force
- **AND** release SHALL require a healthy storage restart and a durably audited
  automatic recovery decision for the operation

#### Scenario: Journal blocking is independent of trade relock halt and not cleared by lock_trade

- **GIVEN** paper execution is in state `JOURNAL_BLOCKED`
- **WHEN** `lock_trade` is called
- **THEN** `lock_trade` SHALL NOT clear paper journal blocking
- **AND** subsequent paper mutations SHALL remain blocked

### Requirement: Storage Lifecycle and Admission Epochs

The execution store SHALL verify its storage before serving mutations, enforce
single-process execution, and SHALL NEVER silently recreate missing storage.

#### Scenario: Journal storage lifecycle contract

- **WHEN** the execution store opens, restarts, or restores storage
- **THEN** the following detailed obligations SHALL hold:

- Creating the journal SHALL be an explicit, configured act. A missing file at a
  configured path SHALL fail closed.
- Exactly one executor process SHALL be permitted to access the journal. Enforced at
  startup using an exclusive non-blocking OS lock on `execution.lock`. If locked,
  startup SHALL fail closed immediately.
- The schema version SHALL be recorded. A version newer than the running binary
  understands SHALL fail closed without modifying the file. A failed integrity check
  SHALL fail closed.
- The store SHALL record a fresh **admission epoch** at each process start.
- The guarantee's scope and limitations:
  - Epoch enforcement refuses unknown non-current-epoch tokens. It does not detect
    all restores (such as when fresh tokens are generated).
  - Restore handling SHALL be understood as exactly two protections: refusing an
    unchanged retry of a token the storage no longer holds, and surfacing for review
    the non-terminal rows the storage still holds. It SHALL NOT be described as
    making a restore safe.
  - Neither protection accounts for history that is entirely absent. An operation
    admitted after a backup was taken leaves no row to review, and a genuinely new
    token SHALL still be admitted, so a restore MAY leave broker-side effects
    unaccounted for.
  - The system SHALL NOT recover missing history from restored backups.
  - The system SHALL NOT infer `NOT_SENT` from a restored pre-dispatch (`ADMITTED`)
    row without establishing verified journal continuity.
- Storage failures during runtime SHALL fail closed immediately. Storage recovery
  SHALL require restarting the server with healthy storage and completing recovery
  review.

#### Scenario: Missing storage is not silently recreated

- **GIVEN** a journal path is configured and no database file exists there
- **WHEN** the server starts for journaled paper execution
- **THEN** startup SHALL fail closed with an error naming the path
- **AND** SHALL NOT create an empty journal in its place

#### Scenario: Explicit initialization creates the journal

- **GIVEN** journal creation is explicitly requested by configuration
- **WHEN** the server starts with no database file at the configured path
- **THEN** it SHALL create the journal and record its schema version and a new
  admission epoch

#### Scenario: A newer schema version fails closed

- **GIVEN** the journal records a schema version newer than the running binary
  understands
- **WHEN** the server starts
- **THEN** startup SHALL fail with a schema incompatibility error
- **AND** the file SHALL NOT be modified

#### Scenario: A failed integrity check fails closed

- **GIVEN** the journal file fails its integrity check
- **WHEN** the server starts for journaled paper execution
- **THEN** startup SHALL fail closed
- **AND** SHALL NOT serve mutations

#### Scenario: Single executor process is enforced by process file lock

- **GIVEN** the server starts and acquires the exclusive lock on `execution.lock`
- **WHEN** another process attempts to open the execution journal
- **THEN** the second process SHALL fail to acquire the lock
- **AND** SHALL fail closed immediately without accessing the database

#### Scenario: Concurrent second executor process fails closed

- **GIVEN** an active executor process holds the execution lockfile
- **WHEN** a second executor process attempts startup
- **THEN** startup of the second process SHALL fail with an error stating that
  another process holds the execution store

#### Scenario: Storage failure recovery requires restarting with healthy storage

- **GIVEN** the journal encounters a disk I/O failure or SQLite corruption
- **WHEN** the error occurs
- **THEN** the store SHALL transition to a storage-failed state and refuse subsequent
  mutations
- **AND** recovery SHALL require restarting the executor process with healthy storage
  and running recovery review

#### Scenario: Concurrent requests during storage failure fail closed

- **GIVEN** journal storage is experiencing I/O errors
- **WHEN** multiple mutation requests arrive concurrently
- **THEN** all requests SHALL fail closed with storage error notifications
- **AND** none SHALL be dispatched unjournaled

#### Scenario: Restored backup with missing rows and stale existing rows refuses missing tokens

- **GIVEN** an older database backup is restored that lacks rows for recently admitted
  operations and contains stale non-terminal rows for completed orders
- **WHEN** a caller retries an operation that was in the missing rows using its
  original epoch
- **THEN** the system SHALL look up the ID, find no row, observe the non-current
  epoch, and refuse the request
- **AND** the stale existing rows SHALL keep the recovery review gate closed at
  startup

#### Scenario: Journal continuity is required before inferring not sent from pre-dispatch row

- **GIVEN** a restored database contains an operation in state `ADMITTED`
- **AND** journal continuity across restarts is broken or unverified
- **WHEN** recovery review evaluates the operation
- **THEN** the system SHALL NOT assume or infer that the operation was `NOT_SENT`
- **AND** SHALL require a durably audited automatic recovery decision before gate
  release, without changing uncertainty into `NOT_SENT`

## ADDED Requirements

### Requirement: Persist Placement Correlation Independently

Each new journaled PLACE operation SHALL receive a unique server-generated order
tag, persisted before dispatch and sent as the broker remark. The tag SHALL fit
within 64 UTF-8 bytes and remain separate from the immutable caller operation ID.

#### Scenario: Crash and identical retry retain tag
- **WHEN** a placement is retried with the same operation ID after a crash
- **THEN** its recorded tag SHALL be returned and the gateway SHALL not be called again
- **AND** caller remarks SHALL remain part of the original request identity
- **AND** MODIFY and CANCEL operations SHALL not generate replacement placement tags

### Requirement: Process-Owned Automatic Paper Recovery

The service SHALL start recovery after startup review and uncertain outcomes without
depending on agent descriptions, conversation memory or a client tool invocation.
Progress, observations, decisions and their timestamps SHALL survive restarts.

#### Scenario: Restart discovers dispatch markers and outstanding reviews
- **WHEN** the process restarts
- **THEN** recovered dispatch markers SHALL become UNKNOWN_OUTCOME
- **AND** outstanding review reasons, including those attached to terminal rows,
  SHALL block new admissions until automatic recovery durably accounts for them
- **AND** reads and reconciliation SHALL remain available

#### Scenario: Recovery worker outlives HTTP sessions
- **WHEN** a stateless MCP request ends or OpenD is temporarily unavailable
- **THEN** recovery SHALL remain process-owned and retry query failures later
- **AND** it SHALL not unlock trading or submit mutations

#### Scenario: Clean startup and independent blocking reasons
- **WHEN** startup review finds no unaccounted operations
- **THEN** its gate SHALL clear
- **AND** storage failures, execution halt and other active blocking reasons SHALL
  still refuse mutations independently

### Requirement: Tagged Paper Placement Absence Policy

After one initial check and four further successful negative rounds, a tagged paper
placement SHALL receive ASSUMED_NOT_PLACED_AFTER_RETRIES. Its UNKNOWN_OUTCOME state
SHALL be preserved while its recovery block is atomically audited and released.
This decision SHALL explicitly represent an assumption rather than proven absence.

#### Scenario: Five clean negatives release recovery without replay
- **WHEN** a tagged placement completes five successful negative rounds
- **THEN** recovery SHALL persist actual check times and the assumption decision
- **AND** it SHALL not classify the operation as NOT_SENT or FAILED
- **AND** an identical retry SHALL return the stored decision without dispatch
- **AND** a distinct new operation SHALL be admitted subject to normal safeguards

#### Scenario: Queuing and failed queries do not create a wall-clock timeout
- **WHEN** rate limiting delays a query or a required query fails
- **THEN** elapsed time SHALL not substitute for a successful negative round
- **AND** the retry count and next scheduled check SHALL survive restarts
- **AND** the absence policy SHALL apply only to tagged PLACE operations
- **AND** a recorded broker ID or previously observed correlation SHALL exclude
  assumed absence even if later queries return empty

#### Scenario: Late discovery rechecks exposure
- **WHEN** background monitoring discovers a previously assumed-absent tag
- **THEN** it SHALL durably block new mutations before accounting for the order
- **AND** it SHALL record a late-discovery audit and current position evidence
- **AND** it SHALL release the block only after consistent accounting completes
- **AND** it SHALL never remove, cancel or replace broker orders automatically

#### Scenario: Legacy identityless operation remains pending
- **WHEN** an old operation has neither a broker order ID nor a persisted tag
- **THEN** it SHALL remain discoverably blocked
- **AND** a new journal SHALL not be reported as accounting for that operation

### Requirement: Return Durable Recovery Context

Execution results and journal health SHALL expose recovery progress and decisions
through ordinary MCP replies. Recovery SHALL not require an unsolicited transport
notification to the agent.

#### Scenario: Next tool call exposes background result
- **WHEN** the agent calls `get_execution`, retries the same operation, places a
  subsequent paper operation, or checks health
- **THEN** the reply SHALL include relevant pending IDs or recent recovery decisions
- **AND** detailed execution replies SHALL distinguish broker status from recovery
  disposition, report check counts and times, and state whether the original may replay

#### Scenario: Durable audit and removal of operator override
- **WHEN** recovery releases a block
- **THEN** its decision and audit SHALL commit together before gate reevaluation
- **AND** legacy audit records SHALL remain readable
- **AND** `acknowledge_recovery` SHALL not be exposed as an MCP tool
