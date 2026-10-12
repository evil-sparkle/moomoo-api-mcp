# Provider verification — 2026-10-09–12

As of 2026-10-12, all seven tasks are complete for the tested e2bb7cf image and
selected caller/account, including the operator-approved task 2.5 refinement.
See the completed live campaign and final artifact-scope sections below. The
dated incomplete observations remain historical evidence.

## Historical first-phase status — 2026-10-10

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
[`fix-paper-order-session-evidence`](../2026-10-11-fix-paper-order-session-evidence/proposal.md).
That change accepts explicit RTH with the exact unavailable legacy sentinel and
retains refusal for missing, contradictory or broader session evidence. Its
[local verification](../2026-10-11-fix-paper-order-session-evidence/verification.md) is
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
[compatibility runtime verification](../2026-10-11-fix-paper-order-session-evidence/runtime-verification.md).
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
See [migration evidence](../2026-10-11-fix-paper-order-session-evidence/session-migration-verification.md).

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
[fix archive](../2026-10-11-fix-paper-order-session-evidence/tasks.md).
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

## Merged-main Mac image deployment — 2026-10-11

The operator authorized replacing the Mac paper image with the merged-main
runtime. Remote main was fetched safely and remained
`e2bb7cfab1a71929808d49b2b61a96a7ddc2eb5a`.
[Main CI run 38088219078](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/38088219078)
completed successfully, including application checks, container smoke and image
publication. Its published image digest was
`sha256:8c45b7443ce0c111b5ea0b1cdf62a05e06b898c519a3cb5e4208d6906e8f293e`.
The Mac could not authenticate the ECR pull. The interactive caller therefore
built locally from exactly that committed source, using 39 allowlisted tracked
Docker inputs without private configuration or credentials in the build context.
This is a local build of CI-checked source, not the CI-published artifact.

The active local tag is `moomoo-paper-test:main-e2bb7cf`, pinned by image ID
`sha256:25779f3f9b16e521bc8092926aa97ccddddc3088deb41be7f994d10b4c45b0cb`.
The platform is `linux/amd64`, service UID remains 10001, and all 33 packaged
source files match merged main. Build-input manifest SHA-256 is
`241da1a9b2f019b739872c3b5a24cd27876aa0d2af8ffd9c0cc10c4eae3e6077`.
The exact source label and manifest label were verified before activation.
SDK/OpenD artifact versions remain 10.10.7008; the gateway reports API 1010.
OpenD binary SHA-256 is unchanged:
`e05b901dbe9107061b954a7fe3c059916c4a1e580d8f0a57277d402438898601`.

The shared hourly lock covered build, backup, one container recreation and
verification. Only `moomoo-paper-test-mac` was recreated, retaining the same
Compose project, loopback `127.0.0.1:18080`, unpublished OpenD port and original
`moomoo-paper-test-mac_execution-data` / `moomoo-paper-test-mac_opend-data`
volumes. SIMULATE/US, the exact one-account allowlist ending 0047, USD 336 cap,
quantity cap 2, remembered login and automatic journal creation 0 are unchanged.
No credential file was inspected or changed. No VPS or production deployment ran.

Main includes the schema-3 automatic recovery implementation. Before recreation,
a consistent schema-2 SQLite backup was created in the execution volume with
mode 0600, integrity `ok`, three operations and SHA-256
`04a811114defb53877ac337a40ea321f7e5c2fac72bdb224163dc209c2017169`.
Startup atomically migrated the original journal from schema 2 to 3. Its identity
and original inode were retained. Hash comparison over every original operation
column verified all three stored operations, their admission epochs, states,
dispatch markers, receipts and evidence unchanged. Original identity/intent/result
files and cancellation marker hashes also match. Added columns have expected
null/zero defaults, and the new recovery-check table is empty. Previous journal
epochs remain, with exactly one new executor epoch; no existing intent was retagged
or replayed. Whole-row hashes across different schemas are not claimed identical.

