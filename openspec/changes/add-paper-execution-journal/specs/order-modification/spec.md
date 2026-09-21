# Spec Delta

## MODIFIED Requirements

### Requirement: Support Modifying Orders

The system MUST allow modifying the price, quantity or other attributes of an open
order. `trd_env` SHALL be supplied explicitly; it has no default.

When `acc_id` is `"0"`, the service SHALL resolve it only when exactly one eligible
account exists for the environment. In REAL, an account is eligible only if it is
allowlisted. Otherwise the modification SHALL be refused as not sent.

Before any order-mutating gateway request for a `NORMAL` or `ENABLE` operation, the
service SHALL retrieve the existing order by its identifier. It SHALL then assess the
order that would result:

- **Fields.** The requested quantity and price replace the existing ones. Fields that
  were not supplied keep their existing values. The code, order type and legs come
  from the existing order.
- **Checks.** The order value validation, quantity limit and notional limit apply to
  the resulting order exactly as they do to a new placement. That includes the
  reference-price rule, using the existing order's side, a fresh snapshot, and, for
  `ENABLE`, the existing order's own fields.
- **Refusal.** If the order cannot be found in the requested account and
  environment, or its fields cannot be read, the modification SHALL be refused as
  not sent.

`CANCEL`, `DISABLE` and `DELETE` do not add exposure and are not assessed against
limits.

A modification that is journaled paper execution SHALL additionally:

- carry a non-empty, caller-supplied `operation_id` and a valid `admission_epoch`;
- express any price as a strict decimal string;
- have its admission and intent persisted in state `ADMITTED` before pre-dispatch
  safety checks and target order retrieval run;
- record pre-dispatch refusals as `REFUSED` with disposition `NOT_SENT` without
  committing a dispatch marker;
- commit `DISPATCHING` only after all checks pass, prior to the SDK mutation
  invocation;
- return an immediate bounded in-flight response (`IN_FLIGHT`) on an in-flight retry;
- establish its identity from the **target order ID and original caller patch**, not
  from the merged broker request.

Version 1 supports price and total-quantity modifications only. Locating the target
order at the broker does NOT prove that a modification succeeded.

#### Scenario: Modify Order Price

- **GIVEN** an open order with ID '12345'
- **WHEN** a user calls `modify_order(order_id='12345', modify_order_op='NORMAL',
  price=355.0, trd_env='REAL')`
- **THEN** the order modification request SHALL be sent to Moomoo
- **AND** the tool SHALL return success status

#### Scenario: Price-only change is checked against the full order

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000` and an open US
  stock BUY `NORMAL` order for 10 shares at 50
- **WHEN** a `NORMAL` modification changes only the price to 500
- **THEN** the service SHALL assess 10 × 500 = 5,000 USD
- **AND** SHALL refuse the modification before any order-mutating gateway request

#### Scenario: Quantity-only change is checked against the full order

- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL_BY_CURRENCY` is `USD:1000` and an open US
  stock BUY `NORMAL` order for 10 shares at 50
- **WHEN** a `NORMAL` modification changes only the quantity to 100
- **THEN** the service SHALL assess 100 × 50 = 5,000 USD and refuse it

#### Scenario: Re-enabling an order is checked

- **GIVEN** a disabled order whose value exceeds the configured notional cap
- **WHEN** an `ENABLE` modification is requested
- **THEN** the service SHALL refuse it before any order-mutating gateway request

#### Scenario: Unknown order

- **WHEN** a `NORMAL` modification names an order that is not found
- **THEN** the service SHALL refuse it as not sent

#### Scenario: Journaled paper modification commits admission before pre-dispatch checks

- **GIVEN** journaled paper execution is active
- **WHEN** a price modification is requested with `operation_id='op-m1'`,
  `admission_epoch='epoch-1'`, an explicit `trd_env='SIMULATE'` and a decimal-string
  price
