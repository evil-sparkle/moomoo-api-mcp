# Tasks

No task below is complete. Nothing here is implemented, and no runtime milestone is
claimed. Task groups 1, 10 and 11 gate the others: implementation begins only after
group 1 resolves the provider facts and the prerequisite reconciliation, and after
implementation is separately authorized. Group 11 requires its own authorization
again.

## Test layers

| ID | Layer | What it proves |
| --- | --- | --- |
| `U01`–`U14` | Automated unit and integration, fake broker | The journal's own logic and its fault behaviour |
| `C01`–`C04` | Isolated container tests | Volume topology, permissions, persistence, restoration |
| `M01`–`M04` | Manual, separately authorized | Real provider behaviour, end to end |

**A passing fake broker proves the journal, not the provider.** `U*` success marks no
provider capability, no live execution quality, and no agent retry behaviour as
verified. Only `M*` can do that, and only with separate authorization.

## 1. Verify provider facts and reconcile with the prerequisite

Read-only or paper-only. No REAL order is placed. Record each finding in `design.md`
under the decision it affects.

- [ ] 1.0 Re-run `npx -y @fission-ai/openspec@1.13.1 validate --all --strict
  --no-interactive` after `harden-trading-safeguards` lands, and re-check these delta
  specs against the landed Stage 1 specs. Verify by confirming that the three
  reconciled requirements — `Support Placing Orders`, `Support Modifying Orders`,
  `Support Cancelling Orders` — still carry the Stage 1 text forward without
  reverting it.
- [ ] 1.1 Confirm against a real paper account whether paper orders accept any time
  in force other than `DAY`. Verify by recording the accepted values. If a non-`DAY`
  value is accepted, revisit the version 1 scope restriction.
- [ ] 1.2 Confirm whether the paper provider exposes any deal query. Verify by
  recording the attempted call and its outcome. If one exists, record it as additive
  to reconciliation; the order and history-order path stays the primary one.
- [ ] 1.3 Record the fields available from paper order and history-order queries that
  reconciliation can match on, including whether a submission timestamp and any
  caller-supplied remark survive. Verify by listing the observed fields.
- [ ] 1.4 Measure how long a paper order stays queryable after it reaches a terminal
  state, by order query and by history-order query. Verify by recording both windows.
  This replaces the prerequisite's multi-day GTC question in a form a `DAY`-only
  provider can answer.
- [ ] 1.5 Raise the GTC conflict with the prerequisite's owner: Stage 1 task 1.2
  proposes a `SIMULATE` GTC order, which a `DAY`-only provider cannot accept. Verify
  by recording the decision taken on that task. Do not silently inherit or drop it.

## 2. Configuration

- [ ] 2.1 Extend the Stage 1 `Settings` with the journal configuration: the
  simulated-account allowlist, the journal path, explicit journal creation, and the
  bounded lock wait. Verify with settings tests for a missing allowlist in
  `SIMULATE`, a malformed allowlist, a non-positive lock wait, and a `READ_ONLY`
  configuration that sets none of them. (`U14`)
- [ ] 2.2 Ensure `READ_ONLY` resolves no journal path and constructs no store. Verify
  with a test asserting that no file is opened or created anywhere under the
  configured path during a `READ_ONLY` start and a read request. (`U14`)
- [ ] 2.3 Reject a `SIMULATE` configuration whose journal settings are incomplete,
  naming the variable. Verify that the process exits before any transport is served.
  (`U14`)

## 3. `ExecutionStore`

- [ ] 3.1 Add `services/execution_store.py` with short, worker-owned connections:
  open, use, close per unit of work, with no pool and no cross-thread sharing. Verify
  with a test that runs store operations from several worker threads and asserts no
  connection is reused across threads. (`U05`)
- [ ] 3.2 Apply the pragmas from design Decision 3: `journal_mode = DELETE`,
  `synchronous = EXTRA`, `foreign_keys = ON`, and `busy_timeout` from configuration.
  Verify by reading each pragma back, and by asserting no `-wal` or `-shm` file is
  created alongside the database. (`U05`, `U12`)
- [ ] 3.3 Implement the schema, its version in `PRAGMA user_version`, and the
  integrity check. Verify with tests for a newer version failing closed without
  modifying the file, and for a corrupt file failing closed. (`U12`)
- [ ] 3.4 Implement admission epochs: record an epoch, stamp each admitted operation,
  retire the previous epoch on start. Verify with tests for an identifier from a
  retired epoch being refused, and for an older restored database refusing
  identifiers it no longer holds rather than admitting them as new. (`U12`)
