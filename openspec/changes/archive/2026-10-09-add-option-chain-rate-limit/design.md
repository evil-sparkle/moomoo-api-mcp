# Design

## Context

See proposal.md for motivation. `get_services()` owns one `MarketDataService`
and quote connection for all stateless HTTP clients in the server process.
The option-chain tool currently offloads its synchronous service method through
AnyIO's default 40-worker pool. Validation already enforces a 29-day difference
between inclusive bounds. Python 3.10 compatibility and existing tool arguments
must remain intact.

The provider policy is grounded in [Moomoo's option-chain specification](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html),
checked on 2026-10-08: 10 requests per 30 seconds and at most a 30-day date span.

## Goals / Non-Goals

**Goals:** Async quota admission, shared accounting between async MCP callers
and direct synchronous service callers, deterministic clocks and bounded waits.

**Non-Goals:** Multi-process coordination, external quota stores, request
retries, generic MCP throttling and adding policies for other SDK operations.

## Decisions

- Use a small service-layer limiter with an immutable provider/operation policy,
  monotonic clock, deque of dispatch timestamps and thread-protected pending
  reservations. The initial policy is 10 calls / 30 seconds, a 0.1-second safety
  margin and a five-second quota wait. Inject clock and async sleep for tests;
  do not add public MCP arguments or environment settings for this policy.
- The process-owned market data service owns the option-chain budget. All MCP
  clients share it. Permit limiter injection for wrappers sharing a service
  budget; do not infer provider quota scopes from underlying symbols or filters.
- Keep the synchronous `get_option_chain` entry point, validating first and
  admitting immediately or raising a `RuntimeError` subclass with provider,
  operation and numeric retry-after seconds. Add an async service entry point
  that validates, waits for admission, then offloads only SDK execution using
  the same AnyIO worker pool. The tool awaits this entry point. Centralizing
  admission here prevents tool callers from forgetting the limiter.
- Admission reserves pending capacity without assigning an expiry. The worker
  commits the reservation immediately before SDK dispatch; only then does the
  rolling-window timestamp start. Unused reservations are released in `finally`.
  A released reservation cannot later dispatch, covering cancellation races.
  Dispatched timestamps survive exceptions and caller cancellation.
- Async quota waiting uses short cancellable sleeps, bounded by the remaining
  deadline and next expiry. The brief state lock contains no I/O or awaits.
  This works for synchronous threads as well as async callers without binding
  locks or notification objects to an event loop. FIFO fairness is not promised.
- Retain provider errors unchanged and count all dispatched attempts. Do not
  parse unstructured provider errors into local cooldowns or automatically retry.
  Other OpenD clients can still consume an unknown provider accounting scope.

## Risks / Trade-offs

- Independent processes and OpenD clients bypass this budget: document the
  single-server-process topology and that protection does not extend to them.
  A restart resets the local window; provider rejection remains possible.
- Worker congestion can hold reservations beyond 30 seconds: retain them until
  dispatch or cancellation, accepting reduced throughput to prevent overshoot.
- Cancellation after SDK dispatch cannot stop a blocking provider operation:
  retain its quota entry and never replay it.
- Pending-only retry times are estimates: report a conservative full window;
  when dispatched entries exist, report the next known expiry. Clients must
  still handle another quota error on retry.

## Migration Plan

No storage or configuration migration. Ship in the existing one-process
deployment after automated checks. Rollback restores the prior application
image. Live OpenD/client integration is separate from deterministic mock tests.
