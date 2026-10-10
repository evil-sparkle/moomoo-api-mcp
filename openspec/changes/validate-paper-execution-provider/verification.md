# Provider verification — 2026-10-09–10

The Mac first phase has one acknowledged paper placement and a completed
read-only observation inside the approved window. A separately authorized
price modification on 10 October was refused before broker dispatch. A later compatibility activation and one cancellation are recorded below;
current/history reads verified CANCELLED_ALL with zero fills and no positions.
The complete place/modify/cancel lifecycle and remaining live acceptance checks
are incomplete. Tasks 2.1–2.5 remain unchecked.

## Authorization and environment

The operator selected this agent as the authenticated MCP caller and approved
one US SIMULATE account, US.AAPL BUY one share, NORMAL/DAY at USD 1.00, at most
one price-only modification to USD 1.01 and one cancellation of the exact order.
The maximum is one new order, three mutation intents and one additional
share/USD 1.01 gross exposure. Stop conditions include rejection, fill,
uncertainty, journal blocking and unexpected account activity. No sale, fault
injection, restart or recovery acknowledgement was authorized by these original
first-phase bounds.

The original 2026-10-09 21:45–22:00 SGT regular-session window was the gate
for modification and cancellation. The operator subsequently authorized the
same placement ahead of trading, with RTH-only fills. This amended submission
timing only. The window has now expired without either remaining mutation being
attempted; further order mutations require a newly authorized dated window.
Exact account identity, authorization and caller intent are retained
locally outside Git. The operator confirmed that the other instance is READ_ONLY
and this stack is the sole paper writer; this assertion was not independently
audited.

The separate Mac stack uses shipping source
`07b119aad4d1666b6aebbfa21f3c112955fdd283`, pinned image
`sha256:5cbb88e950a1ea52dd5825deb00ff14e2dea619bdfa3fe1d1f8f89ba3978db01`,
and platform `linux/amd64`. SDK and OpenD build pin are `10.10.7008`; the live
OpenD API reports `server_ver=1010`. The original schema-2 journal is preserved
with automatic creation disabled. Authenticated MCP and separate operator access
were verified; anonymous access returned HTTP 401.

## Observations

At 09:40:29 SGT, a read-only pre-placement baseline found the exact configured
US SIMULATE account, no current orders or positions, and an empty READY journal
with integrity `ok`. A final preflight immediately before dispatch repeated the
account, empty-order/position, journal, epoch, image and exact-bound checks.

| Time on 2026-10-09 (SGT) | Observation |
| --- | --- |
| 09:44:25.494 | Sent exactly one MCP `place_order` request for BUY one US.AAPL share at decimal-string limit `1.00`, NORMAL/DAY, on the verified SIMULATE account. |
| 09:44:26.328 | Journal lookup returned `ACKNOWLEDGED` / `durably_stored`. The stored SDK receipt reports `SUBMITTING`, zero fills, `session=RTH`, and `fill_outside_rth=false`. A fresh current-order query reports the same order as `SUBMITTED`, quantity one, limit USD 1.00 and zero fills. |
| 09:45:38.430 | Current and historical order queries both returned that same `SUBMITTED` order, with unchanged quantity and price and zero fills. There were no unrelated current orders and no positions. |
| 09:49:26.058 | The read-only window observer's setup check returned the same order and zero positions. The provider reported US.AAPL market state `OVERNIGHT`; the order retained `session=RTH`. |
| 21:45:01.447 | The one-time observer completed within the approved window, after starting at 21:45:00.011. Provider market state was `AFTERNOON`. Current/history queries returned the same RTH DAY order `SUBMITTED`, one share at USD 1.00, zero fills, no unrelated current orders and no positions. The journal remained READY and intact, with its admission epoch preserved. |
| 22:24:43.863 | A separate operator-requested read-only status probe returned the same order still `SUBMITTED` at USD 1.00, with zero fills and no positions. Journal integrity remained `ok`, with one acknowledged operation, no blocking and the original epoch/identity preserved. This probe was outside the expired mutation window and sent no mutation. |

