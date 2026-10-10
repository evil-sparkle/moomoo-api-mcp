# Separate fix integration — 2026-10-11

The operator requested separating the Session.RTH implementation from the live
provider-validation branch and merging it into current main. The dedicated
`fix/paper-order-session-rth` branch is based on
`34893af` (current main at extraction). Only the Session.RTH source patch, its
regression tests and this completed OpenSpec change are included. Main's
automatic recovery, broker dispatch/rate limiting and notional settings are
preserved. The authoritative session requirement is synced into the main spec.

The older verification files describe the exact code/image/SDK observed at their
recorded dates. They do not establish provider acceptance of this rebased source
or a new deployment. No Mac container or brokerage operation changes as part of
this branch separation. Fresh local checks and exact-head PR CI are recorded
below when completed; provider tasks 2.1–2.5 remain separate and incomplete.

## Checks on current main plus fix

- Fresh locked Python 3.12 environment: installed successfully.
- Focused paper-execution suite: 150 passed, one SDK deprecation warning.
- Ruff lint/format: passed, 100 files already formatted.
- basedpyright: zero errors, warnings or notes.
- Strict OpenSpec after sync/archive: 27 items passed, zero failed.
- Staged whitespace and secret scans: passed.
- Full mock-backed suite: passed through the repository pytest pre-commit
  hook on this rebased source. The hook reports success without a test count.
  Its Ruff, formatting, basedpyright, secret and Conventional Commit hooks passed.
- PR CI: pending; exact-head success required before merge.
