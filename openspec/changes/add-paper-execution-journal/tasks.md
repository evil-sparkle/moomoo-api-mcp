# Tasks

No task below is complete. Nothing here is implemented, and no runtime milestone is
claimed. Task groups 1, 10 and 11 gate the others: implementation begins only after
group 1 resolves the provider facts and the prerequisite reconciliation, and after
implementation is separately authorized. Group 11 requires its own authorization
again.

## Test layers

| ID | Layer | What it proves |
| --- | --- | --- |
| `U01`–`U18` | Automated unit and integration, fake broker | The journal's own logic, lifecycle transitions, and fault behaviour |
| `C01`–`C04` | Isolated container tests | Volume topology, permissions, single-process locking, persistence, restoration |
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
  configured path during a `READ_ONLY` start and a read request. (`U14`, `C01`)
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
- [ ] 3.4 Enforce single executor process access using an exclusive non-blocking OS
  file lock (`fcntl.flock(LOCK_EX | LOCK_NB)`) on `execution.lock`. Verify with a test
  asserting that a second concurrent process fails closed immediately. (`U16`, `C03`)
- [ ] 3.5 Implement admission epochs: record a fresh epoch on startup, stamp each
  admitted operation, evaluate existing identifiers across all epochs first, and
  refuse unknown tokens carrying non-current epochs without rewrite. Verify with tests
  for identifier lookups across epochs and refusal of unknown retired-epoch tokens.
  (`U01`, `U12`, `C04`)
- [ ] 3.6 Implement explicit, short transactions with `BEGIN IMMEDIATE` for writes,
  and structurally prevent a database transaction from spanning broker I/O or
  caller-supplied callables. Verify with a test asserting no write transaction is open
  while a gateway call runs, and one asserting a bounded failure when the write lock
  is held past the configured wait. (`U05`, `U06`)
- [ ] 3.7 Implement two-phase admission: persist the token and request in state
  `ADMITTED`, permit transition to `REFUSED` with disposition `NOT_SENT` without
  committing a dispatch marker, and commit `DISPATCHING` only after pre-dispatch checks
  pass. Verify with tests asserting no dispatch marker exists for pre-dispatch
  refusals. (`U01`, `U05`)
- [ ] 3.8 Implement the audit log table `recovery_audit` for recording operator
  recovery acknowledgements and state transitions. Verify with tests asserting durable
  audit rows for operator recovery actions. (`U17`)

## 4. Execution identity

- [ ] 4.1 Add `services/execution_identity.py`: canonicalization over environment,
  account, operation type and parameters, and a fingerprint over it, storing the
  canonical form so a conflict can be explained. Verify with tests for a matching
  retry, a conflicting retry naming the differing field, and a stable fingerprint
  across key ordering. (`U03`)
- [ ] 4.2 Carry prices as strict decimal strings end to end and compare by decimal
  value. Verify with tests that `350.0` and `350.00` are the same operation, that
  `350.01` and `350.02` conflict, that malformed price strings are refused, and that
  a value with no exact binary representation is stored as supplied. (`U03`)
- [ ] 4.3 Fingerprint a modification over the target order ID and caller's supplied
  patch, storing the merged broker request separately. Verify with a test where a
  partial fill changes the observed quantity between attempts and a price-only retry
  is still recognized as the same operation, and one where adding a field makes it a
  conflict. (`U04`)
- [ ] 4.4 Validate the operation identifier's form: non-empty, printable, within a
  bounded length, never generated or rewritten by the server, with frozen account
  binding. Verify with tests for a missing, blank, over-long, ill-formed identifier,
  and same-ID account switching. (`U01`)
- [ ] 4.5 Return an immediate bounded response (`IN_FLIGHT`) when an identical retry
  arrives while the operation is in state `DISPATCHING`. Verify that the caller does
  not block indefinitely and no second dispatch is initiated. (`U02`)

## 5. Trade service integration and dispatch lifecycle

- [ ] 5.1 Persist admission and intent first in state `ADMITTED`, run pre-dispatch
  safety checks, record ordinary pre-dispatch refusals as `REFUSED` / `NOT_SENT`
  without committing a dispatch marker, and commit `DISPATCHING` only after checks
  pass. Verify with tests tracing state transitions and marker omission. (`U05`)
- [ ] 5.2 Implement serialized dispatch execution: check the recovery gate and
  journal state, commit `DISPATCHING`, execute exactly one SDK mutation invocation,
  and commit the outcome, without holding a database transaction across broker I/O.
  Verify with tests for serialized execution and single invocation. (`U05`)