The order's redacted reference is `8da2eefedf3ef77e`; it is a SHA-256 prefix of
the exact broker order ID, which remains in the original private journal. The
saved caller operation ID and admission epoch were used for placement; journal
lookup used that same operation ID, and health reads confirmed the epoch was
unchanged. Both values are retained in the local evidence. The provider's
creation/update timestamp was `2026-10-08 21:44:26`, recorded verbatim without
converting its timezone. The historical query covered the explicit 2026-10-08 through
2026-10-09 date range.

The stored SDK receipt and later broker observations are distinct evidence.
Current/history reads returned `fill_outside_rth=N/A` while retaining
`session=RTH`; the placement receipt explicitly returned
`fill_outside_rth=false`. No regular-session fill behavior has yet been observed.

There was one mutation HTTP request, no automatic retry, no modification and no
cancellation. One journal operation and its dispatch marker were present; there
were zero review requirements and zero recovery audit rows. After the reads,
the journal remained READY with no in-flight operations or blocking reasons,
integrity `ok`, and the original journal inode and identity preserved.

## Fill amendment and pre-dispatch refusal — 2026-10-10

The operator's USD 335 fill approval specified 9 October 22:30–23:00 SGT but
arrived after that deadline. It was retained as expired and never activated.
The operator then explicitly approved 10 October 01:45–02:15 SGT, during
confirmed regular trading, for one price-only modification of the existing
order to decimal-string USD `336.00`. Quantity one, NORMAL/DAY/RTH and the
exact original broker/account identities were preserved. Maximum gross purchase
exposure was USD 336, excluding fees. The amendment also authorized increasing
the Mac cap from USD 250 to USD 336 and one container recreation, preserving
the original journal and volumes. It prohibited new orders, sale, cancellation,
fault injection, recovery acknowledgement and automatic retry, with a stop on
fill, refusal, uncertainty or blocking.

The operator privately activated the override at 01:48:31 SGT. Read-only
verification confirmed the same immutable image, dedicated volumes, loopback
listener, original journal identity/inode, integrity `ok`, automatic creation
disabled and a READY journal. The authorized recreation issued a new admission
epoch; it did not reset the original placement record. The new intent used the
new epoch and persisted its operation ID before the single MCP invocation.

| Time on 2026-10-10 (SGT) | Observation |
| --- | --- |
| 01:54:54.055 | Active cap `USD:336`; original order SUBMITTED, one share at USD 1.00, zero fills and no positions. Provider market state AFTERNOON; AAPL ask USD 335.04. Original journal intact and READY with one operation. |
| 01:56:31.811 | Invoked `modify_order` exactly once with NORMAL, SIMULATE, decimal-string price `336.00`, adjustment zero and quantity omitted. Fresh provider state was AFTERNOON and ask USD 334.89. Dispatch was within the approved window. |
| 01:56:31.955 | MCP returned a tool refusal. The new journal operation was `REFUSED` / `NOT_SENT`, durably stored with dispatch marker zero. No broker modification was dispatched. Fresh current-order reads showed the original order unchanged at USD 1.00 with zero fills and positions. |
| 01:56:42.969 | Read-only journal lookup reported: “No order was sent: Target order is outside verified DAY/regular-hours paper scope”. |
| 01:57:14.983 | Current/history reads still returned the exact SUBMITTED order, quantity one, USD 1.00, zero fills, no unrelated orders and no positions. Journal READY, integrity `ok`, original identity/inode preserved, two operations, zero reviews/recovery audit rows and no blocking. The refused intent's new epoch remained unchanged. |

The pinned image requires `fill_outside_rth` to equal `false` or numeric zero
before admitting a modification. The provider's fresh target order reports
`fill_outside_rth="N/A"`, although `session=RTH`, order type NORMAL and time in
force DAY remain present. This concrete field mismatch caused the refusal.
The placement receipt's `fill_outside_rth=false` is separate evidence and was
not substituted for the fresh broker field. No guard was bypassed.