The first post-recreation probe reached authenticated MCP after approximately
eight seconds, while OpenD was still starting. It accurately reported disconnected
broker access, so the activation helper stopped its verification sequence without
another recreation. A subsequent read-only probe at 2026-10-10 22:00:56 UTC
(2026-10-11 06:00:56 SGT) observed remembered login and connected quote/trade
services. Final read-only verification completed at 06:01:17 SGT. The journal
is READY, integrity `ok`, with three operations, zero in-flight/blocking/review
records, zero recovery checks and zero recovery audits. No order, fault injection,
automatic mutation retry or recovery acknowledgement was performed.

Normal MCP authentication and host-loopback access are verified. Anonymous access
returns HTTP 401. Current main intentionally removes the separate operator token
and manual recovery override; the retained legacy operator credential also returns
401. Recovery is process-owned, read-only and durably audited against broker
evidence. Historical operator-capability checks in task 1.1 describe the earlier
revision. Tasks 2.2/2.5 must use an orchestration method consistent with the current
automatic recovery contract; the retired override must not be reintroduced to
make validation pass.

Fresh account observations returned zero current orders and zero positions.
The original test order is no longer in the current-order list; historical orders
still return exact order reference `8da2eefedf3ef77e` as CANCELLED_ALL, quantity one,
limit USD 1.00, NORMAL/DAY/RTH, zero fills, with no unrelated history rows.
This extends the history-retention lower bound without measuring current-list
expiry. Provider market state was AFTER_HOURS_END. The operator's previously
confirmed sole-writer arrangement remains the exclusivity basis; Docker isolation
does not independently establish brokerage-account exclusivity.

The canonical local Compose helper now verifies and selects this exact image.
The hourly prompt/readiness record uses the new source/image/schema and normal
authentication. The source/image blocker is resolved; standing bounded paper
authorization remains, with fresh account/journal/session checks before every
mutation sequence. A concurrent runner invocation was verified to skip under the
shared lock. No acceptance sequence ran during deployment. Tasks 2.1–2.5 remain
unchecked. Live mutation sequences require freshly confirmed regular US trading;
fault/visibility cases additionally require safely bounded, locally verified
orchestration against current main. No new PR is needed for this environment work.

Private redacted evidence is retained under `env/paper-test-mac/`:
`main-image-e2bb7cf-provenance.json`, `main-image-e2bb7cf-journal-backup.json`,
`main-image-e2bb7cf-preactivation.json`, `main-image-e2bb7cf-postactivation-probe.json`,
`main-image-e2bb7cf-poststartup-probe.json`, `main-image-e2bb7cf-after.json` and
`main-image-e2bb7cf-activation-result.json`. These operational files and the
journal backup remain outside Git.

## Hourly continuation — 2026-10-11 06:09 SGT

OpenSpec apply reports 2/7 tasks complete; 1.1/1.2 remain complete and 2.1–2.5
remain unchecked. The latest merged-main deployment section supersedes the older
image, separate-operator-authentication and analysis-only handoff blockers.
Standing bounded paper-order/fault authority remains valid. Existing edits and
all historical evidence are preserved.

Read-only inspection confirmed the expected main-e2bb7cf image
`sha256:25779f3f9b16e521bc8092926aa97ccddddc3088deb41be7f994d10b4c45b0cb`,
linux/amd64, running with zero restarts in the orbstack Mac stack. Both original
named volumes and loopback port 18080 remain intact. The shared runner lock was
held. The container process listing showed OpenD and two Python processes,
consistent with the service/supervisor baseline. This does not independently
audit remote account use; the operator's other-client READ_ONLY confirmation
remains the sole-writer basis.

