# Lab scanner broker contract (WIS-32, WIS-26, WIS-33)

This is a repository/source contract, not evidence of a deployed private
connection, quote rights, or real-client acceptance. The Lab owns comparison,
selection, per-scan limits and explicit fallback decisions; the broker owns
observed facts, scope enforcement and process-wide request budgets.

## Scoped profile and schema verification

Configure the distinct **restricted** bearer with
`MCP_RESTRICTED_PERMISSIONS=market_read`. Do not give the Lab the primary or
operator credential. The restricted default includes account/paper permissions;
it is **not** the scanner profile. `LAB_SCANNER_GRANTS` is the equivalent Python
profile, with no account grants. Transport credentials and live captures must
stay private; none belong in public fixtures.

`moomoo_mcp.catalogue_capture.verify_lab_scanner_catalogue` requires the exact
21-tool list in `LAB_SCANNER_TOOLS`, validates core inputs and safety hints, and optionally checks
the approved full-schema SHA-256. The set is independent of authorization's
classification inventory so future tools fail acceptance until reviewed.
It includes expiry/chain discovery, snapshot evidence, contract facts, health,
historical/other market reads, subscriptions and user watchlist reads. It does
**not** include accounts, orders, previews, preparation, executions, mutations,
unlock/lock or unsubscribe. `get_stock_quote` and `get_order_book` have honest
non-read-only hints because they can subscribe the shared quote connection;
`market_read` does not guarantee zero subscription side effects. The core
four-read scanner route needs neither of those tools.

The existing bounded loopback catalogue CLI can verify a served capture:

```bash
uv run python -m scripts.mcp_catalogue \
  --authorization-file /private/path/scanner-authorization-header \
  --output /private/path/new-scanner-catalogue.json \
  --profile lab-scanner
```

Supply `--expected-tools-sha256 <approved capture hash>` to reject schema drift,
not just name/hint drift. Keep captures private. The command makes only
`initialize` and paginated `tools/list` requests, never `tools/call`, and refuses
redirects/overwrites through the existing transport. A successful catalogue check
leaves `authorization_profile_verified`, `execution_authorization_verified` and
`live_connection_verified` false; discovery alone cannot prove any of them.

`tests/test_lab_scanner_profile.py` checks the authenticated HTTP catalogue and
direct denials for **every** registered non-profile tool in REAL and SIMULATE,
before schema validation/service/journal/SDK work. Distinct registered MCP
sessions share one process lifespan/dispatcher: ordinary reads and chains share
the 8-worker/32-pending admission policy, 15s end-to-end deadline and upstream
quota pools. New sessions, omitted filters or an explicit fallback cannot reset
these budgets. Structured failures include `CLIENT_PERMISSION_DENIED`,
`CONNECTION_UNAVAILABLE`, `CAPABILITY_UNAVAILABLE`, `QUOTE_PERMISSION_DENIED`,
`READ_DEADLINE_EXCEEDED`, `READ_OVERLOADED` and `RATE_LIMITED`. Failed calls are
not successful empty scans. Timeout/cancellation does not abort an OpenD thread;
late results are discarded and its worker slot remains occupied until exit.

## Comparable contract facts audit

Every named comparison field has `state: KNOWN | UNKNOWN | CONFLICT`, `value`
(null for unknown/conflict), and an `evidence` list. Each usable observation
retains `source_method`, `source_field`, normalized `value`, `raw_value` and UTC
`observed_at`. Conflicts never choose a source by precedence. All supplied
aliases participate, so conflicting strike aliases are also explicit.

| Fact | Provider column(s) | Limits / no inference |
| --- | --- | --- |
| `is_option` | `option_valid` | Requires a boolean; DRVT or a symbol is insufficient. |
| `option_type` | `option_type` | Observed CALL/PUT, not parsed from a code. |
| `underlying` | `stock_owner` | Exact observed identifier. |
| `expiry` | `strike_time` | Valid provider date, not a UTC exercise cutoff. |
| `strike` | `option_strike_price`, `strike_price` | Positive finite numeric; snapshot and chain aliases. |
| `classification` | `stock_type` | DRVT alone does not prove an option. |
| `lot_size` | `lot_size` | Trading lot only; not a deliverable or multiplier. |
| `currency` | `currency` | Unknown if omitted; never derived from market or reporting currency. |
| `exercise_style` | `option_area_type` | Observed style only. |
| `settlement_mode` | `option_settlement_mode` | Preserve AM/PM or supplied provider value; not an inferred cutoff. |
| `settlement_cutoff` | `settlement_cutoff` | Requires an explicit observation; no calendar/timezone assumption. |
| `deliverable_size` | `option_contract_size` | Separate from lot and premium multiplier; not complete components. |
| `deliverable_description` | `deliverable_description` | Only supplied description; not constructed from size/standard status. |
| `monetary_multiplier` | `option_contract_multiplier` | Never substitute lot, deliverable size, zero or hardcoded 100. |
| `standard_status` | `option_standard_type` | Observed status; no compatibility certification. |