The approved attempt ended at this refusal. There were two MCP mutation intents
across the run: the original acknowledged placement and this refused modification.
Only the placement reached the broker. There was no mutation retry, cancellation,
new order, sale, fault injection or recovery acknowledgement. The USD 336 cap
remains active and the original USD 1.00 DAY order remains open as of the latest
read. Task 2.1 remains unchecked; this run did not establish a successful
modification, fill or full lifecycle.

Local evidence includes `phase1-fill-approval-current.json`,
`phase1-fill-cap-activation.json`, `phase1-fill-modify-intent.json`,
`phase1-fill-modify-result.json`, `phase1-fill-refusal-detail.json` and
`phase1-fill-final-observation.json`. Caller guards in both the local artifact
directory and original journal volume prohibit a second harness invocation.

## Separate local compatibility follow-up

The operator approved local development of
[`fix-paper-order-session-evidence`](../archive/2026-10-11-fix-paper-order-session-evidence/proposal.md).
That change accepts explicit RTH with the exact unavailable legacy sentinel and
retains refusal for missing, contradictory or broader session evidence. Its
[local verification](../archive/2026-10-11-fix-paper-order-session-evidence/verification.md) is
separate from this provider acceptance record. At that local-development
snapshot no replacement image had been deployed. The later authorized activation
and cleanup are recorded below. No original journal row or caller marker was replaced.

## Remaining checks

- The separate compatibility activation and original-order cleanup are complete;
  see the dated handoff below. The original order is CANCELLED_ALL with zero fills
  and no positions as of the final timestamped reads. Its refused modification
  remains immutable. Task 2.1 lacks a successful modification/full lifecycle.
- Standing paper-order and fault-injection authorization is recorded in the
  hourly operator context. Expired original attempt windows do not prevent
  future separately bounded tests under that standing authority. This handoff
  is complete and permits no further mutation or recreation.
- The hourly job remains analysis-only; one coordinated interactive caller through
  the fixed Mac journaled MCP service is the sole live executor route. Record
  conservative exact bounds and current regular-session/account/journal facts
  before future tests, without creating a competing executor.
- Tasks 2.2, 2.3 and 2.5 remain unperformed: response loss/recovery, caller retries,
  and quantity/price visibility with restart and reverse-field timing.
- Task 2.4 remains unchecked pending the complete dated acceptance record.
  This cleanup evidence is a partial record, not full acceptance publication.

Local redacted evidence is retained in `env/paper-test-mac/`:
`phase1-place-intent.json`, `phase1-place-result.json` and
`phase1-post-placement-observation.json`. These files and the local runbook are
Git-ignored. A single-use marker in the original journal volume prevents the
placement harness from submitting the intent again. No credentials or exact
account/broker identifiers are included in this record.
The read-only schedule is recorded in `phase1-window-check-schedule.json`, its
setup check in `phase1-observer-setup-check.json`, and its completed result in
`phase1-window-observation.json`. The separate current-status
probe is `phase1-status-20261009T142443Z.json`; its timestamp distinguishes it
from the in-window test evidence.

## Authorized compatibility activation and original-order cleanup — 2026-10-10

At 03:07:30 SGT, one operator-authorized recreation activated the reviewed local
linux/amd64 compatibility image. At 03:09:10 SGT, exactly one fresh journaled MCP
cancellation of the original unfilled AAPL order was acknowledged during AFTERNOON
regular trading. Current/history queries returned CANCELLED_ALL with zero fills;
positions were empty. Independent reads at 03:10:58 SGT agreed. The original
schema-2 journal, identity, inode and volumes are intact; both original operation
rows/epochs and marker files have unchanged hashes. Three operations are now
present: placement ACKNOWLEDGED (marker one), original modification REFUSED/NOT_SENT
(marker zero), cancellation ACKNOWLEDGED (marker one). Journal READY, integrity ok,
zero review/recovery audit rows and no blocking.

