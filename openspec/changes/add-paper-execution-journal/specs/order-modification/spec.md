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

A modification that is journaled paper execution SHALL additionally carry a
non-empty, caller-supplied `operation_id`, express any price as a decimal string, and
be committed to the journal before the SDK mutation invocation.

Its identity SHALL be established from the **caller's supplied patch**, not from the
merged request that is dispatched. The merged request SHALL be stored separately, as
required by `execution-journal` › Request Canonicalization and Modification Identity.
Version 1 supports price and total-quantity modifications only.

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

#### Scenario: Journaled paper modification commits before dispatch

- **GIVEN** journaled paper execution is active
- **WHEN** a price modification is requested with `operation_id='op-m1'`, an explicit
  `trd_env='SIMULATE'` and a decimal-string price
- **THEN** the operation SHALL be committed to the journal before the SDK mutation
  invocation
- **AND** the assessment of the merged order SHALL still apply

#### Scenario: Repeated modification returns the stored outcome

- **GIVEN** a journaled modification with `operation_id='op-m1'` was already
  processed
- **WHEN** the caller re-sends it with the identical patch
- **THEN** the system SHALL return the stored outcome
- **AND** SHALL NOT make a second SDK mutation invocation

#### Scenario: Modification outside the version 1 paper scope is refused

- **GIVEN** journaled paper execution is active
- **WHEN** a modification other than a price or total-quantity change is requested
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
- **AND** SHALL NOT dispatch it unjournaled

### Requirement: Support Cancelling Orders

The system MUST allow cancelling an open order. `trd_env` SHALL be supplied
explicitly; it has no default. Account resolution for `acc_id="0"` follows the same
rule as modification. Cancellation SHALL remain permitted while the service is
in the `HALTED` execution state.

A cancellation that is journaled paper execution SHALL carry a non-empty,
caller-supplied `operation_id` and be committed to the journal before the SDK
mutation invocation. Version 1 supports cancelling one named order at a time.

When the broker reports that the order had already filled, or otherwise cannot be
cancelled, the system SHALL record and report that factual state. It SHALL NOT report
the order as cancelled.

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

#### Scenario: Bulk cancellation is out of scope

- **GIVEN** journaled paper execution is active
- **WHEN** a cancellation is requested that does not name a single order
- **THEN** the system SHALL refuse it as out of scope for version 1 paper execution
