# MCP delivery reconciliation (WIS-32)

Source registration, a credential-visible endpoint schema, and a hosted
connector import are separate evidence layers. Do not add duplicate tools or
widen write permissions to repair an unexplained catalogue difference.

## Evidence collected 2026-10-26

Worker B inspected fetched `origin/main` at
`527ccde1551171a649339c11d24de0cdb278d564` and the selected remote hotfix at
`a5893883419af0f1453a74ca938c24fce6203a74`. The clean worker branch was
fast-forwarded from `c42741a35bea50da92ae2ab3c404fa695f04c551` to main.
No broker account/order reads or mutations were made for this investigation.

The GitHub repository variable `DEPLOY_BRANCH`, retrieved through the variables
API rather than a secret store, was `hotfix/security-emudhra-20261023`, updated
`2026-10-23T07:42:04Z`. This confirms release selection at observation time,
not the identity of any running process.

The selected hotfix's successful push CI was
[40153041353, attempt 1](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/40153041353).
Its public `moomoo-deployment-1/deployment.json` bound that exact source SHA to:

* Application digest: `sha256:85dd8d51feca0d322f6b579628f805f9e0e1163b2f7d56300163d664f9343086`.
* Tunnel digest: `sha256:dd3464b78e2462efe27103793280b04671db3231c529869fa227f2d9536d8da7`.

[Deployment controller run 40153314815](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/40153314815)
completed its release verification and Vultr deployment/verification jobs
successfully on October 25. Its workflow/controller head was
`a201ba37bd1a74a8daa5d49824388f9c135f18c9`, which must not be mistaken for
the application image's source revision. No current host/container inspection
was performed. These records do not attest that the connected hosted connector
still reaches that exact image.

At `2026-10-26T14:19:07Z`, the available connector's non-account health check
reported package `0.2.1`, contract `2026-10-22.1`, gateway value `1010`, source
revision `unknown`, and restricted grants `market_read`, `real_read`,
`simulate_read`, `simulate_write`. Gateway `1010` is not a verified full OpenD
build. Installed deployed SDK identity remains unknown. The local validation
environment used SDK `10.10.7008` and MCP `1.25.0`.

## Versioned image build provenance

New application image builds accept the public `SOURCE_REVISION` build argument.
CI supplies its full checked-out commit, matching the OCI revision-label input.
Before the non-editable package installation, the Dockerfile generates the fixed
package resource `moomoo_mcp/_build_identity.json` with exactly these fields:

