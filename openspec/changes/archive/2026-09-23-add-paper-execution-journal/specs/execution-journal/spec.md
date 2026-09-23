# Spec Delta

## Purpose

Provides a persistent, application-local execution journal for paper order
mutations. It owns execution identity, suppresses duplicate submission, commits
intent before dispatch, preserves uncertainty until it is explicitly reconciled, and
refuses new mutations until recovery has been reviewed.

## ADDED Requirements

### Requirement: Operation Admission and Execution Identity

Every journaled mutation SHALL carry a complete operation token consisting of a
caller-owned `operation_id` and a server-issued `admission_epoch`. The server SHALL
NOT generate, default, derive, parse, or rewrite an identifier.

The `operation_id` SHALL be a non-empty, printable string within a bounded length.
It is scoped to the journal database and its bound paper environment and accounts.
Once reserved in the journal, the identifier is immutable.

When a mutation request is evaluated, the system SHALL look up existing identifiers
in the journal across all recorded epochs before enforcing the current-epoch rule
for new admission:

- **Already Admitted (Stored Outcome).** The identifier exists in the journal and
  the canonical request matches. If the operation is in `DISPATCHING`, the system
  SHALL return an immediate bounded in-flight response (`IN_FLIGHT`) without
  blocking or dispatching. If the operation is in any completed or unresolved state,
  the stored state SHALL be returned and no further dispatch SHALL occur.
- **Conflict.** The identifier exists in the journal and the canonical request
  differs. The request SHALL be refused, naming the differing fields. Same-ID
  "corrected" requests SHALL be refused.
- **New Admission.** If the identifier is not found in the journal:
  - If the caller's supplied `admission_epoch` matches the server's current fresh
    epoch, the token SHALL be admitted in state `ADMITTED`.
  - If the caller's supplied `admission_epoch` does not match the server's current
    fresh epoch, the request SHALL be refused as an unknown non-current-epoch token.
    The caller SHALL NOT refresh or rewrite an old token to retry it.

Admission SHALL be atomic. The environment and the **concrete resolved account** SHALL
be frozen into the operation at admission. Account resolution SHALL precede admission
persistence, and an unresolved placeholder SHALL NOT be persisted as a binding. A retry
of a known identifier SHALL use the recorded binding and SHALL NOT resolve the account
again.

#### Scenario: Unknown identifier with current epoch is admitted

- **GIVEN** no operation exists with identifier `op-a1`
- **AND** the server's active admission epoch is `epoch-current`
- **WHEN** a paper placement is requested with `operation_id='op-a1'` and
  `admission_epoch='epoch-current'`
- **THEN** the journal SHALL persist the operation in state `ADMITTED`
- **AND** the account and environment SHALL be bound immutably to the operation

#### Scenario: Existing identifier lookup precedes epoch validation on retry

- **GIVEN** an operation `op-a2` was admitted and acknowledged under prior epoch
  `epoch-prior`
- **AND** the server's active admission epoch is now `epoch-new`
- **WHEN** the identical request is retried with `operation_id='op-a2'` and
  `admission_epoch='epoch-prior'`
- **THEN** the system SHALL return the stored state for `op-a2`
- **AND** SHALL NOT refuse the request due to the retired epoch
- **AND** SHALL NOT make a second SDK mutation invocation

#### Scenario: Retry with an identical request returns the stored state

- **GIVEN** an operation `op-a1` was admitted and acknowledged
- **WHEN** the same request is re-sent with `operation_id='op-a1'`
- **THEN** the system SHALL return the stored state for `op-a1`
- **AND** SHALL NOT make a further SDK mutation invocation

#### Scenario: Same identifier with changed contents is a conflict

- **GIVEN** an operation `op-a1` was admitted for a quantity of 100
- **WHEN** a request is sent with `operation_id='op-a1'` and a quantity of 200
- **THEN** the system SHALL refuse the request as a conflict
- **AND** the refusal SHALL name the differing field
- **AND** no SDK mutation invocation SHALL be made

#### Scenario: Same-ID corrected request is refused as a conflict

- **GIVEN** an operation `op-a3` was admitted or refused for a limit price of `100.00`
- **WHEN** a caller presents `op-a3` with a "corrected" limit price of `101.00`
- **THEN** the system SHALL refuse the request as an immutable identifier conflict
- **AND** SHALL NOT update or overwrite the existing record

#### Scenario: Missing identifier is refused without substitution

- **WHEN** a journaled mutation is requested with no `operation_id`, or with an empty
  or blank one
- **THEN** the system SHALL refuse it before any gateway request
- **AND** SHALL NOT generate, default or derive an identifier in its place

#### Scenario: Concurrent admissions dispatch at most once

