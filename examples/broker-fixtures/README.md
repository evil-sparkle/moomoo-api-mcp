# Synthetic broker adapter handoff

The directory [`2c019d50238d636deea5e49d3b48eac7868ff69b/`](2c019d50238d636deea5e49d3b48eac7868ff69b/)
contains **SYNTHETIC** source fixtures for the exact production-hotfix source
commit named in the directory. They are not captured market/account data, runtime
support evidence, entitlement evidence, a deployed-image attestation or a
performance measurement. No gateway/account/HTTP credential is used to generate
them. No runtime implementation or application code is changed by this handoff.

Start with [`manifest.json`](2c019d50238d636deea5e49d3b48eac7868ff69b/manifest.json)
and [`catalogue.market_read.json`](2c019d50238d636deea5e49d3b48eac7868ff69b/catalogue.market_read.json).
The manifest pins the source commit, runtime source-tree identity, each source
file's SHA-256, hotfix dependency-file hashes, serialization package versions,
selected exact Python signatures, schema identity and fixture byte hashes.
The catalogue is the complete actual registered `tools/list` for a synthetic
`market_read` grant, including descriptions, input/output schemas and annotations.
It is not a new SDK inventory or a claim about the currently imported connector.

The runtime source files were compared byte-for-byte to the historical commit.
The locally installed serialization packages match its lockfile: MCP 1.25.0,
moomoo-api 10.10.7008, Pydantic 2.12.5 and pandas 2.3.3. The manifest separately
records the generator's certifi 2026.1.4 versus the hotfix's certifi 2026.10.19;
no TLS/network operation is performed. Do not describe this as reproducing the
entire production image or weakening/replacing its certificate security fix.

## Wire shapes

Each scenario JSON has `fixture_kind`, `case_id`, `permissions`, `request`,
`response` and `broker_double_methods`. The request is JSON-RPC `tools/call`;
the response is produced through the registered low-level MCP handler and the
MCP `JSONRPCResponse` codec with synthetic SDK DataFrame broker doubles. The
surrounding fixture metadata is not part of the broker response.

| Tool | Actual response value path | Contract |
| --- | --- | --- |
| `get_option_expiration_date` | `response.result.structuredContent.result` | Legacy list, including empty `[]`; **no evidence envelope**. Nonempty lists also have one JSON text block per row. |
| `get_option_chain`, `include_evidence=true` | `response.result.structuredContent.result` | Wrapped evidence object; contracts at `.data`, submitted filters at `.scope`. |
| `get_market_snapshot_evidence` | `response.result.structuredContent` | Direct evidence object; quotes at `.data`. |
| `get_instrument_facts` | `response.result.structuredContent` | Direct evidence object; fact records at `.data`, explicit fact states under `.facts`. |

Always inspect `response.result.isError` before normalizing success data.
`chain.permission_error.json` is a synthetic error result, not an empty chain or
evidence that any real credential lacks permission. Validate success
`structuredContent` against the selected catalogue tool's `outputSchema`.

## Cases and consumer boundaries

| Files | Purpose |
| --- | --- |
| `expiry.normal.json`, `expiry.empty.json` | Preserve the legacy list and returned market-local expiry date. No invented source/receipt metadata. |
| `chain.normal_adjusted.json`, `chain.empty.json`, `chain.permission_error.json` | Preserve exact returned symbols, submitted delta filters, STANDARD/NON_STANDARD metadata, empty success versus failure. |
| `snapshot.normal.json` | Bid/ask/sizes with a timezone-free provider string and separate UTC request/receipt times. |
| `snapshot.partial_null_zero.json` | Missing requested code, explicit incomplete coverage/errors, zero bid/size, null ask/size/last. Zero and null are distinct; neither implies executable liquidity. |
| `snapshot.missing_timestamp.json` | Truly absent `update_time`; do not invent a provider timestamp from receipt time. |
| `facts.standard_incomplete.json` | Normal synthetic contract with incomplete lifecycle facts; compatibility remains false. |
| `facts.adjusted_non100.json` | Monetary multiplier 25, deliverable size 150 and lot size 1 are different facts. Join the exact code to the chain's NON_STANDARD row; no 100 fallback. |
| `facts.conflict.json` | Basicinfo/snapshot lot sizes disagree. State CONFLICT, value null, both observations retained. Never pick the convenient value. |
| `facts.zero_unknown.json` | Provider zero multiplier/deliverable normalizes to UNKNOWN/null, not a free or zero-sized contract. |
| `facts.missing_sources.json` | Missing snapshot/reference sources, placeholder fact row and incomplete coverage. A row in `.data` or `.coverage.returned` does not establish complete sources. |

