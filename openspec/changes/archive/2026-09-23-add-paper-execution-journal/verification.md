# Stage 2 development verification — 2026-09-23

The operator authorized archiving completed development with live provider
validation retained in `validate-paper-execution-provider`. No live broker order,
provider login, deployed configuration change, or ZeroClaw/Telegram experiment was
performed in this development run. REAL-order journaling remains deferred.

## Verified development scope

Paper mutations persist in the same dedicated SQLite journal under SIMULATE and
REAL deployments. A restart test places in SIMULATE, retrieves the same operation
in REAL, places another paper operation there, then returns to SIMULATE and
retrieves both without additional dispatch. READ_ONLY neither opens nor creates
the journal. REAL trading retains Stage 1 policy and is not stored in this database.

Caller-owned identity, concrete frozen account binding, decimal request identity,
serialized authoritative modification reads, durable dispatch markers and
conservative uncertainty classification are implemented. Recovery requirements
survive independently of lifecycle state and across two restarts. Recovery
acknowledgement checks fresh broker facts and resulting positions and atomically
persists its audit/disposition. The operator capability is separate from ordinary
agent authentication and is supported only by stateless streamable HTTP.

## Local gates

The virtual environment was recreated from the locked dependency set before the
final gates (`uv sync --locked --extra dev`, CPython 3.12.13).

| Gate | Result |
| --- | --- |
| `uv run pytest -q` | 1189 passed, 1 skipped, 72 subtests passed |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | 72 Python files formatted |
| `uv run basedpyright` | 0 errors, 0 warnings |
| OpenSpec 1.13.1 `validate --all --strict --no-interactive` before archive | 27 passed, 0 failed |
| Delta scenario/table comparison | 147 scenarios across 16 requirements; exact 1:1 mapping, no orphan rows |
| Delta/main comparison | All six Stage 2 capabilities synchronized |
| `scripts/smoke-test.sh` | Passed; gateway stub restart preserves MCP, MCP exit restarts container |
| `scripts/test-paper-container.sh` with built original/replacement images | C01–C04 passed, network disabled |

The full suite emits existing protobuf/fork deprecation warnings. The later
mode-continuity extension passed in its 54-test paper suite. Repository commit
hooks and PR CI repeat the required checks on committed content.

## Acceptance implementation map

The scenario table in `tasks.md` remains the exhaustive scenario-to-suite mapping.
These executable files supply the named suites; parameterized tests and shared
fixtures exercise multiple scenarios.

| Suites | Main executable coverage |
| --- | --- |
| U01–U04 | `tests/test_services/test_paper_execution.py`, `tests/test_execution_identity_contract.py`: token forms, decimal precision, frozen accounts, concurrent admission, immediate retries, serialized patch merge |
| U05–U07 | `tests/test_services/test_execution_store.py`, paper tests: per-worker connections/pragmas, lock timeout, no transaction during gateway calls, subprocess crash windows |
| U08–U10 | Paper tests plus retained trade-service tests: gateway acknowledgement/error/timeout, conversion/write/read failures, distinct fill/cancel facts |
| U11–U13 | Store/paper tests: candidate versus identity, contradictory observations, corrupt/newer/WAL storage, restore epochs, startup gates |
| U14 | Config/startup tests, trading policy and MCP trading tests: both deployment modes, account verification, out-of-scope refusal, strict quantity and decimal arguments, pre-listener storage failure |
| U15–U16 | Store/paper/trade-service tests: independent journal blocking, concurrent failed storage, process locks and no replay |
| U17/U19/U20 | Store/paper tests plus `tests/test_operator_capability.py`: durable audit, two restarts, real HTTP agent/operator isolation, terminal exposure accounting and contradictory-evidence refusal |
| U18 | Store/paper, system and event-loop tests: health precedence/counts, unwritable storage and bounded independent journal probe |
| C01–C04 | `tests/fixtures/paper_container_checks.py` via isolated container runner: UID 10001, image recreation, lock exclusion, concurrent online backup, older restore, genuine hot rollback journal, READ_ONLY lifespan/tool read without a volume |

C01–C04 ran on local Docker using linux/amd64 images on an ARM host. Only expected
platform-emulation notices were emitted. Disposable containers and volumes were
cleaned up. The existing smoke test used its fake OpenD, never the provider.

## Specification and configuration notes

The minor U01–U18 design typo was corrected to U01–U20 in its own commit.
Stage 1's already-implemented safeguard deltas were synchronized first. Stage 1
remains active for its unfinished provider checks, and its overlapping order
requirements now carry forward Stage 2 so a later archive cannot revert them.

The shared REAL/paper mutation tools retain a combined schema; the paper branch
requires its tokens and decimal-string price before admission. Ordinary trading
credentials cannot claim operator authority through tool arguments.

Workspace secret-file constraints excluded environment files from edits. The safe
configuration table and recovery/backup runbook are in `docs/paper-execution.md`,
linked from README and the state/deployment guides. The optional Compose overlay
uses one stable named paper volume without changing the OpenD authorization mount.

## Remaining validation

After-close retention, live lifecycle checks, controlled response-loss recovery,
and ZeroClaw/Telegram identity propagation remain incomplete in the active
follow-up. Automated fake-broker and container results do not verify those paths.

## PR review adaptation — acknowledgement versus observed modification

The review of `886db36` recommended separating the mutation acknowledgement from
later authoritative order visibility. `DelayedObservationBroker` now retains the
old order fields after an acknowledgement-only response, then publishes the new
state independently. Seven additional cases cover both dependent field directions,
with and without restart, cancellation availability, schema-1 migration, and a
failed successor preparation followed by a regressed observation.

Dependent modifications refuse before their dispatch marker while quantity/price
still disagree with the earlier acknowledged request. The guard persists in schema
2; schema 1 upgrades atomically and preserves recorded operations and epochs.
A predecessor's guard retires only in the transaction that durably acknowledges its
successor. Refused tokens and acknowledged tokens remain non-replaying on retry.
The double's no-transaction-across-I/O probe now allows a bounded wait for another
thread's legitimate short admission transaction, avoiding a race that conflated
that transaction with one spanning the calling thread's gateway invocation.

Updated local validation: **1196 passed, 1 skipped, 72 subtests passed**, Ruff lint
and formatting passed, basedpyright zero errors/warnings, all 26 active/main
OpenSpec items passed strict validation, and C01–C04 passed against rebuilt
application/replacement images. The live-provider follow-up's task 2.5 records the
unverified timing experiment; these results do not establish provider behavior.
