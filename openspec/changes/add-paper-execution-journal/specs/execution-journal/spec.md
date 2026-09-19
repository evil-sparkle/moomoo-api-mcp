# Spec Delta

## Purpose

Provides a persistent, application-local execution journal for paper order
mutations. It owns execution identity, suppresses duplicate submission, commits
intent before dispatch, preserves uncertainty until it is explicitly reconciled, and
refuses new mutations until recovery has been reviewed.

## ADDED Requirements

### Requirement: Operation Admission and Execution Identity

Every journaled mutation SHALL carry a complete operation identifier established by
the caller before the request is sent. The server SHALL NOT generate, default,
derive or synthesize one.

The identifier SHALL be a non-empty printable string within a bounded length. The
server SHALL treat it as opaque and SHALL NOT parse it or infer structure from it.

Admission SHALL resolve to exactly one of:

- **Reserved.** The identifier is unknown to the journal and is recorded, permitting
  a single dispatch.
- **Already admitted.** The identifier is known and the canonical request matches.
  The stored state SHALL be returned and no further dispatch SHALL occur.
- **Conflict.** The identifier is known and the canonical request differs. The
  request SHALL be refused, and the refusal SHALL identify the differing fields.
- **Refused.** The identifier cannot be accounted for, as specified by Storage
  Lifecycle and Admission Epochs.

Admission SHALL be atomic. Concurrent admissions of the same identifier SHALL result
in at most one dispatch.

#### Scenario: Unknown identifier is reserved and dispatched once

- **GIVEN** no operation exists with identifier `op-a1`
- **WHEN** a paper placement is admitted with `operation_id='op-a1'`
- **THEN** the journal SHALL record the operation against that identifier
- **AND** exactly one SDK mutation invocation SHALL be made for it

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

#### Scenario: Missing identifier is refused without substitution

- **WHEN** a journaled mutation is requested with no `operation_id`, or with an empty
  or blank one
- **THEN** the system SHALL refuse it before any gateway request
- **AND** SHALL NOT generate, default or derive an identifier in its place

#### Scenario: Concurrent admissions dispatch at most once

- **GIVEN** no operation exists with identifier `op-a2`
- **WHEN** two requests carrying `operation_id='op-a2'` and identical contents are
  admitted concurrently
- **THEN** at most one SDK mutation invocation SHALL be made
- **AND** the other request SHALL resolve to the resulting stored state rather than
  dispatching

#### Scenario: Identifier outside the accepted form is refused

- **WHEN** an `operation_id` exceeds the bounded length, or contains characters
  outside the accepted form
- **THEN** the system SHALL refuse the mutation before any gateway request
- **AND** SHALL NOT truncate, normalize or otherwise rewrite the identifier

### Requirement: Request Canonicalization and Modification Identity

The journal SHALL compute a canonical form of each operation's request and a
fingerprint over it, and SHALL store both, so that a conflict can be explained
rather than merely asserted.

- The canonical form SHALL cover the trading environment, the account, the operation
  type, and the operation's own parameters.
- Prices SHALL be carried and stored as decimal strings, and compared by decimal
  value, so that identity does not depend on binary floating-point coercion.
- For a modification, the fingerprint SHALL be computed over the **caller's original
  patch**: the fields the caller actually supplied. The merged broker request SHALL
  be stored separately, for audit, and SHALL NOT contribute to identity.

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

#### Scenario: Price-only retry survives an intervening partial fill

- **GIVEN** a price-only modification `op-b3` was admitted against an order for 100
  shares
- **AND** the order partially filled, leaving a different outstanding quantity
- **WHEN** the caller retries `op-b3` with the same price and no quantity
- **THEN** the system SHALL recognize it as the same operation
- **AND** SHALL NOT report a conflict on the basis of the newly observed quantity

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

The journal SHALL durably commit the operation's intent and a dispatch marker before
the SDK mutation invocation begins. The dispatch marker records that an invocation is
about to occur, so that an interrupted operation is recoverable rather than lost.

A database write transaction SHALL NEVER remain open across a gateway call. Every
transaction SHALL be explicit, short, and closed before dispatch begins.

For each admitted operation the system SHALL make at most one application-level SDK
mutation invocation. The system SHALL NOT itself retry that invocation.

If storage cannot be written before dispatch, the mutation SHALL be refused, and the
refusal SHALL state that no order was sent.

#### Scenario: Intent and dispatch marker are committed before the call

- **WHEN** a journaled mutation is dispatched
- **THEN** the operation and its dispatch marker SHALL be committed to durable
  storage before the SDK mutation invocation begins
- **AND** the committing transaction SHALL be closed before that invocation

#### Scenario: No transaction is held across the gateway call

- **WHEN** a journaled mutation is dispatched
- **THEN** no database write transaction SHALL be open for the duration of the
  gateway call

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