- [ ] 3.5 Implement explicit, short transactions with `BEGIN IMMEDIATE` for writes,
  and structurally prevent a transaction from spanning a caller-supplied callable.
  Verify with a test asserting no write transaction is open while a gateway call
  runs, and one asserting a bounded failure when the write lock is held past the
  configured wait. (`U05`, `U06`)
- [ ] 3.6 Implement atomic admission returning exactly one of reserved, already
  admitted, conflict, or refused, plus the transition log. Verify with concurrent
  admissions of one identifier resolving to a single reservation. (`U01`, `U02`)

## 4. Execution identity

- [ ] 4.1 Add `services/execution_identity.py`: canonicalization over environment,
  account, operation type and parameters, and a fingerprint over it, storing the
  canonical form so a conflict can be explained. Verify with tests for a matching
  retry, a conflicting retry naming the differing field, and a stable fingerprint
  across key ordering. (`U03`)
- [ ] 4.2 Carry prices as decimal strings end to end and compare by decimal value.
  Verify with tests that `350.0` and `350.00` are the same operation, that `350.01`
  and `350.02` conflict, and that a value with no exact binary representation is
  stored as supplied. (`U03`)
- [ ] 4.3 Fingerprint a modification over the caller's supplied patch, storing the
  merged broker request separately. Verify with a test where a partial fill changes
  the observed quantity between attempts and a price-only retry is still recognized
  as the same operation, and one where adding a field makes it a conflict. (`U04`)
- [ ] 4.4 Validate the operation identifier's form: non-empty, printable, within a
  bounded length, never generated or rewritten by the server. Verify with tests for a
  missing, blank, over-long and ill-formed identifier. (`U01`)

## 5. Trade service integration

- [ ] 5.1 Admit the operation, then commit intent and the dispatch marker, before
  Stage 1's pre-dispatch sequence runs and before the single SDK mutation invocation.
  Verify with a test asserting the commit precedes the invocation, and that exactly
  one invocation occurs per admitted identifier. (`U05`)
- [ ] 5.2 Refuse the mutation when storage cannot be written before dispatch, stating
  that no order was sent, and never dispatching unjournaled. Verify with storage
  fault injection. (`U06`)
- [ ] 5.3 Record outcomes onto Stage 1's three-way classification, keeping local
  lifecycle states distinct from broker statuses. Verify with fake-broker tests for
  an acknowledgement, a gateway error code, an SDK raise and a timeout. (`U08`)
- [ ] 5.4 Report late local failures with broker evidence separated from the local
  failure, naming conversion versus persistence, and stating whether the submission
  state is observed or durably stored. Verify with tests for an unconvertible
  receipt, a failed outcome write, and a readable order identifier surviving both.
  (`U09`)
- [ ] 5.5 Run subprocess crash tests at each persistence and dispatch boundary, and
  assert the recovered journal state at each. Verify that a crash after commitment
  leaves a record indicating an invocation may have started, never one indicating
  nothing happened. (`U07`)

## 6. Reconciliation and the recovery gate

- [ ] 6.1 Implement reconciliation over order and history-order observations for
  journal-owned operations, preferring a recorded broker order identifier and
  otherwise matching recorded attributes within the operation's submission window.
  Verify with a stateful fake broker for a unique match resolving the operation.
  (`U11`)
- [ ] 6.2 Leave an operation unresolved on zero matches and on two or more, never
  treating an empty result as proof of absence and never choosing between candidates.
  Verify with tests for both, and one asserting reconciliation issues no deal query
  and dispatches no mutation. (`U11`)
- [ ] 6.3 Record partial fills, complete fills and cancellations as mutually distinct
  broker statuses, including a cancellation that races a fill, which is never
  reported as cancelled. Verify with fake-broker race tests. (`U10`)
- [ ] 6.4 Implement the recovery review gate: enumerate non-terminal operations on
  start, refuse new mutations until review has run and accounted for each, keep reads
  and reconciliation available, and require review after a restore. Verify that the
  gate has no entry point other than a process start. (`U13`)

## 7. Policy and scope

- [ ] 7.1 Add the simulated-account allowlist to `TradingPolicy`, requiring
  verification that an allowlisted account is a simulated account, and refusing any
  other account before a gateway request. Verify with tests for an un-allowlisted
  account, an unverifiable account, and resolution when exactly one is eligible.
  (`U14`)
