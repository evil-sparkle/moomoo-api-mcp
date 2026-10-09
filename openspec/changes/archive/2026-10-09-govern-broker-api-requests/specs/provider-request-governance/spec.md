# Spec Delta

## ADDED Requirements

### Requirement: Complete Broker Operation Coverage

The system SHALL govern every runtime broker operation with a documented timed
limit, including internal validation and recovery calls. It SHALL document
operations without timed limits and SHALL preserve cached-read and historical
candle continuation exemptions.

#### Scenario: Fresh and cached account reads

- **WHEN** account funds, positions, current orders, or today's deals are queried
- **THEN** only refresh requests consume their operation's timed budget.

#### Scenario: Internal broker observations

- **WHEN** order assessment or paper recovery queries orders, history, positions,
  or snapshots
- **THEN** those calls consume the same budgets as the corresponding public tools.

#### Scenario: Historical candle continuation

- **WHEN** a valid provider continuation token is used
- **THEN** the continuation does not consume the initial-page request budget.

### Requirement: Scoped and Shared Quotas

The system SHALL separate account-scoped budgets by resolved account and
user-scoped budgets by gateway user. Placement and combo placement SHALL share
one budget; modifications and cancellations SHALL share another. Independent
operations SHALL retain separate budgets. Conservative grouping SHALL be
documented when broker wording does not explicitly establish independence.

#### Scenario: Shared placement budget

- **WHEN** regular and combo orders target one account
- **THEN** they collectively consume at most 15 placement calls per rolling 30 seconds.

#### Scenario: Independent accounts and operations

- **WHEN** one account's fresh-order budget is exhausted
- **THEN** another account's fresh-order budget and the first account's funds
  and history budgets remain independent.

### Requirement: Broker Dispatch Spacing

The system SHALL enforce at least 20 milliseconds between placement starts and
40 milliseconds between modification starts, including cancellations and combo
placements in their respective groups. Waiting worker reservations SHALL NOT
permit those starts to bunch together.

#### Scenario: Delayed paced workers

- **WHEN** worker congestion delays admitted placements or modifications
- **THEN** eventual SDK starts still satisfy their group's minimum spacing.

### Requirement: Safe Mutation Admission

The system SHALL reserve mutation capacity before the broker dispatch boundary.
Credential-managed REAL writes SHALL reserve unlock and relock capacity before
unlocking. Paper writes SHALL reserve capacity before persisting a dispatch
marker. Admission failures SHALL identify unsent writes, and dispatched writes
SHALL never be automatically replayed.

#### Scenario: Insufficient relock capacity

- **WHEN** a REAL write cannot reserve both unlock and relock capacity
- **THEN** the gateway remains locked and no order mutation is attempted.

#### Scenario: Paper mutation admission failure

- **WHEN** a paper mutation cannot reserve broker write capacity
- **THEN** no broker write or dispatch marker is produced.

#### Scenario: Broker rejects an admitted mutation

- **WHEN** an SDK mutation returns an error or loses its response
- **THEN** capacity remains charged and existing outcome and recovery semantics
  remain intact, without replay.

## MODIFIED Requirements

### Requirement: Shared Provider Operation Budget

The system SHALL share each governed operation's rolling request budget across
all clients of the process-owned gateway within its configured account, user,
or gateway scope. Explicitly grouped operations SHALL share one budget. Only
configured operations SHALL consume that budget. Documentation SHALL identify
its process scope and SHALL NOT claim coordination with independent clients.

#### Scenario: Several clients use one gateway service

- **WHEN** concurrent MCP clients request the same governed operation within one
  quota scope
- **THEN** they consume one shared budget regardless of other request arguments.

#### Scenario: Other operations remain available

- **WHEN** option-chain admission is saturated
- **THEN** unrelated tools do not wait for its quota.

### Requirement: Bounded Cancellable Admission

Governed MCP requests SHALL wait asynchronously for at most five seconds for
quota before dispatching blocking SDK work. Expired admission SHALL raise an
operation-specific error containing retry-after seconds. Cancellation before
dispatch SHALL release unused reservations and prevent SDK work. A mutation
already past its safe dispatch boundary SHALL retain permits to complete its
SDK write and any relock.

#### Scenario: Capacity becomes available before the deadline

- **WHEN** a rolling-window entry expires within the admission deadline
- **THEN** a waiting request is admitted without consuming a worker while waiting.

#### Scenario: Capacity does not become available

- **WHEN** admission reaches its deadline with the quota exhausted
- **THEN** the request fails with retry-after guidance and no SDK invocation.

#### Scenario: Cancellation during quota or worker waiting

- **WHEN** a request is cancelled while waiting for quota or an SDK worker
- **THEN** it makes no SDK call and any reserved capacity becomes reusable.

### Requirement: Dispatch Accounting

Reservations SHALL remain counted until dispatched or released and SHALL NOT
expire while waiting for a worker. Dispatched calls SHALL consume the window
budget even when the provider rejects them or the caller cancels. Known invalid
arguments and policy violations SHALL fail before quota admission. Direct
synchronous service calls SHALL obey the same budget without sleeping for capacity.

#### Scenario: Worker congestion outlasts the window

- **WHEN** reserved requests cannot start because all workers are busy
- **THEN** their reservations remain counted until actual dispatch or cancellation.

#### Scenario: Rejected provider request

- **WHEN** a dispatched SDK request returns an error
- **THEN** its quota entry remains counted and the error is returned without retry.

#### Scenario: Invalid request with an exhausted budget

- **WHEN** an invalid date range or option type is submitted
- **THEN** the original validation error is raised immediately without quota use.

#### Scenario: Direct synchronous caller with no capacity

- **WHEN** a synchronous service caller finds the shared budget exhausted
- **THEN** it receives the retry-after error immediately without invoking the SDK.