Authenticated, journaled MCP reads completed at 2026-10-10 22:09:35 UTC.
They freshly verified SIMULATE/US, the single exact allowlisted account ending
0047, USD 336 cap, quantity cap two, automatic journal creation zero, remembered
login, SDK 10.10.7008 and gateway API 1010. The original schema-3 journal is READY,
integrity `ok`, with three operations and zero in-flight/blocking/review/recovery
check/recovery audit records. Identity/inode, all original operation fields and
stored epochs, original evidence and cancellation markers match the deployment
baseline. The freshly read admission epoch also matches that baseline; its
redacted reference is `75552440ab12dc9f`.

Fresh current orders and positions are empty. Historical orders contain only
the original order reference `8da2eefedf3ef77e`: CANCELLED_ALL, BUY one US.AAPL
share, NORMAL/DAY/RTH at USD 1.00, zero fills. Its continued historical presence
is a retention lower bound; the empty current list does not prove absence or
measure expiry. No unexpected account activity was observed.

At 22:09:34 UTC the provider reported US.AAPL `AFTER_HOURS_END`. The run stops
before mutation. A single calendar read for 10–20 October returned 12 October
2026 as the next US trading day, type `WHOLE`, in market-local dates. Resume
read-only preflight at **2026-10-12T14:00:00Z**. This is an estimated clock slot,
not authorization to dispatch: reconfirm the provider calendar, regular session,
image/account/current epoch, journal and fresh orders/positions then.

No provider mutation, caller retry, fault, recovery acknowledgement, restart,
deployment, new intent or marker was performed. No historical helper was run.
Unchanged local tests were not repeated. Task 2.1 still needs a new bounded
place/modify/cancel lifecycle; 2.3 needs factual caller identity propagation and
SDK dispatch suppression. Before live 2.2/2.5 use, conservative orchestration
must be locally verified against main's automatic, durably audited recovery
contract. The historical manual operator-recovery wording is not permission to
call or reintroduce the retired override. No fault capability, response-loss
recovery or modification visibility timing is claimed by this read-only run.
Task 2.4 awaits the complete acceptance record.

Redacted evidence:
`env/paper-test-mac/hourly-validation/20261010T220935Z.read-only-evidence.json`.
No runtime code, runner, prompt or operator-authority file was changed. No PR,
push, merge or archive was performed.

## Offline orchestration preparation and dated publication — 2026-10-11

After the hourly run stopped at AFTER_HOURS_END, the interactive caller held
the shared runner lock and prepared external test instrumentation for the
remaining response-loss/retry checks. Packaged main source and the live container
were unchanged; the instrumentation has not been activated in the Mac stack.
All helper files and operational proof records remain private and Git-ignored.

`stage2_validation_observer.py` records entry at the actual SDK place/modify
boundary and can discard one successful SDK response for an explicitly bound
tagged placement. It uses exact account/operation/epoch/target/quantity/price
bindings, an expiry, durable exclusive per-operation claims, one global loss
claim and conservative campaign ceilings. An unexpected second SDK entry is
logged as a failure even when the observer prevents forwarding. It records the
shipping journal's committed outcome and actual gate state; it does not write
execution records or replace automatic recovery.

`stage2_acknowledged_retry.py` provides a selected MCP-compatible caller path:
two identical calls for a previously durable ACK, one caller-local stored-reply
loss and unchanged payload, operation ID and admission epoch. Transport failures,
refused/unknown intents or changed journal/SDK-entry evidence stop the check.
The live transport and snapshots must still be connected during provider testing.

At 2026-10-10 22:17:50 UTC, 18 instrumentation/caller checks passed inside the
exact pinned main image with networking disabled and neither original volume
mounted. Coverage includes finite/exact paper bounds, wrong-account/REAL refusal,
one response-loss ceiling, duplicate claims across observer restart, campaign
budgets, actual SDK-class installation, real temporary schema-3 SQLite blocking,
identical caller retry and rejection of changed SDK-count/uncertain evidence.
The temporary fixture journal had no brokerage connection. A separate isolated
READ_ONLY bootstrap check served 34 registered MCP tools through the normal
package entry point, exposed no retired recovery override and recorded zero SDK
mutation entries. No installed dependencies or packaged application files were
patched to make these checks pass. These are offline orchestration evidence;
they do not complete live response-loss, caller retry or provider visibility.

