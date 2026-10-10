# Local compatibility verification — 2026-10-10

This record describes the initial legacy-sentinel fix. The current implementation
and activated image are documented in
[session-migration-verification.md](session-migration-verification.md).

The implementation accepts a fresh NORMAL/DAY paper target with explicit RTH
session and the SDK's exact legacy `"N/A"` sentinel. It preserves the false/zero
legacy paths, rejects unsupported or ambiguous fields before dispatch, and keeps
the broker observation unchanged. This section records local verification before deployment. The dated
[Mac activation and cleanup evidence](runtime-verification.md) below establishes
the later replacement and cancellation; provider acceptance remains incomplete.

## Evidence and behavior

Moomoo's [placement documentation](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html)
deprecates the legacy flag in favor of session. Its
[session definitions](https://openapi.moomoo.com/moomoo-api-doc/en/quote/quote.html#session)
define RTH as regular hours, NONE as unknown, and ETH/OVERNIGHT/ALL as broader
sessions. Inspection of the installed `moomoo-api` 10.10.7008 package confirms
`trade_query.py` reports the exact `"N/A"` sentinel when the optional
`fillOutsideRTH` protobuf field is absent, while independently decoding session.

The new accepted-path tests failed on the unchanged implementation for both
MODIFY and CANCEL: two failures, eight existing-representation successes. After
the code change, the complete paper service tests passed: **126 passed**.

These tests use stateful broker doubles and actual temporary SQLite stores:

- Explicit RTH/N/A and existing RTH/NONE false/zero cases dispatch exactly once,
  commit marker one, retain quantity on price-only modification, preserve the
  raw legacy observation and return the stored result on duplicate invocation.
- Unknown, null, missing or extended sessions; true/nonzero/null/malformed legacy
  flags; and non-DAY/non-NORMAL targets are durably REFUSED / NOT_SENT with
  marker zero and no SDK mutation call.
- An intent refused under ineligible evidence stays refused after the mock
  provider record becomes eligible; its stored row is unchanged.
- Quantity-then-price and price-then-quantity cases with delayed broker visibility
  still refuse dependent changes until the acknowledged fields are observed,
  including after a SQLite store restart with N/A observations.

## Checks

| Check | Result |
| --- | --- |
| Focused paper service suite | 126 passed |
| Full mock-backed suite | 1,614 passed, 1 skipped, 106 subtests passed; 30 deprecation warnings; 193.21 seconds |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed, 95 files formatted |
| `uv run basedpyright` | 0 errors, 0 warnings, 0 notes |
| Strict validation of fix and provider-validation changes | Passed |
| `git diff --check` | Passed |

## Live separation and remaining prerequisites

Read-only Docker inspection confirmed the selected image digest is still
`sha256:5cbb88e950a1ea52dd5825deb00ff14e2dea619bdfa3fe1d1f8f89ba3978db01`.
The container start time remains `2026-10-09T17:48:31.184883381Z`, matching the
previously approved cap activation. Both original dedicated volumes are mounted.
The original journal inode is preserved, integrity is `ok`, and its two rows
remain one ACKNOWLEDGED/ACKNOWLEDGED with marker one and one REFUSED/NOT_SENT
with marker zero. There are no review or recovery audit rows.

No live mutation, fault injection, recovery acknowledgement, journal migration,
container recreation or deployment ran during this change. A new image and its
deployment require separate approval; another live attempt requires fresh exact
bounds and a newly authorized intent. The original refused intent must keep its
stored refusal. Tasks 2.1–2.5 of `validate-paper-execution-provider` remain
unchecked. Hourly continuation is managed separately by a local cron runner; no native Codex schedule is needed.

## Authorized Mac activation and cleanup — 2026-10-10

The later operator-authorized handoff activated the distinct local linux/amd64
image and cancelled only the original unfilled order. See
[runtime-verification.md](runtime-verification.md) for actual image/patch identity,
original journal preservation, timestamped broker reads and remaining criteria.
The earlier live-separation section describes the predeployment snapshot.
All four implementation tasks remain complete; provider tasks 2.1–2.5 remain
unchecked. No CI approval, full lifecycle or fault/retry acceptance is claimed.


## Documented Session.RTH migration — 2026-10-10

The operator subsequently requested removal of the deprecated application
argument and dependency, and approved a local build/replacement by this agent.
The earlier N/A compatibility behavior is historical. The current implementation
uses Session.RTH alone and refuses unknown/NONE sessions without a legacy fallback.
See [session-migration-verification.md](session-migration-verification.md) for the
new 1,615-test result, actual image, one recreation, three preserved journal rows,
authenticated readiness and updated continuation. Raw SDK columns are retained;
no SDK wire/dependency removal is claimed. Provider acceptance is still incomplete.