- **GIVEN** no operation exists with identifier `op-a4`
- **WHEN** two requests carrying `operation_id='op-a4'` and identical contents are
  admitted concurrently
- **THEN** at most one SDK mutation invocation SHALL be made
- **AND** the other request SHALL resolve to the resulting stored state rather than
  dispatching

#### Scenario: In-flight retry receives immediate bounded response

- **GIVEN** an operation `op-a5` has committed its dispatch marker and is currently
  executing its SDK mutation invocation
- **WHEN** a retry arrives carrying `operation_id='op-a5'` and identical contents
- **THEN** the system SHALL return an immediate bounded response indicating the
  operation is in flight (`IN_FLIGHT`)
- **AND** SHALL NOT block waiting for the in-flight invocation
- **AND** SHALL NOT launch a second SDK mutation invocation

#### Scenario: Identifier outside the accepted form is refused

- **WHEN** an `operation_id` exceeds the bounded length, or contains characters
  outside the accepted form
- **THEN** the system SHALL refuse the mutation before any gateway request
- **AND** SHALL NOT truncate, normalize or otherwise rewrite the identifier

#### Scenario: Unknown identifier from non-current epoch is refused

- **GIVEN** no operation exists in the journal with identifier `op-a6`
- **AND** the server's active admission epoch is `epoch-current`
- **WHEN** a request presents `operation_id='op-a6'` with an older epoch `epoch-old`
- **THEN** the system SHALL refuse the request
- **AND** SHALL NOT admit the operation as new
- **AND** the refusal SHALL indicate that the non-current token cannot be accounted
  for

#### Scenario: Admitted operation has frozen account binding

- **GIVEN** an operation `op-a7` was admitted for account `acc-1`
- **WHEN** a request presents `op-a7` targeting account `acc-2`
- **THEN** the system SHALL refuse the request as an account binding conflict
- **AND** SHALL NOT permit dispatching against `acc-2`

#### Scenario: A resolved placeholder is frozen as a concrete account

- **GIVEN** exactly one allowlisted simulated account is eligible
- **WHEN** an operation is submitted with `acc_id="0"`
- **THEN** the system SHALL resolve it to that concrete account before persisting
  admission
- **AND** the persisted binding SHALL be that concrete account, not `"0"`

#### Scenario: A change to the eligible accounts does not retarget an admitted operation

- **GIVEN** an operation was admitted through `acc_id="0"` and bound to a concrete
  account
- **WHEN** the set of eligible allowlisted accounts changes, and an identical retry of
  that identifier arrives
- **THEN** the retry SHALL resolve from the recorded binding
- **AND** SHALL NOT be resolved afresh, retargeted, or treated as a new request

### Requirement: Request Canonicalization and Modification Identity

The journal SHALL compute a canonical form of each operation's request and a
fingerprint over it, and SHALL store both, so that a conflict can be explained
rather than merely asserted.

- The canonical form SHALL cover the trading environment, the account, the operation
  type, and the operation's own parameters.
- Prices SHALL be carried and stored as decimal strings, and compared by decimal
  value, so that identity does not depend on binary floating-point coercion. A paper
  mutation price supplied as a number SHALL be **refused**, not converted: once a
  numeric argument has been parsed, the caller's original text is no longer available,
  so no conversion at the tool boundary can recover it.
- For a modification, identity and fingerprinting SHALL cover the **target order ID**
  and the **caller's original patch**: the fields the caller actually supplied. The
  merged broker request SHALL be stored separately, for audit, and SHALL NOT
  contribute to identity.

#### Scenario: Prices are stored and compared as decimal values

- **GIVEN** an operation `op-b1` was admitted at a price of `350.00`
- **WHEN** the same operation is retried with a price of `350.0`
- **THEN** the system SHALL treat the requests as identical
- **AND** SHALL return the stored state rather than reporting a conflict

#### Scenario: A price difference is a conflict, not a rounding artefact

- **GIVEN** an operation `op-b2` was admitted at a price of `350.01`
- **WHEN** the same identifier is presented at a price of `350.02`
- **THEN** the system SHALL refuse the request as a conflict

#### Scenario: Price is not coerced through a binary float

- **WHEN** a paper mutation supplies a price whose decimal value has no exact binary
  representation
- **THEN** the value stored in the journal SHALL be the decimal value as supplied
- **AND** identity comparison SHALL use that decimal value

#### Scenario: A numeric price on a paper mutation is refused, not converted

- **WHEN** a paper mutation supplies its price as a number rather than a decimal string
- **THEN** the system SHALL refuse the mutation before any gateway request
- **AND** SHALL NOT convert the number into a decimal string and proceed

#### Scenario: Malformed decimal string price is refused