The private campaign plan records a first intended live window of 12 October
2026, 22:00–23:00 SGT, conditional on fresh provider-confirmed regular trading.
Serial limits are five new US.AAPL BUY orders, 17 SDK mutation calls, 22 trading
MCP requests, one SDK reply loss, one caller-local stored-reply loss and at most
three test container recreations, preserving the pinned image and original
journal/volumes. Quantities are one/two shares and limits USD 1.00–1.01; flat
baselines and one outstanding order bound intended simultaneous gross purchase
exposure to USD 2.02. The first lifecycle retains its one-share/three-intent
bound. No phase may reset evidence, replay an unknown/refused intent or fabricate
provider lag. Automatic recovery must supply reliable durable broker accounting
before uncertain-phase cleanup; an absence assumption alone does not suffice.

Current acceptance status:

| Task | Verified result and remaining limitation |
| --- | --- |
| 2.1 / M01–M02 | New-main setup/readiness verified; successful new place/price-modify/cancel lifecycle remains unperformed. |
| 2.2 / M03 | Bounded external SDK reply-loss/gate observation prepared and checked offline; live uncertainty, durable blocking and automatic broker recovery/audit remain unperformed. |
| 2.3 / M04 | Caller retry and SDK-entry observer prepared and checked offline; two actual MCP retry calls and measured zero additional SDK entries remain unperformed. |
| 2.4 | This dated record publishes observed results, limitations and unfinished checks, with captured evidence retained. |
| 2.5 / M02 timing | Smaller two-to-one-share cases are bounded; live forward/reverse visibility and a restart during genuine provider lag remain unperformed. |

Task 2.4's stated criterion is publication distinguishing incomplete checks; it
does not require those checks to have passed first. Its checkbox is complete.
Progress is now 3/7, with tasks 2.1, 2.2, 2.3 and 2.5 still unchecked. No live
order, caller retry, response loss, recovery acknowledgement or process restart
was performed during this preparation. No journal/volume/evidence reset occurred.

Private preparation evidence: `env/paper-test-mac/stage2-observer-offline-verification.json`
records the tested image, helper hashes, isolated setup and checks.
`env/paper-test-mac/stage2-validation-campaign.md` records execution ceilings,
activation/restoration preflight, per-intent bindings, stop conditions and the
conditional visibility limitation. The hourly handoff uses these artifacts and
preserves standing authorization. Fresh regular-session confirmation is still
required before activation followed by any live mutation sequence.

The private hourly runner now samples one redacted, read-only `check_health`
result before its unchanged-input gate. Automatic recovery can change broker/
journal state without editing source files; this observation permits renewed
analysis when that evidence changes. Two live read-only samples verified a
connected READY journal with zero blocking/in-flight operations and an identical
fingerprint for unchanged facts. Observation timestamps are excluded, so stable
state does not trigger repeated model work. The sample performs no order, retry,
fault or recovery override. The scheduler still refuses to replay uncertain
operations; a changed recovery result is evidence to assess, not new mutation
authority. Its code and state are private operational artifacts outside Git.

## Main documentation update and final continuation checks — 2026-10-11

The verification branch was rebased onto main
`d34140847cd693834d87b880856e917426760cdc`, retaining its framework-independent
MCP agent-client criteria. Relative to main, the branch changes only this
validation change's proposal, design, tasks and dated verification record.

The deployed image remains pinned to the already verified selected source
`e2bb7cfab1a71929808d49b2b61a96a7ddc2eb5a`. Between that source and the new main,
the only runtime/build-input difference is a comment in `server.py`; a direct
Python AST comparison is identical. Tests, dependencies, Dockerfiles and
workflows have no differences. Packaged file hashes are proven against the
selected e2bb7cf source, not claimed equal to the newer comment revision. No
container recreation or further image build accompanied this documentation update.

