# Design

## Context

See proposal.md for motivation. SQLite schema 3 already owns operation identity, startup review, audit and recovery scheduling but is fixed to SIMULATE. Paper execution constrains orders to US NORMAL DAY and uses a paper-only absence policy. REAL has broader APIs and a mature `_dispatch_write` boundary for policy, capacity, JIT unlock and relock. The expansion must compose with that boundary.

## Goals / Non-Goals

Goals are journal every supported REAL mutation and preserve existing REAL validation and numeric contracts. Application-level single dispatch and durable broker accounting are the guarantee. Provider-side exactly-once execution, automatic replacement and Stage 3 operator approval are outside this change. Tests do not authorize live trading or deployment.

## Decisions

1. Use two environment-bound stores and distinct directories, retaining paper schema and data. Parameterize store creation/verification; reject cross-environment opens before migrating or journaling. Enforce REAL's ban on assumed absence in storage as well as the worker. This avoids making paper decisions live-money policy.
2. Add a REAL executor around existing mutation methods, with canonical REAL numeric identity and early token lookup. Resolve a new account before atomic admission; freeze account on retries. Serialize the complete existing validation/target-read/dispatch/outcome region while keeping duplicate lookup outside the execution lock. Persist the marker within the protected SDK dispatch boundary, after successful unlock, and relock even if marker persistence fails.
3. Continue using existing REAL validation, account resolver and JIT dispatcher rather than routing through paper execution. Placements and combos replace broker remark with a persisted correlation tag; preserve original remark in request identity. Modifications store original patch independently of the broker request; dependent mutations must not merge stale prior acknowledged fields.
4. Run REAL recovery as a process-owned read-only worker. Exact ID/tag and consistent observable broker fields plus fresh positions allow accounting. Missing provider fields or unsupported evidence stay pending. Never infer absence after elapsed time or five negative rounds. Recovered ADMITTED with no marker can be durably refused as not dispatched; DISPATCHING becomes unknown on startup.
5. Select the journal explicitly on inspection tools, with SIMULATE default for existing callers. Add a separate REAL health field and bounded probe. Lifecycle opens configured stores before HTTP and closes all partially built resources on failure.

## Risks / Trade-offs

- Broker remark is correlation, not broker deduplication. Never replay after marker commitment; reject ambiguous tags and IDs.
- REAL evidence can be incomplete for complex orders. Preserve a blocked pending state instead of claiming proof; document recovery limitations.
- Required tokens/storage break existing REAL clients. Document the health epoch handshake and one-time initialization, with fail-closed startup.
- Storage failure after acknowledgement can lose the durable outcome. Keep local acknowledged evidence visible, latch the gate and require healthy restart/read-only accounting.
- Provider visibility and retention are unverified by offline tests. Keep live acceptance separate, with no real-money test in this work.

## Migration Plan

Retain the paper volume; add a dedicated REAL volume/path and explicit initialization flag. Initialize once, then use creation disabled. Upgrade clients to send stable operation IDs and the REAL epoch. Preserve both volumes on mode changes or rollback; never delete or rewrite a journal to clear uncertainty. Validate local tests/static checks, strict specs and exact-head PR CI; sync and archive before the authorized merge.