Runtime implementation and deployment evidence belong to the separate
[compatibility runtime verification](../archive/2026-10-11-fix-paper-order-session-evidence/runtime-verification.md).
That record contains the actual replacement image, source/patch provenance, SDK/
OpenD evidence and local checks. No local build CI approval is claimed. No placement,
modification, sale, fault injection, recovery acknowledgement or automatic mutation
retry ran in this handoff. Full broker/account identities remain private. Tasks
1.1/1.2 stay complete and 2.1–2.5 stay incomplete; the checkboxes were not changed.


## Documented session migration and replacement — 2026-10-10 03:29 SGT

The operator requested removal of the deprecated application parameter and
approved this agent's local Mac image replacement. The separate compatibility
change now uses Session.RTH exclusively, omits the legacy SDK argument and rejects
NONE/unknown/extended sessions without fallback. Full local suite: 1,615 passed,
one skipped, 106 subtests. The actual local linux/amd64 image is
`sha256:e499fd77da566746056704c0082145422ed7d0c2802bbb0c23328ffea79587d6`.

One recreation preserved the original volumes and journal; all three operation
row/evidence hashes are unchanged, integrity ok, READY and no reviews/recovery.
Agent/operator authenticated MCP and remembered login succeeded. SDK/OpenD remain
10.10.7008; exact SIMULATE/US ...0047, USD 336 cap and loopback port are unchanged.
Read-only current/history queries still show the original order CANCELLED_ALL,
zero fills and empty positions. No provider mutation ran during this replacement.
See [migration evidence](../archive/2026-10-11-fix-paper-order-session-evidence/session-migration-verification.md).

The hourly continuation now references the actual deployment and standing paper
order/fault authority. Local migration/readiness do not establish the remaining
provider lifecycle, fault/recovery, caller retry or visibility/restart acceptance;
tasks 2.1–2.5 remain unchecked. Original refused/spent intents remain immutable.

## Hourly continuation — 2026-10-10 04:01 SGT

The latest Session.RTH operator context supersedes the earlier analysis-only
handoff hold and expired per-attempt windows. Standing bounded paper-order and
fault authority remains valid. OpenSpec apply reports 2/7 tasks complete;
tasks 2.1–2.5 remain incomplete. No completed compatibility code or task was changed.

At 2026-10-09 20:01:22 UTC, an authenticated journaled MCP read reported
US.AAPL `AFTER_HOURS_BEGIN`. This ends the run before any mutation, retry,
fault, recovery acknowledgement or restart. No old intent was replayed.

Read-only Docker inspection confirmed the expected Session.RTH image,
linux/amd64, running with zero restarts in the fixed orbstack Mac project.
Both original named execution-data and opend-data volumes remain mounted.
The shared runner lock was held. The container process listing contained
OpenD and two Python processes; it does not independently audit remote account
writers. The operator's other-client READ_ONLY assertion remains the basis for
sole account use, supplemented by fresh account/order/position observations.

Fresh MCP and original-journal reads confirmed SIMULATE/US, the exact sole
allowlisted account ending 0047, USD 336 cap, maximum quantity two,
automatic journal creation disabled, SDK 10.10.7008 and gateway API 1010.
Normal and distinct operator authentication succeeded; anonymous access was 401.
Journal READY, integrity ok, schema 2 and original inode/identity are preserved,
with three operations, zero in-flight/blocking/reviews/recovery audit rows.
The admission epoch matches the session-migration observation. All three complete
operation-row hashes, original evidence hashes and cancellation evidence hashes
match that baseline. Current/history queries both returned the original
CANCELLED_ALL order, quantity one at USD 1.00, zero fills. There were no unrelated
orders and no positions. This establishes fresh readiness facts, not a successful
new lifecycle or caller retry/dispatch suppression.

The provider trading calendar for 10–20 October lists 12 October 2026 as its
next US trading day (`WHOLE`, market-local date). Resume read-only preflight at
2026-10-12T14:00:00Z, an estimated clock slot rather than permission to dispatch.
Reconfirm the calendar, regular session, exact image/account/current epoch,
journal and fresh orders/positions before any future mutation. Do not poll
through the closed session or turn this timer into a failed-mutation retry.