```json
{"schema_version":1,"source_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

The example SHA is synthetic. Version 1 accepts only canonical lowercase full
40- or 64-character hexadecimal Git object IDs. Other types, abbreviated IDs,
uppercase, whitespace, additional fields and noncanonical resource encoding are
rejected. The resource contains no environment snapshot, timestamps, credentials,
repository paths, Git configuration, account data or broker responses. The host
resource path is excluded from the Docker context and Git; generation occurs
inside the build rather than copying a possibly stale checkout artifact.

Runtime reads only this bounded resource from its own installed package. It does
not inspect Git, environment variables, container labels or private files to
derive the identity. Setting a runtime `SOURCE_REVISION` or `GITHUB_SHA` cannot
override it. Builds without the argument, ordinary developer/wheel installs
without generated metadata, unreadable resources and invalid metadata retain
`source_revision: "unknown"` and `build_revision: null`.

With valid metadata, both existing health fields contain the embedded source SHA.
`build_revision` here identifies the declared source of that build, not a unique
build attempt, image digest or reproducibility guarantee. The existing health
keys and tools-schema calculation remain unchanged. SDK version still comes from
the installed SDK; OpenD/gateway version is independent.

An unchanged image reused under a newer release tag retains its original embedded
build revision. Compare that build, immutable image digest and selected release
manifest separately. Neither a new tag nor a documentation-only commit rewrites
an existing image's provenance.

This is build provenance, not cryptographic remote source attestation. A caller
must still verify the running image/endpoint, intended credential-visible schema
and connector import. It establishes no broker health, permission, entitlement,
quote freshness or financial reconciliation. The older observations above remain
dated evidence; this source change does not identify or update their deployment.

The synthetic broker handoff was rolled forward to runtime source commit
`ba005ff7ffb3c32a3366c2b4861a9c71ae79951f`, with all 55 committed runtime-file
hashes and its source tree pinned in the new manifest. The original `2c019d5`
artifact remains unchanged for existing consumers. All 14 wire/catalogue files
in the new set are byte-identical to the earlier artifact; the manifest records
the newly verified runtime source rather than relabelling the old source hash.
Local source generation has no embedded image resource, so its catalogue still
truthfully reports unknown build/source identity. It makes no network, gateway,
account, entitlement or quote-storage claim.

## Source, profiles, and imported definitions

`server.py` imports `trade_queries`, `trade_inspection`, and `trading`; their
decorated definitions are registered. `AgentFriendlyMCP.list_tools` filters
through `client_authorization.tool_visible`. The nine named WIS-32 reads are
account reads, visible with either `real_read` or `simulate_read`, and hidden
for market-only, gateway-only, write-only, and empty restricted grants. Schema
visibility does not prove that a particular environment supports the call.

| Selected tool | Existing implementation / provider boundary | Imported here |
| --- | --- | --- |
| `get_order` | `TradeQueryService`; current `order_list_query`, bounded historical `history_order_list_query` plus exact local matching | Absent |
| `get_order_fees` | `TradeQueryService` -> `order_fee_query` | Absent |
| `get_orders_page` | `TradeQueryService` -> `order_list_query` on initial fetch | Absent |
| `get_deals_page` | `TradeQueryService` -> `deal_list_query` on initial fetch | Absent |
| `get_history_orders_page` | `TradeQueryService` -> `history_order_list_query` on initial fetch | Absent |
| `get_history_deals_page` | `TradeQueryService` -> `history_deal_list_query` on initial fetch | Absent |
| `preview_order` | `TradeService` -> validated preview/capacity reads; no order admission | Absent |
| `get_trading_capabilities` | `TradeService` -> account/instrument facts and advisory readiness | Absent |
| `get_trade_events` | `TradeService` -> bounded process-local callback ring after account resolution | Absent |

Pages are bounded immutable local snapshots; continuation does not query the
SDK or establish full broker history. Events are ephemeral observations, not
a durable ledger or recovery authority. See [trading contracts](trading-contracts.md).
`preview_order` retains its legacy unstructured output; the other selected
reads publish output schemas. All nine have read-only annotations, which are
client hints rather than authorization.

Offline authenticated HTTP fixture tests confirm registration, schemas, safety
annotations, grants, and the exact wire catalogue/hash without calling services.
For the default restricted grants above, offline source fingerprints were:

| Evidence | Tools | SHA-256 |
| --- | ---: | --- |
| Main `527ccde` | 42 | `82143f8b1e5d44099b6f8f386f235cd1d0edb0292819c6405d94f156364f1674` |
| Hotfix `a589388` | 42 | `8dfc1b3196f691dbffe4c58a15882c3f0df7848d6766d2a460d5175b4153a7fb` |
| Connected health's claimed schema | Not directly retrieved | `8dfc1b3196f691dbffe4c58a15882c3f0df7848d6766d2a460d5175b4153a7fb` |

The imported tool catalogue contained 35 Moomoo definitions. Relative to the
modeled hotfix restricted schema it omitted the nine reads above plus
`prepare_trade_request` and `get_prepared_trade`; it additionally exposed
`lock_trade`, `unlock_trade`, `unsubscribe_market_data`, and
`acknowledge_recovery`. The first three are gateway-control tools intentionally
hidden from the modeled restricted profile. `acknowledge_recovery` is not a
registered MCP tool at either inspected source revision; operator recovery uses
a separate protected boundary. Do not invoke such imported definitions to test
visibility. Other imported definitions were compared by name, not by a complete
retrieved remote JSON Schema export.

The hotfix hash matches health, but no fresh authenticated **live endpoint**
`tools/list` was available through this worker's tools. Connector credentials
were neither requested nor accessed. Therefore stale import, connector
selection/filtering, endpoint/profile differences, and deployment drift remain
hypotheses. Ordinary source profile filtering alone does not explain the nine
missing reads for the grants reported by health. Smallest next remedy: obtain
the same-profile endpoint catalogue through operator-controlled diagnostics,
compare it with imported definitions, then refresh only the proven stale layer.
Do not treat health's hash as a retrieved catalogue or as source attestation.

## Local regression gate

Run with an existing compatible development environment:

```sh
python -m pytest tests/test_delivery_schema.py \
  tests/test_published_trading_contract.py tests/test_tool_annotations.py -q