- **WHEN** a paper mutation supplies a price string that is not a valid positive
  decimal representation
- **THEN** the system SHALL refuse the request before any database write or gateway
  request

#### Scenario: Price-only retry survives an intervening partial fill

- **GIVEN** a price-only modification `op-b3` was admitted against an order for 100
  shares
- **AND** the order partially filled, leaving a different outstanding quantity
- **WHEN** the caller retries `op-b3` with the same price and no quantity
- **THEN** the system SHALL recognize it as the same operation
- **AND** SHALL NOT report a conflict on the basis of the newly observed quantity

#### Scenario: Modification identity binds target order ID and caller patch

- **GIVEN** an order with ID `order-100`
- **WHEN** a modification is requested targeting `order-100` with price `50.00`
- **THEN** the operation identity SHALL incorporate `order-100` and the price patch
- **AND** a retry targeting a different order ID with the same patch SHALL be
  refused as a conflict

#### Scenario: The merged broker request is recorded but does not define identity

- **GIVEN** a modification `op-b4` supplied only a price
- **WHEN** the merged broker request is constructed from the existing order
- **THEN** the journal SHALL store the merged request
- **AND** the fingerprint SHALL be computed over the supplied patch alone

#### Scenario: A patch that adds a field is a different operation

- **GIVEN** a price-only modification `op-b5` was admitted
- **WHEN** the same identifier is presented with both a price and a quantity
- **THEN** the system SHALL refuse the request as a conflict

### Requirement: Pre-Dispatch Commitment and Single Invocation

The journal SHALL enforce a two-phase dispatch lifecycle:

1. **Identity Resolution:** Validate the token's form and the request schema, then
   look up the identifier across all epochs. A known identifier SHALL resolve from its
   **recorded** account binding, without resolving the account again. This step SHALL
   run outside the service execution lock, so that a duplicate-identifier retry is not
   delayed by an in-flight dispatch.
2. **Account Resolution:** For a new operation, resolve the target account to exactly
   one concrete broker account. A request that cannot SHALL be refused before anything
   is persisted.
3. **Admission Persistence:** Durably commit the operation in state `ADMITTED` in a
   short transaction, binding the **concrete resolved account**, before the order-safety
   checks run. The placeholder `"0"` SHALL NOT be persisted as a binding.
4. **Serialized Preparation and Safety Checks:** Under the service execution lock,
   verify that the journal is not blocked and that recovery review is complete; read the
   target order authoritatively; merge the caller's patch over that reading; and validate
   order value, reference price limits, notional caps and paper scope against the request
   that will actually be sent. If any check fails, commit a transition to `REFUSED` with
   disposition `NOT_SENT` in a short transaction. Under NO circumstances SHALL a dispatch
   marker (`DISPATCHING`) be committed for a pre-dispatch refusal.
5. **Dispatch Marker Commitment:** Still under the execution lock, commit the transition
   from `ADMITTED` to `DISPATCHING` in a short transaction, and close the transaction
   before broker I/O begins.
6. **SDK Mutation Invocation:** Execute exactly one application-level SDK mutation
   invocation, still under the execution lock. A database write transaction SHALL NEVER
   remain open across broker I/O.
7. **Outcome Handling:** Record the outcome in a short transaction immediately
   following the invocation, then release the execution lock.

The authoritative target-order read, the patch merge, the safety assessment, the
dispatch marker, the invocation and the outcome SHALL all occur within one serialized
region, so that no other operation can mutate the target order between the reading an
operation was assessed against and its dispatch.

For each admitted operation the system SHALL make at most one SDK mutation invocation
and SHALL NOT retry that invocation automatically.

#### Scenario: Admission and intent are persisted before the order-safety checks

- **WHEN** a journaled mutation is submitted
- **THEN** the system SHALL resolve the target account to a concrete account first
- **AND** SHALL persist the operation in state `ADMITTED`, bound to that concrete
  account, in durable storage
- **AND** SHALL do so before evaluating order value, limits or target order status

#### Scenario: Concurrent modifications of one order do not restore an omitted field

- **GIVEN** an open order for 100 shares at 50, and journaled paper execution is active
- **WHEN** operation A reduces the quantity to 50, and operation B, submitted
  concurrently, changes only the price to 80
- **THEN** the two operations SHALL be serialized
- **AND** the operation that dispatches second SHALL be prepared against the order as
  the first one left it
- **AND** the price-only operation SHALL NOT restore the quantity from an earlier
  reading

#### Scenario: The target order is read authoritatively inside the serialized region

- **WHEN** a journaled modification or cancellation is prepared
- **THEN** the authoritative target-order read, the patch merge and the safety
  assessment SHALL occur under the service execution lock