- [ ] 5.3 Refuse mutations when storage cannot be written before dispatch, stating
  that no order was sent, and never dispatch unjournaled. Verify with storage fault
  injection. (`U06`)
- [ ] 5.4 Record outcomes onto Stage 1's three-way classification, keeping local
  lifecycle states distinct from broker statuses. Transition to `JOURNAL_BLOCKED`
  upon unknown dispatched outcomes or failed outcome persistence, ensuring
  `lock_trade` does not clear journal blocking and that cancellations do not fall
  back to unjournaled execution. (`U08`, `U10`, `U15`)
- [ ] 5.5 Report late local failures with broker evidence separated from the local
  failure, naming conversion versus persistence, and stating whether the submission
  state is observed or durably stored. Verify with tests for an unconvertible
  receipt, a failed outcome write, and a readable order identifier surviving both.
  (`U09`)
- [ ] 5.6 Run subprocess crash tests covering all crash windows: between admit and
  dispatch marker commit, between marker commit and SDK call, during SDK call, and
  between SDK call and outcome commit. Assert the recovered state for each. (`U07`)

## 6. Reconciliation, recovery review gate, and operator acknowledgement

- [ ] 6.1 Implement candidate matching versus proof of ownership in reconciliation:
  attribute/time matches are treated as candidates only; automatic association to
  `RECONCILED` requires reliable broker order ID or verified provider correlation.
  Verify with a stateful fake broker. (`U11`)
- [ ] 6.2 Assert that unrelated identical broker orders do not resolve an operation,
  and that locating a modification or cancellation target order does not prove that
  the mutation succeeded. Verify with tests for both. (`U11`)
- [ ] 6.3 Record partial fills, complete fills and cancellations as mutually distinct
  broker statuses, including a cancellation that races a fill, which is never
  reported as cancelled. Verify with fake-broker race tests. (`U10`)
- [ ] 6.4 Implement the recovery review gate at startup: enumerate non-terminal
  operations, refuse new mutations until reviewed, keep reads and reconciliation
  available, and handle restored backups with missing rows and stale existing rows.
  Verify with tests for startup review gating and restore detection. (`U13`, `C04`)
- [ ] 6.5 Implement the named operator recovery acknowledgement tool
  `acknowledge_recovery`: enforce operator authorization, require explicit operator
  ID, durable reason, and verified broker evidence reference, and write durable audit
  records to `recovery_audit`. Verify that unauthorized attempts fail closed. (`U17`)
- [ ] 6.6 Support evidence-backed accounting of terminal target orders
  (`TERMINAL_ACCOUNTED`) to release the recovery gate without claiming uncertain
  mutations succeeded, and keep execution blocked when evidence is insufficient.
  Verify with tests. (`U17`)

## 7. Policy and scope

- [ ] 7.1 Add the simulated-account allowlist to `TradingPolicy`, requiring
  verification that an allowlisted account is a simulated account, and refusing any
  other account before a gateway request. Verify with tests for an un-allowlisted
  account, an unverifiable account, and resolution when exactly one is eligible.
  (`U14`)
- [ ] 7.2 Enforce independent paper journal blocking: when `JOURNAL_BLOCKED`, all
  paper mutations fail closed; `lock_trade` has no effect on journal blocking.
  Verify with tests. (`U15`)
- [ ] 7.3 Enforce the version 1 paper scope, refusing anything outside it rather than
  dispatching unjournaled or adapting it. Verify with tests for market, stop,
  trailing-stop and combo routes, a non-`DAY` time in force, a non-US or non-equity
  instrument, a fractional quantity, a modification other than price or total
  quantity, and a cancellation that names no single order. (`U14`)
- [ ] 7.4 Assert that no tool argument can elevate the configured mode or reach
  `REAL`, and that paper records expose no promotion path into live execution. Verify
  with tests attempting each through the tool surface. (`U14`)

## 8. Tools and health

- [ ] 8.1 Require `operation_id` and `admission_epoch` on paper mutations in
  `tools/trading.py`, accept strict decimal-string prices, and rewrite docstrings to
  state identity preservation rules. Verify schemas. (`U01`, `U03`, `U14`)
- [ ] 8.2 Expose the `acknowledge_recovery` tool for authorized operators with schema
  validation for operator ID, resolution, reason, and evidence reference. (`U17`)
- [ ] 8.3 Report journal state in health without a gateway request: state (`DISABLED`,
  `READY`, `REVIEW_PENDING`, `JOURNAL_BLOCKED`), schema version, active epoch,
  non-terminal count, independent of `execution_halted`. Verify with tests. (`U18`)