The next task-2.1 lifecycle retains the recorded bounds: one new US.AAPL
SIMULATE BUY, quantity one, NORMAL/DAY/Session.RTH at USD 1.00, at most one
price-only modification to USD 1.01 and one exact-order cancellation. Maximum
one new order, three mutation intents and one additional share/USD 1.01 gross
exposure. Each new original intent/current epoch and one-shot marker must be
saved atomically before its call. Stop on refusal, rejection, fill, uncertainty,
blocking or unexpected account activity; no sale or price chasing. No new
intent/marker was created during this closed-session read-only run.

Task 2.2 response-loss/recovery capability and task 2.5 bounded visibility/restart
orchestration remain to be inspected and locally verified before live use.
No capability assessment, injection, visibility timing or SDK-dispatch
suppression evidence is claimed here. Task 2.4 remains incomplete because this
is a partial dated record, not complete acceptance publication.

Redacted evidence: `env/paper-test-mac/hourly-validation/20261009T200123Z.read-only-evidence.json`.
Unchanged local checks were not rerun. No deployment, PR, push, merge or archive ran.


## Source separation and session timing — 2026-10-11

The operator requested a dedicated Session.RTH fix branch/PR and a validation
branch containing only planning and verification documents based on the resulting
main revision. [Fix PR #53](https://github.com/evil-sparkle/moomoo-api-mcp/pull/53)
contains the source/tests, synced main execution-journal requirement and completed
[fix archive](../archive/2026-10-11-fix-paper-order-session-evidence/tasks.md).
It preserves newer main's automatic recovery and broker-governance changes.

The broader earlier caller-agnostic edits to archived OpenSpec/container specs
are preserved separately on local `docs/mcp-caller-agnostic`, commit `2434acc`.
They are not bundled into the runtime fix or this validation branch.
The validation branch contains only this change's proposal, design, tasks and
verification documents. Task checkboxes remain 1.1/1.2 complete, 2.1–2.5 pending.

Full local pytest passed through the fix repository commit hook; 150 focused
paper tests, Ruff lint/format, basedpyright, strict OpenSpec and secret/whitespace
checks also passed. All eight PR checks completed successfully on exact head
`69225c01b8f607c697cec6ad865360faf461549f`:
[application CI](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/38087821770)
and [default/rootless tunnel integration](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/38087821783).
GitHub reports PR #53 merged at 2026-10-10 21:35:37 UTC as main commit
`e2bb7cfab1a71929808d49b2b61a96a7ddc2eb5a`. It was merged while the interactive caller was waiting;
this caller did not issue a duplicate merge. The merged tree equals the tested
PR tree exactly. The validation documentation commit was rebased onto that main
without conflict; its runtime source, tests, scripts and workflows match main.
Strict OpenSpec passed again (27 items), and only the four files of this change
appear in the branch diff. No provider-acceptance task is completed by these
local or CI checks. Post-merge main publication is a separate CI run, not claimed
passed by the PR checks.

Read-only Docker inspection still showed original Session.RTH image
`sha256:e499fd77da566746056704c0082145422ed7d0c2802bbb0c23328ffea79587d6`
running with zero restarts. No container recreation, journal mutation, provider
order, fault injection or recovery acknowledgement occurred in this branch work.
The existing Mac image was built from older `1c99e60` plus the local session
patch; it does not establish acceptance of later main's recovery/governance code.
A matching intended-main image and fresh account/auth/journal/session preflight
are prerequisites before further live acceptance of the new revision. The shared
hourly lock prevents concurrent execution during this work; the durable prompt
records this source/image prerequisite and read-only preparation until resolved.
Standing bounded paper-order/fault authorization remains valid.

The [task timing guidance](tasks.md#session-timing) distinguishes live mutations
from work that can proceed at any time: tasks 2.1/2.2/2.5 use freshly confirmed
US regular trading for mutation sequences; read-only reconciliation, preparation
and publication can proceed after close. Task 2.3 can exercise an identical,
durably acknowledged retry outside market hours when no SDK dispatch occurs.
Unknown/refused intents are never replayed. Task 1.2 specifically measures
after-close retention. Advance order submission remains a separate historical
observation and does not complete the full provider lifecycle.
