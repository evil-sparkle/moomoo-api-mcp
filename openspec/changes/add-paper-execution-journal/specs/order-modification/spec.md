## MODIFIED Requirements

### Requirement: Support Modifying Orders

The system MUST allow modifying price, quantity, or other attributes of an open order.
In paper trading (`SIMULATE` mode), modification SHALL require a caller-provided `operation_id`, SHALL be durably recorded in the execution journal before gateway dispatch, and SHALL suppress duplicate submissions.

#### Scenario: Modify Order Price
- **GIVEN** an open order with ID '12345' in `SIMULATE` mode
- **WHEN** a user calls `modify_order(operation_id='op-mod-1', order_id='12345', modify_order_op='NORMAL', price=355.0, trd_env='SIMULATE')`
- **THEN** the modification operation SHALL be committed to the execution journal in state `PENDING_SUBMIT`
- **AND** the order modification request SHALL be sent to the OpenD gateway
- **AND** the tool SHALL return the modification status.

#### Scenario: Duplicate modification returns stored result
- **GIVEN** `modify_order` with `operation_id='op-mod-1'` was already processed
- **WHEN** `modify_order` is called again with `operation_id='op-mod-1'` and identical arguments
- **THEN** the system SHALL return the stored outcome without sending another modification request to the gateway.

### Requirement: Support Cancelling Orders

The system MUST allow cancelling an open order.
In paper trading (`SIMULATE` mode), cancellation SHALL require a caller-provided `operation_id`, SHALL be recorded in the execution journal, and SHALL reconcile actual broker order status so that racing fills do not report false cancellations.

#### Scenario: Cancel Order
- **GIVEN** an open order with ID '67890' in `SIMULATE` mode
- **WHEN** a user calls `cancel_order(operation_id='op-cancel-1', order_id='67890', trd_env='SIMULATE')`
- **THEN** the cancellation operation SHALL be committed to the execution journal in state `PENDING_SUBMIT`
- **AND** the order cancellation request SHALL be sent to the OpenD gateway
- **AND** the tool SHALL return the cancellation status.

#### Scenario: Cancellation racing with fill does not falsely claim cancelled
- **GIVEN** an open order was filled by the broker immediately before or during the cancellation request
- **WHEN** the cancellation request is processed and the broker reports that the order has already filled or cannot be cancelled
- **THEN** the system SHALL NOT claim or report that the order was cancelled
- **AND** the journal SHALL reflect the factual filled state (`FILLED_ALL` or `FILLED_PART`) returned by the broker.
