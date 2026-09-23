# Design

## Context

See proposal.md. Stage 2 unit and container tests use fake broker observations.
Earlier provider checks established DAY-only paper validity in the tested account,
no deal queries, and same-session order fields; none proves the journal end to end.

## Goals / Non-Goals

**Goals:** Collect reproducible live evidence for retention, journaled lifecycle,
uncertain outcomes, and preserved caller identity.

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
4. Exercise the real ZeroClaw/Telegram path. Preserve the exact token on retries
   and demonstrate no second application-level mutation invocation. A direct
   Python harness does not establish caller-chain behavior.
5. Preserve unresolved records and stop on uncertainty. Reconcile with orders and
   history, then use the separate operator capability only with verified evidence.
   A missing match or indefinite block is an honest test outcome.

## Risks / Trade-offs

- Paper environments may still fill orders → bound every operation and record
  final status, fills, remaining quantity and resulting position.
- Controlled response loss may leave execution blocked → retain the journal;
  use evidence-backed recovery, never a new database against the same account.
- External caller access may be unavailable → leave its test unchecked, with
  the missing prerequisite recorded.
- Retention changes over time → date observations and scope them to the tested
  account, market, SDK and OpenD versions.

## Review follow-up: acknowledgement and order visibility

The PR review identified an unmeasured timing boundary: a successful modification
response may precede a subsequent order query reflecting its new fields. An
adversarial double now separates those events. Development guards dependent
modifications until a fresh target observation matches the earlier requested total
quantity and limit price, including across restarts. This guard does not claim
causality or prove fill status. The provider's actual acknowledgement/query timing
remains unverified and must be recorded by task 2.5 before unattended execution.