- **AND** the request assessed SHALL be the request dispatched

#### Scenario: A duplicate-identifier retry is not delayed by an in-flight dispatch

- **GIVEN** an operation holds the service execution lock during broker I/O
- **WHEN** a retry carrying a known identifier arrives
- **THEN** the retry SHALL be answered from its recorded state without waiting for that
  lock
- **AND** no second SDK mutation invocation SHALL be made

#### Scenario: Pre-dispatch refusal is recorded as refused and not sent without dispatch marker

- **GIVEN** an operation is persisted in state `ADMITTED`
- **WHEN** a pre-dispatch safety check (such as notional cap or account allowlist)
  refuses the request
- **THEN** the operation SHALL transition to `REFUSED` with local disposition
  `NOT_SENT`
- **AND** no `DISPATCHING` marker SHALL be committed
- **AND** no SDK mutation invocation SHALL be made

#### Scenario: Intent and dispatch marker are committed before SDK invocation

- **GIVEN** all pre-dispatch safety checks have passed
- **WHEN** the operation proceeds to dispatch
- **THEN** the dispatch marker (`DISPATCHING`) SHALL be committed to durable
  storage before the SDK mutation invocation begins
- **AND** the committing transaction SHALL be closed before that invocation

#### Scenario: No transaction is held across the gateway call

- **WHEN** a journaled mutation is dispatched
- **THEN** no database write transaction SHALL be open for the duration of the
  gateway call

#### Scenario: Serialized execution prevents concurrent SDK dispatches

- **GIVEN** two admitted operations are ready for dispatch
- **WHEN** dispatching proceeds
- **THEN** the system SHALL serialize the dispatch slot such that only one SDK
  mutation invocation executes at a time
- **AND** the dispatch marker for the second operation SHALL NOT be committed until
  the first invocation and outcome commit complete

#### Scenario: Storage failure before dispatch refuses the mutation

- **GIVEN** the journal cannot be written
- **WHEN** a journaled mutation is requested
- **THEN** the system SHALL refuse it with a storage error
- **AND** no gateway request SHALL be made
- **AND** the error SHALL state that no order was sent

#### Scenario: A lock that cannot be acquired fails closed within a bound

- **GIVEN** the journal's write lock is held beyond the configured bounded wait
- **WHEN** a journaled mutation attempts to commit its intent
- **THEN** the attempt SHALL fail closed as a storage error within that bound
- **AND** SHALL NOT wait unbounded

#### Scenario: Crash after commitment leaves a recoverable record

- **GIVEN** an operation's dispatch marker was committed
- **WHEN** the process terminates before the invocation completes
- **THEN** the journal SHALL retain a record in state `DISPATCHING`
- **AND** on restart the operation SHALL transition to `UNKNOWN_OUTCOME`
- **AND** SHALL NOT be recorded as never attempted

#### Scenario: Storage is not bypassed when it is unavailable

- **GIVEN** the trading mode is `SIMULATE` and journal storage is unavailable
- **WHEN** a mutation is requested
- **THEN** the system SHALL refuse it
- **AND** SHALL NOT fall back to dispatching the mutation unjournaled

#### Scenario: Crash before dispatch marker leaves admitted pre-dispatch row

- **GIVEN** an operation is persisted in state `ADMITTED`
- **WHEN** the process terminates before the dispatch marker is committed
- **THEN** the journal SHALL retain the row in state `ADMITTED`
- **AND** SHALL NOT contain a dispatch marker

#### Scenario: Crash during SDK invocation preserves dispatching state

- **GIVEN** an operation committed `DISPATCHING` and the SDK mutation call began
- **WHEN** the process crashes while waiting for the broker response
- **THEN** upon restart the recovered state SHALL transition to `UNKNOWN_OUTCOME`
- **AND** the journal SHALL record that the operation was in flight during crash

#### Scenario: Crash after SDK invocation but before outcome persistence preserves uncertainty

- **GIVEN** the SDK call returned an acknowledgement
- **WHEN** the process crashes before the outcome is durably committed
- **THEN** upon restart the operation SHALL be recovered as `UNKNOWN_OUTCOME`
- **AND** SHALL NOT be recorded as clean success or not sent

### Requirement: Outcome Classification and Preservation of Uncertainty

The journal SHALL record the outcome of each dispatched operation using the dispatch
boundary defined by `order-placement` › Report the Dispatch Boundary.

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
    successful reconciliation of that operation, or by an authorized operator
    acknowledgement;
  - a failed outcome write is released by an authorized operator acknowledgement only,
    because the unstored fact is what is in doubt;
  - a dispatch marker recovered at startup with no durable outcome is released by an
    authorized operator acknowledgement only. Reconciliation records evidence for it
    but does not release it;
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