- [ ] 7.2 Enforce the version 1 paper scope, refusing anything outside it rather than
  dispatching unjournaled or adapting it. Verify with tests for market, stop,
  trailing-stop and combo routes, a non-`DAY` time in force, a non-US or non-equity
  instrument, a fractional quantity, a modification other than price or total
  quantity, and a cancellation that names no single order. (`U14`)
- [ ] 7.3 Assert that no tool argument can elevate the configured mode or reach
  `REAL`, and that paper records expose no promotion path into live execution. Verify
  with tests attempting each through the tool surface. (`U14`)

## 8. Tools and health

- [ ] 8.1 Require `operation_id` on paper mutations in `tools/trading.py`, accept
  decimal-string prices, and rewrite the docstrings to state that the caller owns the
  identifier and must preserve it across retries. Verify that the tool schemas list
  `operation_id` as required and that no default is supplied. (`U14`)
- [ ] 8.2 Report journal state in health without a gateway request: enabled or not,
  schema version, non-terminal count, and whether review is outstanding. Verify that
  health changes no operation state, never clears the gate, and leaves the top-level
  status driven by the probes alone. (`U14`)
- [ ] 8.3 Report unreachable or unwritable journal storage explicitly while keeping
  connectivity probes accurate. Verify with a test that makes storage unwritable and
  asserts both halves of the report. (`U14`)

## 9. Deployment and documentation

- [ ] 9.1 Add the optional `execution-data` volume and its directory, owned by the
  unprivileged user, leaving the OpenD volume's mount path and owning user id
  unchanged. Verify with container tests for recreation preserving the journal, for
  the OpenD volume being unaffected, and for permissions. (`C02`, `C03`)
- [ ] 9.2 Confirm a `READ_ONLY` deployment starts and serves with no journal volume
  mounted and no database anywhere. Verify with a container test. (`C01`)
- [ ] 9.3 Document the journal lifecycle, the recovery review gate, backup and
  restore of the single database file, and the reconciliation runbook, in
  `docs/state-and-restarts.md` and `docs/deploy-vps.md`, and update `.env.example`,
  `README.md` and the `openspec/config.yaml` context. Verify that no document
  describes automatic replay or a deal-based recovery path.

## 10. Automated acceptance

- [ ] 10.1 Run the full local gate and confirm each command passes: `uv run ruff check
  .`, `uv run ruff format --check .`, `uv run basedpyright`, `uv run pytest`, and
  `npx -y @fission-ai/openspec@1.13.1 validate --all --strict --no-interactive`.
- [ ] 10.2 Run the container tests for a missing mount and for restoring older
  storage, confirming that the restore requires recovery review before any mutation
  is admitted. (`C04`)

## 11. Manual verification — requires separate authorization

Not performed by the agent, and not performed on the strength of this document
alone.

- [ ] 11.1 With authorization, verify the real account and SDK capabilities recorded
  in group 1, then run a bounded paper order lifecycle — place, modify price, cancel
  — against a real simulated account, recording each operation identifier and its
  journal state. (`M01`, `M02`)
- [ ] 11.2 With authorization, induce a controlled response loss during a paper
  mutation and reconcile it, then confirm an unchanged operation token propagates
  through ZeroClaw and Telegram across a retry. (`M03`, `M04`)

## Scenario-to-test traceability

Every scenario in this change's delta specs maps to a planned test. 85 scenarios
across 16 requirements.