The six prepared helper/overlay hashes match the retained offline proof and all
are private, Git-ignored files. Exactly one hourly cron entry remains at minute
00. An invocation while the interactive preparation lock was held skipped before
starting another validation run, verifying exclusive continuation. The handoff
retains four live criteria and the planned 12 October 22:00 SGT regular-session
preflight. Caller acceptance must identify the actual tested MCP agent/client,
version, transport and intermediary; the offline harness alone cannot complete
that caller-chain criterion. No agent framework or messaging channel is required.

A final read-only continuation check again confirmed authenticated MCP broker
connectivity, READY state and zero blocking/in-flight operations. SQLite integrity
is `ok`, exactly the original three operations remain, the original journal inode
and both named volumes are preserved, and the external observer directory is
absent from the live stack. The pinned image and loopback-only MCP mapping are
unchanged. Strict OpenSpec validation passes; refreshed apply instructions report
3/7 complete with precisely 2.1, 2.2, 2.3 and 2.5 outstanding.


## Hourly continuation — 2026-10-11 07:01 SGT

The current OpenSpec apply result is 3/7 complete. Tasks 1.1, 1.2 and dated
publication 2.4 remain complete; 2.1, 2.2, 2.3 and 2.5 remain unchecked. The latest
operator handoff supersedes historical image/auth/deployment and expired-window
blockers. Standing bounded paper-order/fault authority remains valid. No runtime
code, runner, prompt or operator-authority file was changed.

At 2026-10-10 23:01:45 UTC the authenticated journaled MCP provider returned
US.AAPL AFTER_HOURS_END. One fresh US calendar query for 10–20 October returned
12 October 2026, WHOLE, as the next trading day in market-local dates. The run
stopped at this closed-session prerequisite without a live attempt. Resume
read-only preflight at **2026-10-12T14:00:00Z**, within the prepared campaign's
14:00–15:00 UTC window. This clock estimate is not dispatch permission; freshly
reconfirm calendar/session and every execution gate before any future mutation.

Read-only Docker inspection confirmed the selected pinned e2bb7cf image,
linux/amd64, zero restarts, the fixed orbstack project, loopback port 18080 and
both original volumes. The shared runner lock was held. Process observations
were consistent with the existing service/OpenD/supervisor baseline; they do
not independently audit remote account writers. The operator's other-client
READ_ONLY confirmation remains the sole-writer basis.

Fresh normal-authenticated MCP reads verified SIMULATE/US, exactly one configured
allowlisted account ending 0047, remembered login, SDK 10.10.7008, gateway API
1010, USD 336 cap, quantity cap two and automatic journal creation zero. The
original journal is schema 3, integrity ok and READY with no in-flight/blocking,
reviews, recovery checks or recovery audit rows. Identity/inode, all three
original operation fields and stored epochs, evidence files and cancellation
markers match the deployment baseline. The freshly read current epoch also
matches it; its redacted reference remains `75552440ab12dc9f`.

Current orders and positions are empty. History contains only the original
CANCELLED_ALL order, zero fills, one share at USD 1.00, NORMAL/DAY/RTH. This is
another dated historical-retention sample, not measured expiry or absence proof.
No unexpected account activity was observed. All six prepared helper/overlay
hashes match stage2-observer-offline-verification.json. External instrumentation
remains inactive; unchanged offline tests were not rerun.

No order, caller retry, fault, recovery acknowledgement, container recreation,
deployment, new intent or one-shot attempt marker was performed. Preparation
and authenticated reads do not complete any live task. Task 2.2 will require
actual automatic, durably audited recovery rather than the retired operator
override. Task 2.3 will require the identified selected MCP agent/client's real
retry chain; a standalone harness/offline callback is insufficient. Task 2.5
still requires genuine pending provider visibility for its conditional restart;
no lag may be manufactured. The prepared bounded campaign remains the method
for future activation, serial tests and restoration under standing authority.