- **GIVEN** an operation enters `UNKNOWN_OUTCOME`
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
- **WHEN** reconciliation runs
- **THEN** journal blocking SHALL remain in force until an authorized operator
  acknowledgement accounts for the operation

#### Scenario: Journal blocking is independent of trade relock halt and not cleared by lock_trade

- **GIVEN** paper execution is in state `JOURNAL_BLOCKED`
- **WHEN** `lock_trade` is called
- **THEN** `lock_trade` SHALL NOT clear paper journal blocking
- **AND** subsequent paper mutations SHALL remain blocked

### Requirement: Late Local Failure Reporting

When the gateway acknowledged a mutation but a later local step failed, the result
SHALL report the broker's evidence separately from the local failure.

The result SHALL state:

- what the broker evidenced, including the order identifier when it was readable;
- what failed locally, identified as a conversion failure or a persistence failure;
- whether the submission state is merely **observed** in this process or **durably
  stored**.

A late local failure SHALL NOT be reported as a clean success, and SHALL NOT be
reported as safe to retry.

#### Scenario: Receipt cannot be converted after acknowledgement

- **GIVEN** the gateway acknowledged a journaled placement
- **WHEN** the response cannot be converted
- **THEN** the result SHALL state that the gateway acknowledged the request and that
  the receipt could not be read
- **AND** SHALL identify the failure as a conversion failure
- **AND** SHALL NOT describe the outcome as unknown or rejected

#### Scenario: Outcome cannot be stored after acknowledgement

- **GIVEN** the gateway acknowledged a journaled mutation
- **WHEN** the journal write recording that outcome fails
- **THEN** the result SHALL report the broker's acknowledgement and the persistence
  failure separately
- **AND** SHALL state that the submission state is observed but not durably stored
- **AND** SHALL NOT report the mutation as safe to retry

#### Scenario: Durably stored submission state is reported as such

- **GIVEN** the gateway acknowledged a journaled mutation and the outcome was stored
- **WHEN** a later local step fails
- **THEN** the result SHALL state that the submission state is durably stored

#### Scenario: A readable order identifier is reported even when a later step fails

- **GIVEN** the gateway acknowledged a journaled placement and returned an order
  identifier that could be read
- **WHEN** a later local step fails
- **THEN** the result SHALL include that order identifier as broker evidence

### Requirement: Bounded Reconciliation of Journal-Owned Orders

The system SHALL provide an explicit reconciliation path for operations that are not
in a terminal state. Reconciliation SHALL apply only to operations the journal owns.

Reconciliation SHALL use order and history-order observations. It SHALL NOT depend on
broker deal records, because the paper provider does not offer a deal query.

Matching SHALL distinguish candidate matches from proof of ownership:

- Attribute/time matching (matching code, side, quantity, price within submission
  window) produces **candidates**, NOT proof of journal ownership.
- Automatic association to `RECONCILED` SHALL require **reliable broker identity**
  (recorded broker order identifier) or **verified provider correlation** (unique
  client tag / remark).
- Finding a modification or cancellation target order does NOT prove that the
  mutation succeeded.
- Exactly one match with reliable identity/correlation SHALL resolve the operation
  to `RECONCILED`, adopting the broker status as authoritative.
- Zero matches, ambiguous matches, or candidate-only matches SHALL leave the
  operation unresolved. An empty result SHALL NOT be treated as proof that no order
  exists at the broker.

Reconciliation SHALL NOT dispatch any order-mutating request.

#### Scenario: A unique match with reliable broker identity resolves the operation

- **GIVEN** an operation is in `UNKNOWN_OUTCOME` with a recorded broker order ID
- **WHEN** reconciliation queries the broker and matches that exact order ID
- **THEN** the operation SHALL transition to `RECONCILED`
- **AND** the broker's status SHALL be recorded as authoritative

#### Scenario: Attribute and time matches are candidates, not proof of ownership

- **GIVEN** an operation in `UNKNOWN_OUTCOME` has no recorded broker order ID
- **WHEN** reconciliation finds an order matching symbol, side, quantity and price
  within the submission window, but lacking verified correlation
- **THEN** the match SHALL be treated as a candidate only
- **AND** the operation SHALL remain unresolved in `UNKNOWN_OUTCOME`

#### Scenario: Unrelated identical broker orders do not resolve the operation

- **GIVEN** multiple orders with identical symbol, quantity, side and price exist on
  the paper account
- **WHEN** reconciliation evaluates an uncertain operation lacking reliable broker ID
- **THEN** the system SHALL NOT associate any of the identical orders
- **AND** the operation SHALL remain unresolved

#### Scenario: Finding modification target order does not prove modification succeeded

