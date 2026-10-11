# order-retrieval Specification

## Purpose

Provides capabilities for retrieving active orders, order status histories, and executed trade deals.

## Requirements

### Requirement: Support Getting Order List
The system MUST allow retrieving the provider's current order list filtered by environment and account. The scope SHALL include open orders and recently terminal orders as documented, and SHALL NOT be described as a complete calendar-day ledger.

#### Scenario: Get Today's Orders
- **WHEN** a user calls get_orders with an explicit environment
- **THEN** the tool SHALL return the provider's current order rows including identifier, code, side, price, quantity and status

### Requirement: Support Getting Deal List
The system MUST allow retrieving executed deals in REAL accounts and SHALL refuse SIMULATE current and historical deal queries before dispatch.

#### Scenario: Get Today's Deals
- **WHEN** a user calls get_deals for a REAL account
- **THEN** the tool SHALL return executed deal identifiers, order identifiers, code, price and quantity

#### Scenario: Paper deal query refused
- **WHEN** a user requests current or historical deals in SIMULATE
- **THEN** the call SHALL fail explicitly without invoking an unsupported SDK query

### Requirement: Exact Single Order Lookup
Current lookup SHALL use the exact broker order identifier. Historical lookup SHALL require a bounded date scope and match the identifier locally. Results SHALL distinguish absence within the queried scope from proof that no order ever existed, preserve exact identifiers, and reject ambiguous duplicate matches.

#### Scenario: Historical lookup has no broker identifier filter
- **WHEN** a historical single-order lookup is requested
- **THEN** date/code filters SHALL be sent to the broker and exact identifier matching SHALL occur locally

### Requirement: Actual Order Fees
The system SHALL retrieve actual fees for at most 400 exact order identifiers per request, only for supported REAL accounts. Paper and Moomoo CA accounts SHALL be refused. Missing currencies or estimates SHALL NOT be invented.

#### Scenario: Unsupported fee environment
- **WHEN** a paper or Moomoo CA account requests order fees
- **THEN** the request SHALL fail before the fee SDK call

### Requirement: Immutable Bounded Query Pages
The system SHALL offer normalized bounded pages while preserving existing list tools. Cursors SHALL bind an immutable expiring server snapshot to query filters, account/environment and page size. Oversized snapshots, expired cursors and changed scope SHALL be rejected explicitly.

#### Scenario: Continuation avoids mutable requery
- **WHEN** a valid cursor is continued
- **THEN** the next page SHALL use the same snapshot without another SDK query
