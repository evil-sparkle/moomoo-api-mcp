# Documented session migration — 2026-10-10

The user requested removal of the deprecated application parameter and approved
building/replacing the local Mac paper image without another agent. Placement
now specifies `Session.RTH`; fresh NORMAL/DAY modification/cancellation targets
require explicit RTH. The application never passes or consults the deprecated
flag. Missing, NONE, unknown and extended sessions remain refused. Raw SDK
columns and all historical evidence are retained. The pinned SDK still has its
own legacy default; no dependency or protocol patch is claimed.

## Local checks

| Check | Observed result |
| --- | --- |
| New placement/legacy-independence regressions before migration | 11 failed, 6 passed |
| Updated complete paper service suite | 127 passed |
| Full mock-backed suite | 1,615 passed, 1 skipped, 106 subtests; 29 deprecation warnings; 184.47 seconds |
| Ruff lint/format | Passed; 95 files already formatted |
| basedpyright | 0 errors, 0 warnings, 0 notes |
| Strict OpenSpec, fix and provider validation | Passed |
| Application source references to deprecated flag | Zero |
| Installed-image module hash vs staged tested source | Matches |

The full run also printed a BrokenPipeError from the deployment HTTP fixture
when its client disconnected; no test failed. No unrelated fixture changes were
made. Regressions verify explicit RTH with absent/arbitrary legacy data, NONE
plus false/zero refusal, immutable stored refusals, one dispatch/marker and
quantity/price visibility across restart.

## Image and activation

The source-only staged build excludes private/operational files and symlinks.
Source HEAD: `1c99e605d6f7c3f6e30c31a21c36df37b891b016`.
Source patch SHA-256: `587d1742465cf95d49a49672413c360c9d86d24d093dde862c50ce932a77f2a2`.
Installed module SHA-256: `53a0725a5e04f9011a822a892bfbfa5d4b594fe459d33dad61c1e1ad8324c95a`.
Local tag: `moomoo-paper-test:local-session-rth-1c99e60-587d1742465c`.
Actual image ID: `sha256:e499fd77da566746056704c0082145422ed7d0c2802bbb0c23328ffea79587d6`.
Platform: linux/amd64. Local build, no CI approval claimed.

Before activation at 03:23:22 SGT, authenticated read-only queries confirmed the
original order CANCELLED_ALL, zero fills and positions, journal READY, integrity
ok, original identity/inode 1167351 and three operations. All three complete row
hashes and preserved original/cancellation evidence hashes were captured.

Activation completed at 03:29:02 SGT; one recreation started the replacement at
03:28:49 SGT. No provider mutation, fault injection or recovery acknowledgement
ran during this development/build/replacement. The previous image is retained.

## Postactivation evidence

- Normal and distinct operator MCP authentication succeeded; anonymous requests
  returned 401 both internally and at host loopback port 18080.
- Remembered login worked without private input. SDK remains 10.10.7008, gateway
  API server_ver 1010, and the checksum-verified OpenD 10.10.7008 binary is unchanged.
- SIMULATE/US, exact allowlist ...0047, USD 336 cap, maximum quantity two, original
  named volumes, UID 10001, journal identity/schema/inode and automatic creation
  disabled were preserved. Ports remain 127.0.0.1:18080 -> 8000 only.
- Complete hashes of all three operation rows, original identity/evidence files
  and cancellation marker files match before and after. No review/recovery audit
  rows exist. Journal READY, integrity ok and no in-flight/blocking entries.
- Current/history reads still show the original order CANCELLED_ALL, zero fills,
  no unrelated orders and empty positions.
- Recreation issued epoch `23b89ff1-6d1a-4c64-9819-fecae453d306`; original stored
  epochs are unchanged. No intent was replayed or substituted with this epoch.
- The installed module matches the tested/staged SHA-256. Container restart count
  is zero. The canonical local Compose helper now includes the correct image/cap
  overlays and verifies image ID, source revision and patch hash. Its settings
  check passed without printing private configuration.

## Continuation and limits

The existing hourly lock was held throughout source edits, build, activation and
verification; a second runner invocation skipped. The durable prompt/context now
refer to this Session.RTH image, cleared handoff and existing standing authority.
The first new lifecycle is bounded to one AAPL BUY share at USD 1.00, one price-only
change to USD 1.01 and cancellation, at most three mutation intents/USD 1.01 gross
exposure, confirmed regular trading, and stop on fill/refusal/uncertainty/blocking.
Fault/recovery and visibility phases require concrete safe methods and factual
proof before execution. No such phase ran during this migration.

The runner retains workspace-write and serial/40-minute bounds. Its network
setting follows [official Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference).
A structured UTC resume time now prevents a closed-market blocker from permanently
suppressing future hourly runs. The future/due resume paths were exercised with a
fake executable in a temporary directory: future skips, due invokes once; no model
or broker ran in that check. Uncertain/failed mutation outcomes get no timed retry.

Provider validation tasks 2.1–2.5 remain unchecked; task 1.1 remains complete.
Local redacted provenance, snapshots and activation results are retained under
Git-ignored env/paper-test-mac/session-migration-*. No PR/push/archive or remote
production change occurred. This image is locally verified, not CI-approved.


Final independent read-only observation at UTC `2026-10-09T19:35:52.748290+00:00` again
confirmed all three row/evidence hashes unchanged, READY, integrity ok, original
order CANCELLED_ALL with zero fills and empty positions. The existing hourly
cron entry was read-back verified once; no duplicate job was installed.
