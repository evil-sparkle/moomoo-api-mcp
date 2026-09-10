# market-subscriptions Specification

## Purpose

Allows inspecting active market data push subscriptions and consumed quota for the current connection, and explicitly releasing unwanted subscriptions.

## Requirements

### Requirement: Inspect and Release Connection Subscriptions

The system SHALL expose current-connection subscriptions and provider usage/quota
fields when available. It SHALL support explicit unsubscription by codes and data
types while preserving automatic subscriptions used by quote and order-book tools.
It SHALL NOT release another connection's subscriptions or invent quota limits.

#### Scenario: Inspect active subscriptions

- **WHEN** quotes or order books have created subscriptions on this connection
- **THEN** inspection returns those subscriptions with available provider usage data.

#### Scenario: Release selected subscriptions

- **WHEN** a caller specifies codes and data types for release
- **THEN** only those subscriptions on this connection are submitted for removal.

#### Scenario: Provider refuses early release

- **WHEN** the gateway rejects release because of a minimum duration or permission
- **THEN** the tool reports the failure and does not claim the subscriptions were removed.

#### Scenario: Preserve other clients

- **WHEN** another connection subscribes to the same security
- **THEN** this server's release does not unsubscribe that other connection.
