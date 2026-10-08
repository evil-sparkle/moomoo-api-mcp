# Proposal

## Why

Concurrent option discovery requests can exceed Moomoo's documented limit of
10 option-chain calls per 30 seconds even when their date ranges are valid.
Admission should follow the provider operation and shared gateway service,
without limiting unrelated MCP tools or occupying workers while waiting.

## What Changes

- Add reusable provider-operation admission with a rolling window, a bounded
  async wait, cancellation cleanup and a clear retry-after error.
- Apply the initial policy only to `get_option_chain`: 10 SDK starts per
  30 seconds, with a small safety margin and a five-second admission deadline.
- Share one budget across clients of the process-owned market data service.
  The supported deployment has one MCP server process per OpenD gateway;
  coordination with independent processes or OpenD clients is not guaranteed.
- Preserve option-chain inputs, returned records, provider errors and the
  existing inclusive 30-day validation. Direct synchronous service calls use
  the same budget but fail immediately if no slot is available.
- Cover rolling-window boundaries, concurrent dispatch, worker congestion,
  cancellation, invalid requests and provider failures with deterministic tests.

## Capabilities

### New Capabilities

- `provider-request-governance`: Shared admission for explicitly governed
  provider operations, including bounded waiting and dispatch accounting.

### Modified Capabilities

- `option-discovery`: Add provider-aware admission requirements for option
  chains while retaining the existing discovery contract.

## Impact

`src/moomoo_mcp/services/market_data_service.py`, a new service-layer limiter,
the option-chain MCP tool, service/tool tests, and README guidance. No new
runtime dependency, external coordinator, trading behavior or public tool
argument is introduced. Inventorying limits for other endpoints is follow-up
work and does not add policies in this change.
