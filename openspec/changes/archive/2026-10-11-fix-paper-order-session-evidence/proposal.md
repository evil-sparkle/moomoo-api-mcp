# Proposal

## Why

OpenD reports the deprecated `fill_outside_rth` field as unavailable even when
an order has an explicit RTH session. The initial compatibility fix accepted one
legacy sentinel. The operator now requests the documented session API as the sole
US paper hours control, removing the obsolete argument and its eligibility fallback.

## What Changes

- Submit paper orders with `session=Session.RTH` and no explicit deprecated
  hours argument. Use explicit RTH alone for fresh NORMAL/DAY target eligibility.
- Refuse missing, unknown, NONE and extended sessions regardless of legacy data.
  Preserve raw provider observations without consulting or rewriting that field.
- Verify exactly-once dispatch, durable refusals and delayed modification
  visibility with SQLite regressions, including legacy-field absence and restart.
- Build a distinct local linux/amd64 image and replace only the Mac paper stack,
  preserving its original journal, volumes, account, USD 336 cap and loopback port.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `execution-journal`: Use documented sessions as authoritative paper hours evidence.

## Impact

Changes `src/moomoo_mcp/services/paper_execution.py` and regression tests.
No dependency update, journal migration or public argument change is needed.
The user previously authorized the local build and Mac test replacement and
now requests a dedicated fix PR, merge to main and archive of the completed fix.
Provider acceptance remains a separate validation change. No production/VPS
deployment is part of this integration. Historical initial-fix and cleanup
evidence remain retained and are superseded only where implementation changes.