ruff check tests/test_delivery_schema.py
ruff format --check tests/test_delivery_schema.py
```

The new check scans only Python tool modules using AST, detects orphaned and
duplicate declarations against the trusted registry, checks explicitly selected
query operations against the installed SDK, and compares local and HTTP
published definitions across eight synthetic grant profiles. It checks schema
hashes, read annotations, existing service interfaces, and zero service calls.
It does not require every upstream SDK method to become an MCP tool.

## Safe main-to-hotfix integration handoff

CI in `.github/workflows/ci.yml` runs on main, feature and hotfix pushes, main
PRs, and manual dispatch. Only the exact selected `DEPLOY_BRANCH` publishes
both images and its immutable release manifest. Main CI success while this
hotfix is selected does not publish main. A selected-branch **push** can trigger
automatic production deployment; manual CI dispatch does not activate that
push-only dispatcher. Publishing and deployment must be reported separately.

The merge base was `6e93f46fbed76c05a68d9a3f2cfcaf4d0fa7f7da`. Five commits
were unique to the hotfix: `fb27162` (CA trust/patched tunnel Go), `4da0820`
(managed pip/ensurepip trust), `d342a3e` (managed recovery regression), `780e4c8`
(OpenSSL refresh), and `a589388` (runtime-preserving legacy migration). Main
also contains a security change followed by its deliberate main-only revert;
blindly resolving a merge toward main can lose the temporary protection.

Parent should:

1. Fetch/recheck selected branch, SHAs, clean worktrees, and newly merged main.
   Preserve unrelated dirty work. Start an integration branch from the current
   hotfix; do not rewrite or reset the production branch.
2. Backport reviewed feature commits with provenance, excluding main's
   security/revert sequence and unrelated changes. An explicit merge is an
   alternative only with reviewed security resolutions. Preserve the CA policy,
   managed pip/ensurepip stores, OpenSSL runtime refresh, tunnel source rebuild,
   Docker build-context inputs, and legacy runtime/project/state migration.
3. Run exact-head functional/profile/schema tests plus hotfix CA, tunnel-build,
   image-input, recovery and native deployment regressions. Main-target PR CI
   is automatic; hotfix-target PR CI is not selected by the current PR filter.
   Dispatch validation on an unselected eligible branch when necessary and
   verify the tested SHA. A feature-branch/manual run checks without publication
   while the hotfix remains selected.
4. Parent reviews and merges only the authorized integration result. A push to
   the selected hotfix is a release trigger. Verify successful CI, attempt,
   source revision and both image digests from `deployment.json`; retain the
   prior image pair and rollback state. This worker does not merge or deploy.
5. After separately controlled rollout, retrieve live authenticated tools/list
   under the intended profile, canonicalize/hash the full definitions, compare
   health and release identity, then perform a non-account hosted-connector
   discovery smoke check. Refresh connector metadata only with operator approval.
   Leave actual order-fee/account reconciliation with WIS-29 and separate read
   authorization. Do not call orders, unlock, permissions, or account reads as
   substitutes for discovery.

See [deployment branch procedure](deployment-branches.md) for release selection
and return-to-main safeguards. WIS-32 remains open until direct endpoint and
hosted connector delivery, deployed SDK/full gateway identity, and running
release identity are evidenced; these local checks do not close those gates.
