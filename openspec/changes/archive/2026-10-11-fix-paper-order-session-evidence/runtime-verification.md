# Mac compatibility activation and cleanup — 2026-10-10

This is the historical 03:07 activation/cleanup of the initial N/A-compatible
image. The subsequent current Session.RTH image is recorded in
[session-migration-verification.md](session-migration-verification.md).

The reviewed fix was locally built and activated in the dedicated Mac paper stack.
Exactly one cancellation of the original unfilled order was acknowledged, and
current/history reads verified `CANCELLED_ALL` with zero fills and no positions.
This establishes the explicit RTH/legacy-N/A cancellation path with the provider;
it does not complete the provider place/modify/cancel lifecycle or other live tests.

## Authorization and scope

The operator explicitly authorized the replacement build and one recreation with
the original journal/volumes, plus at most one cancellation of the existing order.
Standing paper-order/fault permission did not expand this handoff: no placement,
modification, sale, fault injection, recovery acknowledgement or mutation retry ran.
Only Docker context `orbstack`, project/container `moomoo-paper-test-mac` and
loopback endpoint `http://127.0.0.1:18080/mcp` were used. No VPS or production
deployment was accessed. SIMULATE/US, the exact allowlisted account ending 0047,
USD 336 cap, port and credential-consuming configuration route were retained.
No private settings, credential files or environment files were inspected or edited.
Existing reviewed helpers consumed credentials internally; no values were exported.

## Image provenance

| Item | Observed value |
| --- | --- |
| Previous image | `sha256:5cbb88e950a1ea52dd5825deb00ff14e2dea619bdfa3fe1d1f8f89ba3978db01` |
| Previous source | `07b119aad4d1666b6aebbfa21f3c112955fdd283` |
| Replacement actual image ID | `sha256:5fa14c279113e4aa91d8b41f5d11533f1a4e109ea7d5f761b8286e3d468fe1d2` |
| Distinct local tag | `moomoo-paper-test:local-rth-na-1c99e60-624f469b007d` |
| Source HEAD | `1c99e605d6f7c3f6e30c31a21c36df37b891b016` |
| Source-only patch SHA-256 | `624f469b007d6a3b3e75fe5cee4993b9f5671456eb65b7b8c7cd17912c9f0480` |
| Source and test patch SHA-256 | `281391f2929b581463e9de7b6ebef8fbd1222359bde167bdaf365f8129fc6355` |
| Build manifest SHA-256 | `691cb02e935bbd8c3c9733cf14b3e04fa223ec61506f98da9f90c18592c67ae3` |
| Installed paper_execution.py SHA-256 | `e93d65285894101139a6be4cf1fdb67f7c6763d85e74180ed17cb4ce4542c586` |
| Platform | linux/amd64 |
| Activated/start timestamp UTC | 2026-10-09T19:07:30.139244146Z |
| Container recreations | One; subsequent restart count zero |
| CI status | Local build; no CI approval claimed |

Committed Docker build inputs at this HEAD are identical to the old image's
source. The recorded uncommitted compatibility patch is the only application
source difference. The source-only staged build context excluded operational and
private files. Its manifest and reviewed source/test patch are retained locally.
The installed source hash matched the staged reviewed file; no guard normalization
or bypass was introduced. No additional application code changed in this handoff.

## Activation and journal preservation

The original named volumes remain mounted at their original destinations:
`moomoo-paper-test-mac_execution-data` at `/var/lib/moomoo-mcp/data`, and
`moomoo-paper-test-mac_opend-data` at `/home/opend/.com.moomoo.OpenD`.
The original journal identity, inode 1167351, UID 10001 and schema 2 were verified;
integrity was `ok`. Automatic creation remained disabled. No volume, journal,
identity or original marker was reset, replaced, initialized or deleted.

Before/after snapshots of all columns of both original operation rows had
identical SHA-256 hashes, as did the identity and original caller marker/result
files. Placement remains ACKNOWLEDGED/ACKNOWLEDGED with marker one; modification
remains REFUSED/NOT_SENT with marker zero. Their stored original epochs remain
unchanged. The recreation issued a new admission epoch, retained in the private
activation and cancellation records, without replacing either old intent.

Agent/operator authenticated MCP access succeeded; anonymous requests returned
401, including at the host's loopback endpoint. Remembered OpenD login succeeded
without private login input. SDK version was 10.10.7008; gateway API `server_ver`
was 1010. The pinned, checksum-verified OpenD 10.10.7008 artifact was retained:
its actual binary SHA-256 `e05b901dbe9107061b954a7fe3c059916c4a1e580d8f0a57277d402438898601` matched before and
after activation. The API's 1010 report is distinct from the full artifact version.

