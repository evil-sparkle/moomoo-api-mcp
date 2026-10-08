# provider-request-governance Specification

## Purpose

Govern requests to explicitly limited provider operations without imposing a
single rate limit on every MCP request or blocking unrelated diagnostics.

## Requirements

### Requirement: Shared Provider Operation Budget

The system SHALL share each governed operation's rolling request budget across
all clients of the process-owned gateway service. Only explicitly configured
operations SHALL consume that budget. Documentation SHALL identify its process
scope and SHALL NOT claim coordination with independent gateway clients.

#### Scenario: Several clients use one gateway service

- **WHEN** concurrent MCP clients request the same governed operation
- **THEN** they consume one shared budget regardless of request arguments.

#### Scenario: Other operations remain available

- **WHEN** option-chain admission is saturated
- **THEN** unrelated tools do not wait for its quota.

### Requirement: Bounded Cancellable Admission

Governed MCP requests SHALL wait asynchronously for at most five seconds for
quota before dispatching blocking SDK work. Expired admission SHALL raise an
explicit operation-specific error containing retry-after seconds. Cancelled
requests SHALL release unused reservations and SHALL NOT subsequently dispatch.

#### Scenario: Capacity becomes available before the deadline

- **WHEN** a rolling-window entry expires within the admission deadline
- **THEN** a waiting request is admitted without consuming a worker while waiting.

#### Scenario: Capacity does not become available

- **WHEN** admission reaches its deadline with the quota exhausted
- **THEN** the request fails with retry-after guidance and no SDK invocation.

#### Scenario: Cancellation during quota or worker waiting

- **WHEN** a request is cancelled before its SDK operation starts
- **THEN** it makes no SDK call and any reserved capacity becomes reusable.

### Requirement: Dispatch Accounting

Reservations SHALL remain counted until dispatched or released and SHALL NOT
expire while waiting for a worker. Dispatched calls SHALL consume the window
budget even when the provider rejects them or the caller cancels. Invalid
arguments SHALL fail before quota admission. Direct synchronous service calls
SHALL obey the same budget without sleeping for capacity.

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