- [ ] 8.4 Report unreachable or unwritable journal storage explicitly while keeping
  connectivity probes accurate. Verify with a test making storage unwritable. (`U18`)

## 9. Deployment and documentation

- [ ] 9.1 Add the optional `execution-data` volume and its directory, owned by the
  unprivileged user (uid 10001), leaving the OpenD volume's mount path and owning uid
  unchanged. Verify with container tests for recreation, OpenD isolation, and single
  process lock enforcement. (`C02`, `C03`)
- [ ] 9.2 Confirm a `READ_ONLY` deployment starts and serves with no journal volume
  mounted and no database anywhere. Verify with a container test. (`C01`)
- [ ] 9.3 Document consistent backup procedures (using SQLite online backup API or
  stopped containers) and supported POSIX locking/fsync semantics in deployment guides.
  Verify docs. (`C02`)
- [ ] 9.4 Document the journal lifecycle, two-phase dispatch, recovery review gate,
  operator acknowledgement runbook, and restore limitations in
  `docs/state-and-restarts.md` and `docs/deploy-vps.md`, and update `.env.example`,
  `README.md` and `openspec/config.yaml`. Verify docs.

## 10. Automated acceptance

- [ ] 10.1 Run the full local gate and confirm each command passes: `uv run ruff check
  .`, `uv run ruff format --check .`, `uv run basedpyright`, `uv run pytest`, and
  `npx -y @fission-ai/openspec@1.13.1 validate --all --strict --no-interactive`.
- [ ] 10.2 Run container tests for missing mount, single-process locking, consistent
  backup, and restoring older storage, confirming that restore requires recovery
  review. (`C01`–`C04`)
- [ ] 10.3 Verify that all 122 spec scenarios map 1:1 to planned tests in the
  traceability table.

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

Every scenario in this change's delta specs maps to a planned test. 122 scenarios
across 16 requirements.

### `execution-journal`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Operation Admission and Execution Identity | Unknown identifier with current epoch is admitted | `U01` |
| | Existing identifier lookup precedes epoch validation on retry | `U01` |
| | Retry with an identical request returns the stored state | `U01` |
| | Same identifier with changed contents is a conflict | `U01` |
| | Same-ID corrected request is refused as a conflict | `U01` |
| | Missing identifier is refused without substitution | `U01` |
| | Concurrent admissions dispatch at most once | `U02` |
| | In-flight retry receives immediate bounded response | `U02` |
| | Identifier outside the accepted form is refused | `U01` |
| | Unknown identifier from non-current epoch is refused | `U12` |
| | Admitted operation has frozen account binding | `U01` |
| Request Canonicalization and Modification Identity | Prices are stored and compared as decimal values | `U03` |
| | A price difference is a conflict, not a rounding artefact | `U03` |
| | Price is not coerced through a binary float | `U03` |
| | Malformed decimal string price is refused | `U03` |
| | Price-only retry survives an intervening partial fill | `U04` |
| | Modification identity binds target order ID and caller patch | `U04` |
| | The merged broker request is recorded but does not define identity | `U04` |
| | A patch that adds a field is a different operation | `U04` |
| Pre-Dispatch Commitment and Single Invocation | Admission and intent are persisted before pre-dispatch safety checks | `U05` |
| | Pre-dispatch refusal is recorded as refused and not sent without dispatch marker | `U05` |
| | Intent and dispatch marker are committed before SDK invocation | `U05` |
| | No transaction is held across the gateway call | `U05` |
| | Serialized execution prevents concurrent SDK dispatches | `U05` |
| | Storage failure before dispatch refuses the mutation | `U06` |
| | A lock that cannot be acquired fails closed within a bound | `U06` |
| | Crash after commitment leaves a recoverable record | `U07` |
| | Storage is not bypassed when it is unavailable | `U06` |
| | Crash before dispatch marker leaves admitted pre-dispatch row | `U07` |
| | Crash during SDK invocation preserves dispatching state | `U07` |
| | Crash after SDK invocation but before outcome persistence preserves uncertainty | `U07` |
| Outcome Classification and Preservation of Uncertainty | Acknowledgement is recorded as acknowledged, not filled | `U08` |
| | Gateway error code is recorded as unknown, not rejected | `U08` |
| | Timeout or dropped response is recorded as unknown | `U08` |
| | An unresolved operation is never replayed automatically | `U07` |
| | Broker status is recorded separately from local state | `U08` |
| | Fills and cancellations remain mutually distinct | `U10` |
| | Dispatched uncertainty blocks subsequent automated paper mutations | `U15` |
| | Unjournaled cancellation fallback is forbidden when journal is blocked | `U15` |
| | Journal blocking is independent of trade relock halt and not cleared by lock_trade | `U15` |
| Late Local Failure Reporting | Receipt cannot be converted after acknowledgement | `U09` |
| | Outcome cannot be stored after acknowledgement | `U09` |
| | Durably stored submission state is reported as such | `U09` |
| | A readable order identifier is reported even when a later step fails | `U09` |
| Bounded Reconciliation of Journal-Owned Orders | A unique match with reliable broker identity resolves the operation | `U11` |
| | Attribute and time matches are candidates, not proof of ownership | `U11` |
| | Unrelated identical broker orders do not resolve the operation | `U11` |
| | Finding modification target order does not prove modification succeeded | `U11` |
| | Finding cancellation target order does not prove cancellation succeeded | `U11` |
| | An empty result does not resolve the operation | `U11` |
| | Ambiguous matches do not resolve the operation | `U11` |
| | Reconciliation does not use deal records | `U11` |
| | Reconciliation sends no order mutation | `U11` |
| | Orders the journal does not own are not reconciled | `U11` |
| Storage Lifecycle and Admission Epochs | Missing storage is not silently recreated | `U12` |
| | Explicit initialization creates the journal | `U12` |
| | A newer schema version fails closed | `U12` |
| | A failed integrity check fails closed | `U12` |
| | Single executor process is enforced by process file lock | `U16` |
| | Concurrent second executor process fails closed | `U16` |
| | Storage failure recovery requires restarting with healthy storage | `U16` |
| | Concurrent requests during storage failure fail closed | `U16` |
| | Restored backup with missing rows and stale existing rows refuses missing tokens | `U12`, `C04` |
| | Journal continuity is required before inferring not sent from pre-dispatch row | `U12`, `C04` |
| Recovery Review Gate and Operator Acknowledgement | New mutations are refused until review has run | `U13` |
| | Reads and reconciliation remain available while the gate holds | `U13` |
| | A clean journal reviews to empty | `U13` |
| | Unresolved operations keep the gate closed | `U13` |
| | A restored journal requires review before mutations | `U13`, `C04` |
| | Named operator recovery acknowledgement with valid evidence satisfies gate condition | `U17` |
| | Unauthorized recovery acknowledgement is refused | `U17` |
| | Insufficient evidence keeps execution blocked | `U17` |
| | Recovery acknowledgement records durable audit entry | `U17` |
| | Evidence-backed accounting of terminal target accounts for exposure without false success claim | `U17` |