- **GIVEN** an operation's intent and dispatch marker were committed
- **WHEN** the process terminates before the invocation completes
- **THEN** the journal SHALL retain a record indicating that an invocation may have
  started
- **AND** the operation SHALL NOT be recorded as never attempted

#### Scenario: Storage is not bypassed when it is unavailable

- **GIVEN** the trading mode is `SIMULATE` and journal storage is unavailable
- **WHEN** a mutation is requested
- **THEN** the system SHALL refuse it
- **AND** SHALL NOT fall back to dispatching the mutation unjournaled

### Requirement: Outcome Classification and Preservation of Uncertainty

The journal SHALL record the outcome of each dispatched operation using the dispatch
boundary defined by `order-placement` › Report the Dispatch Boundary.

Local lifecycle states SHALL remain distinct from broker order statuses. Local states
are `ADMITTED`, `DISPATCHING`, `ACKNOWLEDGED`, `UNKNOWN_OUTCOME`, `RECONCILED` and
`REFUSED`. Broker statuses are those the provider reports, such as `SUBMITTED`,
`FILLED_PART`, `FILLED_ALL`, `CANCELLED_ALL` and `REJECTED`.

- An acknowledgement SHALL NOT be recorded or reported as a fill.
- An unknown outcome SHALL NOT be recorded or reported as a rejection.
- An operation in `UNKNOWN_OUTCOME` SHALL NEVER be automatically resubmitted, and
  SHALL NEVER be resolved by dispatching a substitute or replacement order.

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

Matching SHALL prefer a recorded broker order identifier. Without one, it SHALL match
on the operation's recorded attributes within that operation's own submission window.

- Exactly one match SHALL resolve the operation to `RECONCILED`, adopting the broker
  status as authoritative.
- Zero matches SHALL leave the operation unresolved. An empty result SHALL NOT be
  treated as proof that no order exists at the broker.
- Two or more matches SHALL leave the operation unresolved. The system SHALL NOT
  choose between them.

Reconciliation SHALL NOT dispatch any order-mutating request.

#### Scenario: A unique match resolves the operation

- **GIVEN** an operation is in `UNKNOWN_OUTCOME`
- **WHEN** reconciliation finds exactly one matching broker order
- **THEN** the operation SHALL transition to `RECONCILED`
- **AND** the broker's status SHALL be recorded as authoritative

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

#### Scenario: A recorded broker order identifier is preferred for matching

- **GIVEN** an operation recorded a broker order identifier before the failure
- **WHEN** reconciliation runs
- **THEN** it SHALL match on that identifier
- **AND** SHALL NOT resolve the operation from attribute matching alone

#### Scenario: Orders the journal does not own are not reconciled

- **GIVEN** the account holds orders that no journal operation owns
- **WHEN** reconciliation runs
- **THEN** those orders SHALL NOT be adopted into the journal
- **AND** SHALL NOT resolve any operation

### Requirement: Storage Lifecycle and Admission Epochs

The execution store SHALL verify its storage before serving mutations, and SHALL
NEVER silently recreate missing storage.

- Creating the journal SHALL be an explicit, configured act. A missing file at a
  configured path SHALL fail closed.
- The schema version SHALL be recorded. A version newer than the running binary
  understands SHALL fail closed without modifying the file. A failed integrity check
  SHALL fail closed.
- The store SHALL record an **admission epoch**, and SHALL stamp each admitted
  operation with the epoch that admitted it. Each start SHALL retire the previous
  epoch and begin a new one.
- After a restart, an operation identifier that the store cannot account for — including
  one whose record is absent because older storage was restored — SHALL be refused,
  and SHALL NOT be admitted as a new operation.

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

#### Scenario: An identifier from a retired epoch is refused

- **GIVEN** an operation identifier was admitted under a previous admission epoch
- **AND** the store cannot account for that operation after a restart
- **WHEN** the identifier is presented again
- **THEN** the system SHALL refuse it
- **AND** SHALL NOT admit it as a new operation

#### Scenario: An older restored backup does not resurrect identifiers as new

- **GIVEN** an older backup was restored over the journal, and it does not contain
  rows for operations admitted after that backup was taken
- **WHEN** one of those operation identifiers is presented again
- **THEN** the system SHALL refuse it
- **AND** SHALL NOT admit it as a new operation

### Requirement: Recovery Review Gate

Every start of a paper execution process SHALL require recovery review before new
mutations are admitted.

On start, the system SHALL enumerate operations that are not in a terminal state.
Until review has run and each has been accounted for, new mutations SHALL be refused.
Reconciliation and read operations SHALL remain available while the gate holds.

This gate is specific to recovery. It is not a general operator pause facility, and
it SHALL NOT be entered other than by a process start.

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

- **GIVEN** the journal's epoch history does not match what this process retired
- **WHEN** the process starts
- **THEN** recovery review SHALL be required before any mutation is admitted
