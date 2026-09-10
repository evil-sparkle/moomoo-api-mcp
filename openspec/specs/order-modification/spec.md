# order-modification Specification

## Purpose

Provides capabilities for modifying open orders (adjusting quantity, price, or limit parameters) and cancelling open orders.

## Requirements

### Requirement: Support Modifying Orders

The system MUST allow modifying price, quantity, or other attributes of an open order.

#### Scenario: Modify Order Price

Given an open order with ID '12345'
When a user calls `modify_order(order_id='12345', op='NORMAL', price=355.0)`
Then the order modification request should be sent to Moomoo
And the tool should return success status.

### Requirement: Support Cancelling Orders

The system MUST allow cancelling an open order.

#### Scenario: Cancel Order

Given an open order with ID '67890'
When a user calls `cancel_order(order_id='67890')`
Then the order cancellation request (MODIFY with CANCEL op) should be sent to Moomoo
And the tool should return success status.