Redacted evidence:
`env/paper-test-mac/hourly-validation/20261010T230146Z.read-only-evidence.json`.
The corresponding dated operator report and latest publication retain all four
remaining criteria. No PR, push, merge or archive was performed.

## Completed live Mac campaign — 2026-10-12

The operator requested all remaining tests after market open and approved refining
task 2.5 to measured real-provider timing plus a clearly labeled controlled stale-
observation restart. This section supersedes earlier incomplete-status statements;
it does not turn earlier offline checks into live evidence. The proposal, design
and task criterion retain this approved distinction. No shipping runtime change
was made on the verification branch.

### Scope, preflight and campaign accounting

The interactive Codex caller held the existing hourly runner lock throughout.
Fresh provider queries returned US.AAPL `AFTERNOON`, with a `WHOLE` US trading day
for 12 October. Before writes the sole allowlisted US SIMULATE account ending
0047 (reference `8f46b6109a1eb244`) had no current orders or positions, and the
original schema-3 journal was READY/unblocked, with integrity `ok`. The operator's
sole-writer confirmation remained in effect; the other client is READ_ONLY.

The recorded window was 13:39–15:00 UTC (21:39–23:00 SGT); all order/fault calls
finished by 14:12:53 UTC. Scope was US.AAPL BUY, NORMAL/DAY/RTH, one/two whole
shares and USD 1.00–1.01 limits. Maximum intended simultaneous gross purchase
exposure was USD 2.02, with one outstanding test order and flat baselines between
phases. Configured USD 336/quantity-2 caps remained unchanged.

Actual usage: five new orders, eight initially seeded shares across flat phases,
18 new journal intents and two known-ACK retry requests, totaling 20 trading MCP
requests and 17 SDK mutation entries/forwards. There was one SDK response loss,
one caller-local stored-reply loss and one controlled stale preparation read.
The three same-image recreations covered observer activation, the single test
restart and restoration. No REAL order, sale, fill, uncertain-operation replay,
manual recovery override, account/journal reset or evidence deletion occurred.

Runtime image stayed
`sha256:25779f3f9b16e521bc8092926aa97ccddddc3088deb41be7f994d10b4c45b0cb`,
local tag `moomoo-paper-test:main-e2bb7cf`, selected source
`e2bb7cfab1a71929808d49b2b61a96a7ddc2eb5a`, linux/amd64. SDK/OpenD remain
10.10.7008, API 1010. This is the verified local build, not a CI-published artifact.
The branch remains based on d341408 main, whose executable source is equivalent.

External instrumentation was extended to project only the exact dependent
preparation read, after obtaining a real fresh observation and checking the
unfilled SUBMITTED order, exact account/target, real reduced quantity and durable
unobserved prior acknowledgement. Current/history/positions remained unfiltered.
An isolated suite of 25 checks passed in the pinned amd64 image with networking
disabled and no original volumes mounted. These checks are preparation evidence;
the results below came from the actual authenticated MCP/broker path.

### Task 2.1 — lifecycle accepted

At 13:55:14.625 UTC the one-share USD 1.00 placement was ACKNOWLEDGED. The price-
only USD 1.01 amendment was ACKNOWLEDGED at 13:55:29.564, and cancellation at
13:55:44.106. Fresh current/history reads showed the exact order
`477dde6e67a59a52` CANCELLED_ALL, one share at USD 1.01, dealt quantity zero.
Positions remained empty. Each intent retained its epoch and had exactly one
SDK entry, forward and durable dispatch marker. Acknowledgement, broker visibility,
final cancellation and zero resulting filled exposure were observed separately.

### Task 2.3 — selected agent caller retry accepted