- **THEN** the operation SHALL be persisted in state `ADMITTED` before the target
  order is retrieved or limits assessed
- **AND** the dispatch marker (`DISPATCHING`) SHALL be committed only after checks
  pass, prior to the SDK mutation invocation

#### Scenario: Finding target order does not prove modification succeeded

- **GIVEN** an operation `op-m2` modifying order `order-500` is in `UNKNOWN_OUTCOME`
- **WHEN** the order `order-500` is located in the account
- **THEN** locating `order-500` SHALL NOT be treated as proof that `op-m2` succeeded
- **AND** the operation SHALL remain unresolved without verified correlation

#### Scenario: Repeated modification returns the stored outcome

- **GIVEN** a journaled modification with `operation_id='op-m1'` was already
  processed
- **WHEN** the caller re-sends it with the identical patch
- **THEN** the system SHALL return the stored outcome
- **AND** SHALL NOT make a second SDK mutation invocation

#### Scenario: In-flight modification retry returns immediate bounded response

- **GIVEN** a journaled modification `op-m3` is in state `DISPATCHING`
- **WHEN** a retry arrives carrying `operation_id='op-m3'` and identical patch
- **THEN** the system SHALL return an immediate bounded in-flight response
- **AND** SHALL NOT block or initiate a second gateway request

#### Scenario: Modification outside the version 1 paper scope is refused

- **GIVEN** journaled paper execution is active
- **WHEN** a modification other than a price or total-quantity change is requested
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
- **AND** SHALL NOT dispatch it unjournaled

### Requirement: Support Cancelling Orders

The system MUST allow cancelling an open order. `trd_env` SHALL be supplied
explicitly; it has no default. Account resolution for `acc_id="0"` follows the same
rule as modification. Cancellation SHALL remain permitted while the service is
in the `HALTED` execution state of the Stage 1 trade relock mechanism.

A cancellation that is journaled paper execution SHALL carry a non-empty,
caller-supplied `operation_id` with `admission_epoch`, persist admission in
`ADMITTED` before pre-dispatch checks, and commit `DISPATCHING` before the SDK
mutation invocation. Version 1 supports cancelling one named order at a time.

When the broker reports that the order had already filled, or otherwise cannot be
cancelled, the system SHALL record and report that factual state. It SHALL NOT report
the order as cancelled.

When paper execution is in state `JOURNAL_BLOCKED`, cancellation requests SHALL NOT
bypass the journal or fall back to unjournaled execution.

#### Scenario: Cancel Order

- **GIVEN** an open order with ID '67890'
- **WHEN** a user calls `cancel_order(order_id='67890', trd_env='REAL')`
- **THEN** the order cancellation request (MODIFY with CANCEL op) SHALL be sent to
  Moomoo
- **AND** the tool SHALL return success status

#### Scenario: Cancellation with an ambiguous account

- **GIVEN** two allowlisted REAL accounts
- **WHEN** `cancel_order(order_id='67890', trd_env='REAL')` is called without an
  `acc_id`
- **THEN** the service SHALL refuse it as not sent and ask for an explicit `acc_id`

#### Scenario: Cancellation racing a fill is recorded factually

- **GIVEN** a journal-owned order filled at the broker immediately before or during
  the cancellation request
- **WHEN** the broker reports that the order has already filled or cannot be
  cancelled
- **THEN** the system SHALL NOT report the order as cancelled
- **AND** the journal SHALL record the factual broker status

#### Scenario: Cancellation does not fall back to unjournaled execution when journal is blocked

- **GIVEN** paper execution is in state `JOURNAL_BLOCKED`
- **WHEN** a cancellation is requested
- **THEN** the cancellation SHALL be refused
- **AND** the system SHALL NOT dispatch the cancellation unjournaled

#### Scenario: Bulk cancellation is out of scope

- **GIVEN** journaled paper execution is active
- **WHEN** a cancellation is requested that does not name a single order
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