Provider `update_time` remains exactly `2026-10-26 10:29:59.123` where supplied.
The synthetic UTC request time is `2026-10-26T14:30:00Z`; receipt is
`2026-10-26T14:30:00.250Z`. There is no verified timezone for the provider string,
no separate bid/ask update time and no measured per-side quote age. Keep those
UNKNOWN; do not infer them from the US prefix, subtract a timezone-free string
from UTC, or replace missing provider time with receipt time. Delay and
entitlement remain UNKNOWN in the actual evidence payloads.

`manifest.consumer_constraints` is explanatory handoff metadata, **not fabricated
API output**. Payload `source_revision` remains null; catalogue identity reports
`source_revision: "unknown"` and null build revision. The manifest's exact source
commit must never be copied into those fields as purported runtime evidence.

Currency, settlement mode/cutoff, full deliverable description and standard status
are UNKNOWN/null in the actual basicinfo/snapshot facts route where unsupported.
Exercise style is explicitly AMERICAN in the synthetic snapshot. The chain's
`option_settlement_mode: "N/A"` is unknown and does not establish cash/physical
settlement or a cutoff. The generator does not inject extra SDK-only fields to
fabricate complete facts. All fact scenarios retain `compatibility_verified=false`.

The catalogue contains auto-subscribing quote/book tools because the real
market-read profile permits them; this handoff never invokes them. It contains no
account/order/fee/unlock tools. Consumers can allowlist the four named calls
without treating the profile or catalogue as proof of provider entitlements.

## Verification and regeneration

Ordinary offline tests require **no historical Git objects**, no fetch-depth
change and no external endpoint. They verify committed pinned manifest hashes
against current runtime bytes and serialization packages, then reproduce all
wire responses/schemas through the real registered handlers. A dedicated test
makes all Git access fail and proves replay still works. Another verifies the
manual historical check fails explicitly when its object is unavailable.

```sh
.venv/bin/pytest -q tests/test_broker_fixture_handoff.py tests/test_delivery_schema.py
.venv/bin/ruff check tests/broker_fixture_handoff.py tests/test_broker_fixture_handoff.py
.venv/bin/basedpyright
```

Manual regeneration additionally requires the exact historical object already
available locally and verifies source/dependency hashes against it before writing:

```sh
.venv/bin/python -m tests.broker_fixture_handoff
```

The generator never fetches, silently repins to current HEAD or changes runtime
code/dependencies/workflows. If runtime bytes or serialization package versions
differ, stop and explicitly review the intended source fixture version. SDK
constructors, service startup and socket connections are blocked during replay;
only the four closed broker-double read methods can be invoked. Each JSON is
under 128 KiB and the whole JSON bundle is under 256 KiB.

Live WIS-25/WIS-26/WIS-32 and private fee-reconciliation gates remain separate.
This bundle unblocks offline adapter/snapshot evolution, not live acceptance.

## Current runtime fixture pin

The replay generator now targets `e5488ed9d84cdf4ac3372f82335fa29e96be5964/`,
which pins the preparation/admission account-resolution refactor and all 55
runtime source files. Its 14 wire/catalogue JSON files are byte-identical to the
prior `ba005ff7ffb3c32a3366c2b4861a9c71ae79951f/` set. Both
`2c019d50238d636deea5e49d3b48eac7868ff69b/` and
`ba005ff7ffb3c32a3366c2b4861a9c71ae79951f/` are preserved unchanged for consumers.
Each manifest keeps its own exact source commit/tree, runtime hashes, package
versions and payload hashes; no old artifact is relabelled.
The generator remains synthetic and denies SDK context construction, sockets,
private account access and services. No image metadata is injected into its
source-only catalogue; unknown identity is preserved until an actual image build.
