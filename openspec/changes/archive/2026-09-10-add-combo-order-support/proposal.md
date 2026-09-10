# Proposal: Add Combo (Multi-Leg) Order Support

## Goal

Enable AI agents to place multi-leg option strategies — vertical spreads, straddles,
butterflies — as a single atomic combo order, using the `moomoo-api` SDK's
`place_combo_order`.

## What Changes

- `combo-order-placement`: New capability to submit a multi-leg order as one order,
  with per-leg `code`, `trd_side`, `qty_ratio`, and an optional `position_id`, plus a
  net limit `price`.
- `account-info`: Extend position retrieval with the option strategy view, which is
  the documented source of the `position_id` values a closing combo order requires.
  Without it, the closing workflow cannot be performed through this server.
- **Dependency bump**: `moomoo-api` must be raised from `>=3.3.0` to a version that
  exposes `place_combo_order` and `ComboLeg`. Neither exists in the currently
  resolved 9.06.5608.

## Why

`place_order` is single-leg only. An agent asked to close a vertical spread today has
no choice but to submit two independent orders, which introduces **leg risk**: if the
first leg fills and the second does not, the account is left holding a materially
different — and potentially far riskier — position than intended. Closing a call debit
spread one leg at a time can leave a naked short call, whose risk is theoretically
unbounded.

A combo order is filled by the exchange as a single unit at a net price, or not at all.
This removes leg risk entirely and matches how the moomoo app itself presents these
strategies (one ticket, one net price).

This is foundational rather than incidental: any strategy automation built on this
server — stop-losses, roll logic, expiry management — needs atomic multi-leg execution
to be safe.