- **GIVEN** an order modification is in `UNKNOWN_OUTCOME`
- **WHEN** reconciliation locates the target order at the broker
- **THEN** locating the target order SHALL NOT be taken as proof that the
  modification succeeded
- **AND** the modification SHALL remain unresolved until verified provider
  correlation confirms the patch was applied

#### Scenario: Finding cancellation target order does not prove cancellation succeeded

- **GIVEN** an order cancellation is in `UNKNOWN_OUTCOME`
- **WHEN** reconciliation locates the target order with broker status `CANCELLED_ALL`
- **THEN** the system SHALL NOT mark the cancellation operation as successful without
  verifying that the cancellation was caused by this operation rather than prior
  action or expiry

#### Scenario: An empty result does not resolve the operation

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** the broker order and history-order queries return no matching record
- **THEN** the operation SHALL remain unresolved
- **AND** the system SHALL NOT record that the order does not exist at the broker

#### Scenario: Ambiguous matches do not resolve the operation

- **WHEN** reconciliation finds more than one candidate matching broker order
- **THEN** the operation SHALL remain unresolved
- **AND** the system SHALL NOT select one of the candidates

#### Scenario: Reconciliation does not use deal records

- **WHEN** reconciliation runs
- **THEN** it SHALL query orders and history orders only
- **AND** SHALL NOT issue a deal query or depend on deal records being available

#### Scenario: Reconciliation sends no order mutation

- **WHEN** reconciliation runs for any operation
- **THEN** no order placement, modification or cancellation SHALL be dispatched

#### Scenario: Orders the journal does not own are not reconciled

- **GIVEN** the account holds orders that no journal operation owns
- **WHEN** reconciliation runs
- **THEN** those orders SHALL NOT be adopted into the journal
- **AND** SHALL NOT resolve any operation

### Requirement: Storage Lifecycle and Admission Epochs

The execution store SHALL verify its storage before serving mutations, enforce
single-process execution, and SHALL NEVER silently recreate missing storage.

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
- **AND** SHALL require explicit evidence-backed operator accounting before gate
  release

### Requirement: Recovery Review Gate and Operator Acknowledgement

Every start of a paper execution process SHALL require recovery review before new
mutations are admitted.

On start, the system SHALL enumerate operations that are not in a terminal state.
Until review has run and each has been accounted for, new mutations SHALL be refused.
Reconciliation and read operations SHALL remain available while the gate holds.

When automatic reconciliation cannot resolve an uncertain operation, it SHALL be
resolved only via a named, operator-only recovery acknowledgement mechanism:

- **Authorization.** The mechanism SHALL require an operator capability that is
  distinct from the credential the trading agent presents, and that is not provisioned
  to the agent. A request authenticated only by the trading agent's transport
  credential SHALL be refused, whatever arguments it carries. Where no operator
  capability is configured, the mechanism SHALL be unavailable and paper execution
  SHALL remain blocked.
- **Audited identity.** The recorded operator identity SHALL be derived from the
  authenticated principal. A supplied `operator_id` SHALL be accepted only when it
  matches that principal, and SHALL otherwise be refused rather than recorded.
- **Binding.** An acknowledgement SHALL name the recovery epoch and the operation state
  the operator reviewed, and SHALL be refused if either has changed since.
- Requests with insufficient evidence SHALL be refused, leaving execution blocked.
- Mutation uncertainty SHALL remain distinct from recovery disposition. The system
  SHALL NOT provide a generic "accept risk and mark reconciled" override.
- **`TERMINAL_ACCOUNTED` SHALL record accounting, not closure.** A terminal order and
  closed exposure are distinct facts. An operator MAY account for a target order whose
  final status is established by verified broker evidence, and the disposition SHALL
  record all of:
  - the target's final broker status;
  - its filled quantity and average fill price;
  - its remaining executable quantity, stated explicitly;
  - the resulting position or other account effects attributable to the operation.

  It SHALL NOT require the account to be flat, and SHALL NOT be recorded as the
  uncertain mutation having succeeded.
- **Version 1 SHALL provide exactly one operator disposition, `TERMINAL_ACCOUNTED`.**
  A disposition asserting that no broker order exists SHALL NOT be offered in version
  1. An order's non-appearance in a query SHALL NOT be accepted as evidence of absence,
  and an empty post-close history query alone SHALL leave the operation unresolved.
  - Should such a disposition be introduced later, it SHALL rest on provider-verified
    evidence that positively establishes absence, and SHALL NOT be mapped onto Stage
    1's pre-dispatch `NOT_SENT` classification: evidence that no broker order was
    created does not establish that the SDK invocation never started.