### `trading-policy`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Enforce Configured Trading Mode | Read-only deployment | `U14` |
| | Read-only deployment needs no journal | `U14`, `C01` |
| | Simulation deployment | `U14` |
| | Paper journal blocking is independent of REAL relock halt | `U15` |
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
| | Journaled paper placement persists admission then checks before dispatching | `U05`, `M02` |
| | Pre-dispatch limit refusal records refused not sent without dispatch marker | `U05` |
| | In-flight retry returns immediate bounded response | `U02` |
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
| | Journaled paper modification commits admission before pre-dispatch checks | `U05`, `M02` |
| | Finding target order does not prove modification succeeded | `U11` |
| | Repeated modification returns the stored outcome | `U04` |
| | In-flight modification retry returns immediate bounded response | `U02` |
| | Modification outside the version 1 paper scope is refused | `U14` |
| Support Cancelling Orders | Cancel Order | `U08` |
| | Cancellation with an ambiguous account | `U14` |
| | Cancellation racing a fill is recorded factually | `U10` |
| | Cancellation does not fall back to unjournaled execution when journal is blocked | `U15` |
| | Bulk cancellation is out of scope | `U14` |

### `container-deployment`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Execution Journal Storage Persistence | Container recreation preserves the journal | `C02` |
| | Journal storage is owned by the unprivileged user | `C03` |
| | Single executor process is enforced across container environment | `C03` |
| | Supported consistent backup procedure does not corrupt database | `C02` |
| | A deployment without journaled paper execution needs no volume | `C01` |
| | Restored older storage requires review before mutations | `C04` |

### `system-health`

| Requirement | Scenario | Test |
| --- | --- | --- |
| Report Execution Journal State | Journal not enabled | `U18` |
| | Journal enabled and reviewed | `U18` |
| | Journal blocked by unresolved outcome reported independently of relock halt | `U18` |
| | Unresolved operations are reported without being changed | `U18` |
| | Unreachable journal storage is reported without breaking probes | `U18` |
