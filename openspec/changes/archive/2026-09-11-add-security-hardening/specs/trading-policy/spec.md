## ADDED Requirements

### Requirement: Order Safety Guardrails

The trading policy SHALL support optional configurable upper bounds on single-order quantities and single-order notional values to protect against erroneous or runaway automated orders.

#### Scenario: Enforce max order quantity limit
- **GIVEN** `MOOMOO_MAX_ORDER_QTY` is configured to a positive limit
- **WHEN** an order placement or combo order request specifies a quantity exceeding that limit
- **THEN** the service SHALL reject the order before contacting the OpenD gateway

#### Scenario: Enforce max order notional limit
- **GIVEN** `MOOMOO_MAX_ORDER_NOTIONAL` is configured to a positive dollar value
- **WHEN** an order placement or combo order request has a calculated notional (`price * qty`) exceeding that limit
- **THEN** the service SHALL reject the order before contacting the OpenD gateway

#### Scenario: Unbounded orders when limits are not configured
- **GIVEN** neither `MOOMOO_MAX_ORDER_QTY` nor `MOOMOO_MAX_ORDER_NOTIONAL` is set
- **WHEN** an order is submitted within an authorized trading environment
- **THEN** the service SHALL permit the order without quantity or notional limit rejection
