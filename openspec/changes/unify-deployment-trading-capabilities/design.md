# Design

## Context

See proposal.md for the review findings. Mode checks already live below MCP dispatch, execution journals are environment-bound, and gateway connections belong to one process. Existing installations must keep their account identities, journal data, and authenticated endpoint behavior.

## Goals / Non-Goals

Goals: align effective configuration across launchers; independently scope a restricted client; allow operators to pause new risk and observe prior executions without enabling new writes; make deployment rollback reproduce the previous effective release.

Non-goals: expanding paper products beyond the verified US equity executor, bypassing unknown-outcome recovery gates, changing broker permissions, or issuing live trading commands during verification.

## Decisions

- Keep the ordinary MCP credential and add a distinct restricted credential with explicit tool/environment permissions and optional exact account scopes. Grants intersect with server policy and executor readiness. Request context propagates into worker calls but never owns process initialization. Discovery and cached results enforce the same account boundary as fresh calls.
- Keep existing modes as presets. REAL continues to permit configured paper execution. An explicit observation opt-in opens only existing configured journals outside their writable modes; it never initializes storage or grants submission authority. Ordinary READ_ONLY remains journal-independent.
- Store operator pauses by environment and optional account in the durable controls database. Pause decisions serialize with both broker dispatch paths. They gate new exposure while cancellation remains subject to its existing authentication, approval, journal and recovery checks. Gateway locking remains a separate safety mechanism.
- Select mutation accounts consistently for discovery, preparation and execution; ordinary account reads retain their wider explicitly authorized scope.
- Give source and Compose the same USD:500 default cap and require explicit NONE to disable it. Keep environment volumes provisioned across permission changes while opening only configured stores.
- Validate the selected image's application settings and mounted storage offline before replacement. Save effective Compose configuration privately with immutable image identities; rollback consumes that snapshot rather than a subsequently edited environment file. Ansible and legacy lifecycle commands use exclusive deployment ownership.

## Risks / Trade-offs

- Restricted authorization is a new boundary: verify denied direct calls, worker propagation, cached pagination, journal retry/results, health redaction and credential separation.
- A pause does not prove an existing order stopped: cancellations still require durable admission and may be blocked by uncertain state.
- Observation updates local recovery evidence and requires exclusive journal ownership; it never mutates broker orders.
- Effective snapshots contain credentials: save them atomically at 0600, exclude them from Git/build contexts, and suppress configuration output.
- Direct source orders above the new default cap are refused: document explicit cap selection and NONE migration.

## Migration Plan

Existing ordinary credentials retain deployment policy. Operators opt into restricted credentials and observation. Existing controls stores receive an additive pause table without changing decisions or charges. The first managed upgrade captures the running configuration before replacing containers. Provisioning flags remain explicit, and rollback preserves all journal volumes. Archive the completed change only after the implementation PR is approved and merged.