Missing placeholders (including padded N/A, UNKNOWN, null, non-finite values),
invalid types/dates and unusable numeric zeros remain UNKNOWN. A boolean false
option flag is known false, not missing. Numeric strings and enum case/spacing
are normalized while retaining their raw observation. Non-100 or adjusted facts
are not errors by themselves: 1-lot, 150-deliverable, 25-multiplier can all be
known and distinct. Differing usable observations are CONFLICT, not silently
coerced to standard 100-share contracts. `compatibility_verified` stays false.

Basic-info and snapshot fields vary by SDK/provider. The pinned SDK generally
omits currency, settlement cutoff and complete deliverable components from these
routes. A complete source/identifier response does not establish complete facts
or make two spreads economically comparable. Consumers must reject
unknown/conflicting required fields; a prose description or numeric deliverable
size alone cannot prove complete deliverable components.

Source audit of the pinned SDK's `quote/quote_query.py` unpackers:

- `StockBasicInfoQuery`: classification, lot, underlying, option type, expiry,
  **`strike_price`** (not snapshot's `option_strike_price`). This alias now
  participates even when the snapshot omits or zeros its strike.
- `MarketSnapshotQuery`: option flag, underlying, expiry, strike, exercise style,
  deliverable size and premium multiplier (with explicit N/A/zero handling).
- `OptionChainQuery`: basic reference fields plus standard status and settlement
  mode. None of these three unpackers establishes currency, settlement cutoff,
  or complete deliverable components. Tests with those extra columns prove honest
  merging if a provider supplies them, not that the pinned SDK provides them.

### Observed chain enrichment, without additional requests

Successful, bounded, exact-code chain discoveries retain **only reference
columns** in the shared InstrumentAdapter. Later `get_instrument_facts` calls
can merge them with basic-info and a fresh snapshot. This can supplement
`strike_price`, `option_standard_type`, `option_settlement_mode`, `strike_time`,
`option_type`, `stock_owner` and `lot_size` only when actually returned. It does
not invent `option_valid`, currency, exercise/cutoff or deliverable details.

The chain cache has the same configurable TTL/capacity as static metadata
(defaults: 300s / 2048 exact codes), is cleared on quote-context replacement,
and is disabled by a zero metadata TTL. Latest successful discovery replaces a
code's previous chain observation; simultaneous sources still conflict rather
than override one another. Observation times are retained on cache hits. Greeks,
quotes and filter requests are not cached. Failed, malformed, oversized,
cancelled or late SDK responses cannot publish chain observations. Fact reads
never issue chain calls, cannot accept client-supplied "broker facts", and the
trading valuation path does not use this observational enrichment. Field
provenance, not request ordering or source coverage, determines what is known.

## Discovery and explicit basic fallback

`get_option_expiration_date` retains its legacy list shape. Choose an observed
expiry, then pass it as both `start` and `end` to `get_option_chain`. Exact
returned contract codes (including adjusted/case-sensitive codes) must be reused
verbatim. Do not synthesize codes.

`get_option_chain(..., include_evidence=True)` returns `DataEvidence` under the
existing MCP `structuredContent.result` wrapper. It includes normalized applied
code/type/moneyness inputs, explicit selected `data_filter` inputs, source/receipt
times, identifier coverage, `errors`, `limitations`, and **WIS-25 `timing`**
(queue/admission/quota/SDK/processing/total plus SDK-call counts). The legacy
default stays a list. IV/Greek provider precision limitations still apply; filter
inputs are not freshness, liquidity, entitlement or full-universe evidence.

Empty data is a successful zero-match result within the scope, not a rights or
transport failure. Rows with partial/null metadata remain present and must not
be promoted to complete contracts. `provider_history_complete` remains null.
`complete_response` refers only to the bounded returned rows, not the whole
provider universe. Exactly 2000 rows are supported; more than `MAX_DATA_ROWS`
or 2 MiB fails with `SNAPSHOT_LIMIT` rather than silently truncating. Missing,
invalid or duplicate contract identifiers fail explicitly.

Unsupported requested optional filters fail with `CAPABILITY_UNAVAILABLE` and
no automatic retry. A caller may **explicitly** omit `data_filter` for a basic
single-expiry scan (including when the SDK lacks optional filter support), under
the same quota/deadline/pending budget. Permission/timeout failures never trigger
an implicit unfiltered retry. The Lab still owns its overall scan deadline,
universe/expiry bounds and selection policy.

## Remaining integration acceptance

Offline tests/fixtures do not verify private routing, deployed identity/schema,
actual Lab authentication/reconnect behavior, quote entitlements or applicable
data-use rights. Capture the deployed profile privately, compare its full schema
hash, verify actual requested-client denial and bounded read diagnostics, and
obtain complete contract/deliverable and quote semantics before declaring a
connected comparison usable. No live broker acceptance is claimed here.