Only this Mac container was active in OrbStack. Process names showed one
supervisor, one OpenD and one MCP process; READY journal process ownership and
zero in-flight/review/blocking entries were verified. The other client remains
READ_ONLY according to the operator's existing sole-writer confirmation;
Docker evidence does not independently audit remote broker clients.

## One cancellation and factual outcome

| Time on 2026-10-10 (SGT) | Observation |
| --- | --- |
| 03:04:59 | Old-stack preactivation probe: original journal READY/intact with two operations; original order SUBMITTED, quantity one at USD 1.00, zero fills and no positions. |
| 03:07:40–41 | Fixed image: matching current/history NORMAL/DAY/RTH target with raw `fill_outside_rth="N/A"`; SUBMITTED, zero fills, no unrelated orders or positions. Provider market state AFTERNOON. |
| 03:09:10.289 | After fresh account, target, regular-trading, epoch, journal and executor checks, sent exactly one journaled MCP cancellation using a fresh operation ID and the new epoch. Durable caller markers were written exclusively before dispatch. |
| 03:09:10.676–11.094 | Cancellation journal state ACKNOWLEDGED/ACKNOWLEDGED, marker one. Current/history reads returned CANCELLED_ALL, quantity one, limit USD 1.00, zero fills. Positions empty; no unrelated orders. Journal READY, integrity ok, three operations, zero reviews/recovery audit rows. |
| 03:10:58.340–58.662 | Later independent current/history/positions reads again returned CANCELLED_ALL, zero fills and empty positions. Original rows and evidence hashes remained unchanged; current epoch stable. |

Broker order reference is `8da2eefedf3ef77e`; full broker/account identities stayed
inside the original journal and authenticated calls. Saved placement operation
owned the target identity. The original refused modification was neither replayed
nor altered. The new cancellation operation ID and admission epoch are retained
in `env/paper-test-mac/compatibility-cancel-intent.json` and the original volume's
single-use cancellation marker. No automatic mutation retry or recovery occurred.

The first secondary evidence collector completed its broker reads, then exited 1
on the Docker process diagnostic because its requested format omitted PID. The
corrected PID/command diagnostic succeeded. The sanitized follow-up also captured
the successful later reads above. The cancellation's first current/history and
journal evidence was already successful. This generated no additional mutation.

## Verification and continuation

The actual source/test diff was reviewed. This handoff reran the focused paper
service suite (126 passed), Ruff lint/format on the two affected files, basedpyright
(0 errors/warnings/notes), strict validation of both changes and diff whitespace
checks. The earlier full-suite result remains 1,614 passed, one skipped and
106 subtests; it was recorded before this handoff and was not rerun unchanged.
Sandbox restrictions initially prevented uv cache/SDK log access; direct venv
checks plus scoped execution succeeded. No application code or regression tests
were changed after review, so no new behavioral test claims are made.

The hourly runner's existing filesystem lock was held during activation and
cancellation. Its 03:00 SGT run had already ended analysis-only, BLOCKED, at
03:02:32; no competing live executor was enabled. The hourly operator context
now records completed handoff, exact deployed image and cleanup evidence.
Cron stays analysis-only. The sole brokerage route is authenticated journaled MCP
in this fixed Mac container, with one coordinated interactive caller at a time.
The image/cap overlay is retained locally; the legacy Compose helper's mutating
commands still refer to the old pin and must not be used to restart this stack.

Validation tasks 1.1 and 1.2 remain complete; 2.1–2.5 remain unchecked. Still
unobserved: a successful modification/full lifecycle, response-loss and recovery,
caller retry propagation, full acceptance publication, and modification visibility
including restart/reverse-field timing. Standing permission is recorded; it is
not an expired-approval blocker. This handoff's one cancellation allowance is spent.
No PR, push, merge or archive occurred.

Local redacted evidence: `compatibility-image-provenance.json`,
`compatibility-reviewed.patch`, `compatibility-journal-before.json`,
`compatibility-postactivation-probe.json`, `compatibility-journal-after.json`,
`compatibility-activation-result.json`, `compatibility-cancel-intent.json`,
`compatibility-cancel-result.json`, `compatibility-final-observation.json` and the
saved one-shot orchestration sources under `compatibility-handoff-scripts/`.
All are under Git-ignored `env/paper-test-mac/`; originals in the persistent volume
and prior local evidence were preserved.
