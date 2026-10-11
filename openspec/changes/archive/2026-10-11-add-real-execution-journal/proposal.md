# Proposal

## Why

REAL mutations currently ignore execution tokens and lose their local submission history on restart. Extend the durable execution boundary to live trading so client retries cannot issue another SDK mutation and uncertain outcomes can be accounted for through broker queries.

## What Changes

- **BREAKING**: REAL mode requires an explicitly initialized, dedicated REAL journal; every REAL placement, combo placement, modification and cancellation requires caller-owned `operation_id` and server-issued `admission_epoch`.
- Preserve existing REAL order types, markets, numeric inputs, account allowlists, limits and managed unlock/relock behavior within the durable lifecycle.
- Persist immutable account/request identity, dispatch markers, receipts, broker correlation tags and recovery evidence, with no unjournaled fallback.
- Add process-owned read-only REAL recovery. Exact broker evidence may account for an operation; unsuccessful searches never authorize a replacement or release the REAL journal gate.
- Expose independent REAL journal health and environment-selected execution lookup/reconciliation, retaining existing paper behavior.
- Document initialization, persistent container storage, migration and the remaining live-provider acceptance boundary. Stage 3 approval remains separate.

## Capabilities

### New Capabilities

- `real-execution`: Durable identity, dispatch, duplicate suppression and conservative broker accounting for REAL mutations.

### Modified Capabilities

- `configuration`: Require separate REAL storage configuration and explicit initialization in REAL mode.
- `order-placement`: Require REAL placement tokens and persist broker correlation.
- `combo-order-placement`: Require durable execution tokens for REAL packages.
- `system-health`: Report independent REAL journal state and recovery progress.
- `trading-policy`: Replace the REAL journaling deferral with independent durable gates.

## Impact

Execution store, REAL service mutation and admission paths, process lifecycle, MCP mutation/inspection tools, settings, container configuration and deployment documentation. Unit, adversarial concurrency/restart/storage tests and CI validate application behavior without touching a real account. No deployment, account unlock or live order is part of this implementation request.