Tested chain: this T3 Code Codex agent (`gpt-6.1-sol`) -> agent-selected local
single-action transport -> Docker exec in the sole Mac container -> authenticated
stateless Streamable HTTP MCP -> unchanged shipping service. The MCP client
identified itself as `t3-code-codex-paper-validator/1.0`; initialize negotiated
protocol `2025-06-18` with `Moomoo Trading/1.25.0`. No messaging client or other
agent framework was required. Evidence is scoped to this selected caller chain.

At 13:56:21.085 UTC the caller received the durable placement's stored ACK and
injected one local reply loss. The agent then selected a second call at
13:56:29.536, using the identical saved payload rather than an automatic retry
loop. Both payload hashes were
`48062a8e0323bfa268395e29d964ecea2d350a346b40f7e3a28ba2dcd26e2edd`;
operation reference `368ae725b342957c` and stored epoch reference
`a5e821bf077e4bf8` remained unchanged. The second call returned ACKNOWLEDGED.
Full journal-row hashes and markers were unchanged, and both calls added zero
SDK boundary entries. This is actual agent/client/MCP evidence, not fixture
callbacks or an assertion about every compatible client.

### Task 2.5 — measured timing and injected restart accepted

The forward sequence reduced two shares to one, then changed only price to
USD 1.01. The reverse sequence changed price first, then reduced quantity to one.
Captured merged SDK requests preserved the requested field and never restored the
old quantity/price. All three visibility-case orders ended CANCELLED_ALL at one
share/USD 1.01, with zero fills.

Real provider samples:

| Case | SDK success returned UTC | Current query completed UTC | History query completed UTC | Observed fields |
| --- | --- | --- | --- | --- |
| Quantity reduction first | 13:58:26.496036 | 14:00:21.845116 | 14:00:21.997787 | qty 1, price 1.00 before price amendment |
| Price change first | 14:06:27.855061 | 14:06:28.642998 | 14:06:29.469695 | qty 2, price 1.01 before quantity reduction |

The forward immediate successor preparation was interrupted by a read-only
account-discovery timeout before any dependent intent or attempt marker existed.
The resumed current/history samples bound visibility by about 115.349/115.502
seconds after SDK return; this observation gap is **not** measured provider lag.
The reverse samples bound visibility by 0.788/1.615 seconds; its quantity-only
call began 1.789 seconds after SDK success return. These are sampled upper bounds,
not exact application times or a claim that provider visibility is always instant.
Transient read-only preflight failures were retained; no trading command was
replayed to hide them. The caller removed redundant discovery calls with a short
cache; the unchanged shipping service still verifies the account per new mutation.

For deterministic restart coverage, a real acknowledged reduction at
14:07:38.558 remained durably unobserved by the modification guard. Real queries
already showed one share/USD 1.00, so naturally pending provider visibility was
not claimed. Its original acknowledgement/epoch survived a same-image restart.
The observer then obtained the fresh, unfilled one-share order and projected the
captured old two-share quantity for exactly one dependent preparation read.
At 14:09:07.612 the price-only intent `b97f44393ec2e03f` was durably
REFUSED/NOT_SENT, marker zero, zero SDK entries, with the prior guard preserved.
The recorded reason specifically identified the unobserved prior modification.

After releasing that projection, unfiltered real current/history/positions
confirmed the reduced state. A separately recorded new bounded price-only intent
`b7df0e95fcab76f4` was ACKNOWLEDGED at 14:10:14.907, merged quantity one and
price USD 1.01. The refused intent's complete row hash stayed
`c5079a5cf480402785585b017e65a3f75df41a3bb2acb08db31ad261f8dc60c7`,
with zero marker/SDK entries. No refused token was retried. This establishes the
approved injected guard/restart criterion, not naturally occurring provider lag.

### Task 2.2 — response loss, gate and automatic recovery accepted