### `execution-journal`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Operation Admission and Execution Identity | Unknown identifier is reserved and dispatched once | `U01` |
| | Retry with an identical request returns the stored state | `U01` |
| | Same identifier with changed contents is a conflict | `U01` |
| | Missing identifier is refused without substitution | `U01` |
| | Concurrent admissions dispatch at most once | `U02` |
| | Identifier outside the accepted form is refused | `U01` |
| Request Canonicalization and Modification Identity | Prices are stored and compared as decimal values | `U03` |
| | A price difference is a conflict, not a rounding artefact | `U03` |
| | Price is not coerced through a binary float | `U03` |
| | Price-only retry survives an intervening partial fill | `U04` |
| | The merged broker request is recorded but does not define identity | `U04` |
| | A patch that adds a field is a different operation | `U04` |
| Pre-Dispatch Commitment and Single Invocation | Intent and dispatch marker are committed before the call | `U05` |
| | No transaction is held across the gateway call | `U05` |
| | Storage failure before dispatch refuses the mutation | `U06` |
| | A lock that cannot be acquired fails closed within a bound | `U06` |
| | Crash after commitment leaves a recoverable record | `U07` |
| | Storage is not bypassed when it is unavailable | `U06` |
| Outcome Classification and Preservation of Uncertainty | Acknowledgement is recorded as acknowledged, not filled | `U08` |
| | Gateway error code is recorded as unknown, not rejected | `U08` |
| | Timeout or dropped response is recorded as unknown | `U09` |
| | An unresolved operation is never replayed automatically | `U07` |
| | Broker status is recorded separately from local state | `U08` |
| | Fills and cancellations remain mutually distinct | `U10` |
| Late Local Failure Reporting | Receipt cannot be converted after acknowledgement | `U09` |
| | Outcome cannot be stored after acknowledgement | `U09` |
| | Durably stored submission state is reported as such | `U09` |
| | A readable order identifier is reported even when a later step fails | `U09` |
| Bounded Reconciliation of Journal-Owned Orders | A unique match resolves the operation | `U11` |
| | An empty result does not resolve the operation | `U11` |
| | Ambiguous matches do not resolve the operation | `U11` |
| | Reconciliation does not use deal records | `U11` |
| | Reconciliation sends no order mutation | `U11` |
| | A recorded broker order identifier is preferred for matching | `U11` |
| | Orders the journal does not own are not reconciled | `U11` |
| Storage Lifecycle and Admission Epochs | Missing storage is not silently recreated | `U12` |
| | Explicit initialization creates the journal | `U12` |
| | A newer schema version fails closed | `U12` |
| | A failed integrity check fails closed | `U12` |
| | An identifier from a retired epoch is refused | `U12` |
| | An older restored backup does not resurrect identifiers as new | `U12`, `C04` |
| Recovery Review Gate | New mutations are refused until review has run | `U13` |
| | Reads and reconciliation remain available while the gate holds | `U13` |
| | A clean journal reviews to empty | `U13` |
| | Unresolved operations keep the gate closed | `U13` |
| | A restored journal requires review before mutations | `U13`, `C04` |

### `trading-policy`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Enforce Configured Trading Mode | Read-only deployment | `U14` |
| | Read-only deployment needs no journal | `U14`, `C01` |
| | Simulation deployment | `U14` |
| | Explicit real deployment | `U14` |
| | Password does not enable trading mode | `U14` |
| | Reject invalid configuration | `U14` |
| Simulated Account Allowlist | SIMULATE mode without an allowlist | `U14` |
| | Mutation to an un-allowlisted account is refused | `U14` |
| | An account that is not simulated is refused | `U14` |
| | Paper records are never promoted to live execution | `U14` |
| Version 1 Paper Execution Scope | An unsupported order type is refused, not bypassed | `U14` |
| | An unsupported time in force is refused | `U14` |
| | An unsupported instrument or quantity is refused | `U14` |

### `order-placement`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Support Placing Orders | Place Limit Buy Order | `U08` |
| | Place Market Buy Order | `U08` |
| | Environment omitted | `U14` |
| | Journaled paper placement commits before dispatch | `U05`, `M02` |
| | Market order is refused under journaled paper execution | `U14` |
| | Repeated placement returns the stored receipt | `U01` |

### `order-modification`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Support Modifying Orders | Modify Order Price | `U08` |
| | Price-only change is checked against the full order | `U14` |
| | Quantity-only change is checked against the full order | `U14` |
| | Re-enabling an order is checked | `U14` |
| | Unknown order | `U14` |
| | Journaled paper modification commits before dispatch | `U05`, `M02` |
| | Repeated modification returns the stored outcome | `U04` |
| | Modification outside the version 1 paper scope is refused | `U14` |
| Support Cancelling Orders | Cancel Order | `U08` |
| | Cancellation with an ambiguous account | `U14` |
| | Cancellation racing a fill is recorded factually | `U10` |
| | Bulk cancellation is out of scope | `U14` |

### `container-deployment`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Execution Journal Storage Persistence | Container recreation preserves the journal | `C02` |
| | Journal storage is owned by the unprivileged user | `C03` |
| | A deployment without journaled paper execution needs no volume | `C01` |
| | Restored older storage requires review before mutations | `C04` |

### `system-health`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Report Execution Journal State | Journal not enabled | `U14` |
| | Journal enabled and reviewed | `U14` |
| | Unresolved operations are reported without being changed | `U14` |
| | Unreachable journal storage is reported without breaking probes | `U14` |
