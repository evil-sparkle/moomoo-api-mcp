# market-depth Specification

## Purpose

Provides tools for querying order book depth (bid and ask price levels, quantities, and order counts) for securities.

## Requirements

### Requirement: Retrieve Order Book

The system MUST provide a tool `get_order_book` to retrieve the bid/ask queue for a stock.

#### Scenario: User checks liquidity

- **Given** the agent is checking if there is enough liquidity to buy "HK.00700"
- **When** the `get_order_book` tool is called with `code="HK.00700"`
- **Then** the system returns the top N bid and ask levels with price and volume.
