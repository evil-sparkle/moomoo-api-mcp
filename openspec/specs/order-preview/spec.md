# order-preview Specification

## Purpose

Compose read-only single-order previews from instrument facts, deployment policy and the broker's documented tradable-quantity information.

## Requirements

### Requirement: Composed Single-Order Preview
The system SHALL provide a single-order preview with resolved account/environment, normalized intent, validation, valuation and broker maximum quantities. It SHALL distinguish limited option/futures margin fields from universal account-impact simulation and SHALL NOT claim execution is guaranteed.

#### Scenario: Read-only preview
- **WHEN** an order is previewed on a read-only deployment
- **THEN** broker read APIs SHALL remain usable without a write, unlock or execution admission

#### Scenario: Limited broker margin fields
- **WHEN** maximum-quantity information is returned
- **THEN** absent margin or fee estimates SHALL remain absent rather than be invented