- **A dispatch marker recovered at startup with no durable outcome SHALL require an
  authorized operator acknowledgement** before automated execution resumes.
  Reconciliation SHALL still run and SHALL record its findings as evidence, and SHALL
  NOT by itself clear that requirement. This applies however the process ended,
  because a crash before the SDK call and a lost outcome write leave identical durable
  evidence.
- The acknowledgement and its durable `recovery_audit` record SHALL commit in the same
  transaction, and that transaction SHALL commit before the gate is re-evaluated.
- **Gate release SHALL depend on two conditions, not one:** startup recovery review
  SHALL be complete, **and** no blocking reason SHALL remain active. Release SHALL NOT
  be determined solely by every operation row having reached a terminal state.
- **An outstanding operator-review requirement SHALL be durable and independently
  discoverable,** and SHALL survive the operation's lifecycle state reaching a terminal
  value. Reconciliation moving an operation to `RECONCILED` SHALL NOT satisfy an
  outstanding review requirement attached to it. Startup SHALL enumerate outstanding
  review requirements as well as non-terminal operations, so a requirement attached to
  a terminal row is not overlooked.
- **An operation that can be accounted for by neither reconciliation nor an authorized
  evidence-backed disposition SHALL keep automated execution blocked indefinitely.**
  The system SHALL NOT offer a risk-acceptance override, and SHALL NOT resume by
  discarding the unresolved record.
- **Reinitializing, replacing or repointing the journal for the same broker account
  SHALL NOT be treated as accounting for unresolved execution.** A journal that no
  longer holds an operation's record SHALL NOT be reported as reconciliation of it.

#### Scenario: New mutations are refused until review has run

- **GIVEN** a paper execution process has just started
- **WHEN** a mutation is requested before recovery review has run
- **THEN** the system SHALL refuse it
- **AND** the refusal SHALL state that recovery review is outstanding

#### Scenario: Reads and reconciliation remain available while the gate holds

- **GIVEN** recovery review is outstanding
- **WHEN** a read operation or a reconciliation is requested
- **THEN** it SHALL be served

#### Scenario: A clean journal reviews to empty

- **GIVEN** a paper execution process starts with no operations outside a terminal
  state
- **WHEN** recovery review runs
- **THEN** it SHALL complete with nothing outstanding
- **AND** mutations SHALL be admitted thereafter

#### Scenario: Unresolved operations keep the gate closed

- **GIVEN** recovery review found operations that are not in a terminal state
- **WHEN** those operations have not been accounted for
- **THEN** new mutations SHALL remain refused

#### Scenario: A restored journal requires review before mutations

- **GIVEN** older journal storage has been restored onto the volume
- **WHEN** the process starts
- **THEN** recovery review SHALL be required before any mutation is admitted
- **AND** this SHALL follow from every process start requiring review, without the
  system detecting that a restore occurred

#### Scenario: Recovery review is required whether or not anything was restored

- **GIVEN** a paper execution process starts on journal storage that was never restored
- **WHEN** the process starts
- **THEN** recovery review SHALL still be required before any mutation is admitted

#### Scenario: Named operator recovery acknowledgement with valid evidence satisfies gate condition

- **GIVEN** an operation `op-r1` is in `UNKNOWN_OUTCOME`
- **WHEN** an authorized operator submits `acknowledge_recovery` with `operator_id`,
  `resolution='TERMINAL_ACCOUNTED'`, justification `reason`, verified broker
  `evidence_reference`, and the target's final status, filled quantity, remaining
  executable quantity and resulting position
- **THEN** the operation SHALL transition to `TERMINAL_ACCOUNTED`
- **AND** the recovery review gate condition SHALL be satisfied for that operation

#### Scenario: Unauthorized recovery acknowledgement is refused

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** a recovery acknowledgement is attempted without valid operator
  authorization
- **THEN** the request SHALL be refused as unauthorized
- **AND** the operation state SHALL NOT change
- **AND** execution SHALL remain blocked

#### Scenario: A trading-agent credential cannot acknowledge recovery

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** `acknowledge_recovery` is called with a valid trading-agent transport
  credential, a well-formed `operator_id`, a `reason` and an `evidence_reference`
- **THEN** the request SHALL be refused for lack of the operator capability
- **AND** the operation state SHALL NOT change
- **AND** execution SHALL remain blocked

#### Scenario: Operator identity is taken from the authenticated principal

- **GIVEN** an authorized operator submits `acknowledge_recovery`
- **WHEN** the supplied `operator_id` does not match the authenticated principal
- **THEN** the request SHALL be refused
- **AND** no `recovery_audit` row SHALL record the supplied identity

#### Scenario: A stale acknowledgement is refused

