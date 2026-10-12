# Design

## Context

See proposal.md. Stage 2 unit and container tests use fake broker observations.
Earlier provider checks established DAY-only paper validity in the tested account,
no deal queries, and same-session order fields; none proves the journal end to end.

## Goals / Non-Goals

**Goals:** Collect reproducible live evidence for retention, journaled lifecycle,
uncertain outcomes, and preserved caller identity across MCP agent clients.

**Non-goals:** Runtime changes, REAL execution, strategy evaluation, journal reset,
or changing the existing recovery contract to make a test pass.

## Decisions

1. Obtain authorization for exact account/environment, symbol, quantity, prices,
   maximum operations, fault injection, and cleanup before any mutation. A plan or
   a development archive is not that authorization.
2. Measure retention by querying an existing terminal DAY order where possible.
   Record observation times and query results separately for current and history
   APIs. A positive sample is a lower bound, not maximum retention; empty results
   never prove absence. Continue observations until the window can be bounded or
   explicitly report the remaining uncertainty.
3. Run lifecycle and response-loss checks against the unchanged shipping service
   and journal. Record operation IDs, epochs, broker evidence and durable states.
   Do not retry with refreshed tokens or manufacture a successful outcome.
4. Exercise an authorized MCP agent client's actual retry path. The contract
   applies to any compliant MCP agent client, independent of agent framework or
   messaging channel. Record the client/version, MCP transport and any intermediary.
   Preserve the exact operation ID and admission epoch on retries and demonstrate
   no second application-level mutation invocation. A direct Python harness does
   not establish an external agent client's caller-chain behavior. Scope live
   evidence to the tested client and caller chain.
   For the separately authorized Mac first-phase lifecycle on 2026-10-09,
   the operator selected this agent as the direct authenticated MCP caller.
   Task 2.3 was unperformed in that first phase; it completed on 2026-10-12
   for the selected caller and tested e2bb7cf image. Later standing bounded
   authorization superseded that initial scope.
5. Preserve unresolved records and stop on uncertainty. Reconcile with orders and
   history through the selected image's process-owned, read-only automatic
   recovery and durable evidence. The tested e2bb7cf image supports no retired
   manual operator override. A missing match or indefinite block is an honest
   test outcome.

## Risks / Trade-offs

- Paper environments may still fill orders → bound every operation and record
  final status, fills, remaining quantity and resulting position.
- Controlled response loss may leave execution blocked → retain the journal;
  use evidence-backed recovery, never a new database against the same account.
- External caller access may be unavailable → leave its test unchecked, with
  the missing prerequisite recorded; it does not block an independently
  authorized direct-agent phase.
- Retention changes over time → date observations and scope them to the tested
  account, market, SDK and OpenD versions.

## Review follow-up: acknowledgement and order visibility

The PR review identified a timing boundary: a successful modification response
may precede a subsequent order query reflecting its new fields. Measure the actual
provider's acknowledgement and current/history observations in both field orders;
report sampling bounds and read interruptions without claiming an unobserved lag.

For repeatable restart coverage, a bounded external test observer obtains the real
fresh exact-order observation first, then projects the previously captured quantity/
price for one dependent preparation read after a real acknowledged reduction. It
must preserve an unfiltered current/history/positions path and refuse any fill,
changed status, wrong account/order or mismatched durable prior modification.
Retain its exclusive fault claim across the same-image process restart, preserving
the journal's original acknowledgement, epoch and unobserved-modification guard.
Verify the dependent intent is durably REFUSED/NOT_SENT with marker zero and no
SDK invocation. After fault release and fresh real-state confirmation, use a new
intent; never replay the refused token or restore the old quantity.

The operator approved measured real-provider timing plus clearly labeled injected
restart coverage on 2026-10-12. Natural provider lag is not required to complete
this controlled guard test. Injected timing cannot establish real provider lag,
causality or fill behavior. This external test setup changes no packaged runtime
requirement, recovery policy or shipping source.
