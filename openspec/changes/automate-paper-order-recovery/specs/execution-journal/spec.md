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