- **GIVEN** an operator reviewed an operation in a given recovery epoch and state
- **WHEN** the acknowledgement arrives after that operation's state changed, or after
  the process restarted into a new recovery epoch
- **THEN** the acknowledgement SHALL be refused
- **AND** the gate SHALL remain closed for that operation

#### Scenario: Audit record commits before the gate is re-evaluated

- **GIVEN** a valid operator recovery acknowledgement is accepted
- **WHEN** the disposition is applied
- **THEN** the disposition and its `recovery_audit` row SHALL commit in the same
  transaction
- **AND** the gate SHALL NOT be re-evaluated until that transaction has committed

#### Scenario: Insufficient evidence keeps execution blocked

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** an operator submits `acknowledge_recovery` with missing, blank, or
  unverifiable evidence
- **THEN** the request SHALL be refused for insufficient evidence
- **AND** paper execution SHALL remain blocked

#### Scenario: Recovery acknowledgement records durable audit entry

- **GIVEN** a valid operator recovery acknowledgement is accepted
- **WHEN** the transaction commits
- **THEN** a row SHALL be inserted into `recovery_audit` containing the operator ID,
  operation ID, resolution, reason, evidence reference, and timestamp

#### Scenario: Evidence-backed accounting of terminal target accounts for exposure without false success claim

- **GIVEN** an uncertain cancellation of a BUY order for 100 shares is in
  `UNKNOWN_OUTCOME`
- **AND** broker evidence proves the target order reached `FILLED_ALL` for 100 shares
- **WHEN** the operator acknowledges recovery under `TERMINAL_ACCOUNTED`
- **THEN** the operation SHALL be recorded as `TERMINAL_ACCOUNTED`
- **AND** the system SHALL NOT record that the cancellation succeeded
- **AND** the disposition SHALL retain the filled quantity of 100, a remaining
  executable quantity of zero, and the resulting 100-share position
- **AND** the gate SHALL NOT treat the resulting exposure as closed

#### Scenario: An empty post-close history query alone does not account for an operation

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **AND** a history-order query after trading close returns no matching record
- **WHEN** an operator offers that empty result as evidence of absence
- **THEN** the acknowledgement SHALL be refused for insufficient evidence
- **AND** the operation SHALL remain unresolved
- **AND** paper execution SHALL remain blocked

#### Scenario: A recovered dispatch marker requires operator acknowledgement

- **GIVEN** a dispatch marker was recovered at startup with no durable outcome
- **WHEN** recovery review runs
- **THEN** automated execution SHALL remain blocked until an authorized operator
  acknowledgement accounts for that operation

#### Scenario: Reconciliation after a lost outcome write does not resume execution

- **GIVEN** a mutation was acknowledged by the broker and its outcome write failed
- **AND** the process then terminated and restarted, leaving a recovered dispatch
  marker with no durable outcome
- **WHEN** reconciliation of that operation completes successfully
- **THEN** its findings SHALL be recorded as evidence
- **AND** automated execution SHALL still wait for the bound operator acknowledgement
- **AND** the review requirement SHALL NOT be cleared by that reconciliation

#### Scenario: A reconciled operation with an outstanding review requirement still blocks

- **GIVEN** a dispatch marker was recovered at startup and reconciliation moved that
  operation to `RECONCILED`
- **AND** no operator acknowledgement has been recorded for it
- **WHEN** a paper mutation is requested
- **THEN** execution SHALL remain blocked
- **AND** the outstanding review requirement SHALL remain discoverable although the
  operation's lifecycle state is terminal

#### Scenario: A further restart does not discard an outstanding review requirement

- **GIVEN** a recovered dispatch marker was reconciled, its evidence committed, and no
  operator acknowledgement recorded
- **WHEN** the process restarts again
- **THEN** startup SHALL find the outstanding review requirement even though no
  operation row is non-terminal
- **AND** execution SHALL remain blocked
- **AND** only a valid, durably committed operator acknowledgement SHALL release that
  requirement, subject to any other blocking reason still active

#### Scenario: An operation that cannot be accounted for blocks execution indefinitely

- **GIVEN** an operation that neither reconciliation nor an authorized
  evidence-backed disposition can account for
- **WHEN** further paper mutations are requested, at any later time
- **THEN** they SHALL continue to be refused
- **AND** the system SHALL NOT offer an override that resumes execution by accepting
  the uncertainty

#### Scenario: A new journal does not account for a prior unresolved operation

- **GIVEN** an unresolved operation recorded in a journal for a paper account
- **WHEN** a new or reinitialized journal is started for that same account and its
  startup review finds nothing outstanding
- **THEN** that review SHALL NOT be reported as reconciliation of the prior operation
- **AND** the prior operation SHALL NOT be treated as accounted for
