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

### Requirement: Support Cancelling Orders

The system MUST allow cancelling an open order. `trd_env` SHALL be supplied
explicitly; it has no default. Account resolution for `acc_id="0"` follows the same
rule as modification. Cancellation SHALL remain permitted while the service is
in the `HALTED` execution state.

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