The final tagged one-share USD 1.00 placement had exactly one SDK entry and
forward. SDK success returned at 14:12:10.173159 UTC; the observer discarded its
response at 14:12:10.175676. At 14:12:10.186532 the unchanged shipping journal
committed UNKNOWN_OUTCOME/POSSIBLY_SENT with dispatch marker one, state
JOURNAL_BLOCKED and blocking count one. MCP returned truthful UNKNOWN_OUTCOME at
14:12:10.193452. The operation reference was `9e3d512c6fff6148`.

Process-owned read-only recovery found the exact persisted tag and consistent
current/history identity, recorded one successful recovery check and one durable
audit, and advanced the row to RECONCILED/BROKER_ACCOUNTED with recovery
BROKER_CONFIRMED. The exact tag match was independently verified; matching order
attributes alone were not used as ownership proof. At 14:12:46 fresh authenticated
reads showed READY/unblocked, exact order `a1b14f497c4ad361` SUBMITTED and unfilled,
with empty positions. Absence was not assumed, and no unknown operation was retried.

Only then was a new exact cancellation sent once and ACKNOWLEDGED at
14:12:52.929. Fresh current/history reads showed CANCELLED_ALL, zero fills.
The automatic recovery contract supersedes historical manual operator-override
wording: no retired `acknowledge_recovery` API or direct journal edit was used.

### Final preservation, cleanup and limitations

At 14:16:40 UTC, after restoring the normal packaged server entry point, normal
MCP authentication succeeded; anonymous and retired operator credentials returned
401. Broker was connected/logged in, journal READY, zero blocking/in-flight/review
work, integrity `ok`, 21 operations, one recovery check and one recovery audit.
The original three complete operation-row hashes, original journal identity/inode
1167351 and both original named volumes were preserved. The refused test token,
all per-operation SDK claims, fault markers and evidence remain durable.

All five new orders were CANCELLED_ALL in factual current/history reads, all
with zero fills; there were no nonterminal orders or positions. Fault instrumentation
is inactive after normal entrypoint restoration. Loopback MCP 18080, no published
OpenD port, SIMULATE/US, exact allowlist, USD 336 cap and automatic creation 0 remain.
No inference is made about REAL trading, fills, other accounts/clients, other SDK
versions, maximum retention expiry or naturally occurring provider lag.

Private retained artifacts: `stage2-live-bounds-20261012.json`,
`stage2-live-observer-proof-20261012.json`, `stage2-live-evidence-20261012/`,
`stage2-live-operation-records-20261012.json`, `stage2-live-restart-binding.json`,
`stage2-live-visibility-release.json`, `stage2-live-fault-gate-proof.json`,
`stage2-live-final-proof-20261012.json` and
`stage2-normal-entrypoint-final-baseline.json`, under Git-ignored
`env/paper-test-mac/`. The original volume also retains the full immutable intents,
responses, claims and observer events. No credential/header value was captured in
these artifacts. Completion is scoped to the revised seven verification tasks;
there is no new shipping behavior change, PR, push, merge or archive.

### Final repository rebase and artifact scope — 2026-10-12

During final checks, the shared remote main reference had advanced to
`1231aec3107939568f7ea102fa3cdcaf2226ffbf`, including additional runtime and
execution work beyond the tested source. The three verification-document commits
rebased cleanly onto that main. The final branch differs from main only in this
change's four Markdown documents; no implementation, tests, deployment scripts,
Dockerfiles, dependencies or workflows were changed by this branch.

The Mac container and original journal were retained on the tested e2bb7cf image.
The seven completed criteria establish acceptance for that explicitly recorded
Stage 2 artifact and tested caller/account, with the approved 2.5 refinement.
They do **not** establish acceptance for the newer 1231aec runtime. A later image
replacement needs its own source/image and journal/account preflight and bounded
validation; this completed campaign's intents and fault markers cannot be reused.
The completed hourly job is disabled and will not deploy or repeat tests on the
newer source automatically. Normal-entrypoint recovery evidence, refused tokens
and all original journal/volume data remain preserved.
