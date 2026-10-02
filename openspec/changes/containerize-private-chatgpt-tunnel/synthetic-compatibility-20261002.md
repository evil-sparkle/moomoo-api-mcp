# Synthetic compatibility completion — 2026-10-02

**Task 1.2 remains BLOCKED; task 8.3 remains incomplete.** The production pin,
release refusal and default-off selection are unchanged. PR #38 remains draft.
No live credentials, OpenAI traffic, VPS deployment, trading or product acceptance
are part of this evidence. The private upstream report is outside tracked files
and has not been submitted.

## Explicit case inventory

The fixture now enumerates **515 distinct cases**, rather than treating broad
matrix labels as evidence. Each prints its own verdict and sanitized counters.

| Cases | Coverage |
| --- | --- |
| 62 | Earlier expanded cases, retained with stronger per-method/body observations |
| 360 | Nine paths × eight redirect variants × 301/302/303/307/308 |
| 48 | Individual uppercase/lowercase HTTP/HTTPS/ALL/NO proxy variables; separate control-plane, MCP startup, discovery and actual forwarding |
| 3 | Omitted runtime-key setting, empty file and missing file |
| 28 | Per-path 401/403 refusal and one-shot 429/503 responses; authenticated retry observations |
| 6 | Duplicate-valid, wrong-first, comma-joined, empty, Basic and mixed-case forwarded Authorization |
| 4 | Separate missing/wrong static discovery and runtime MCP credentials |
| 4 | Explicit OAuth discovery, tools/list, doctor authentication and lost forwarded-response behavior |

The nine redirect paths are metadata GET, poll GET, response POST, MCP discovery
GET, generic startup POST probes, explicit initialize POST, tools/list POST,
initialized notification POST, and forwarded check_health POST. Generic startup
probes remain in the inventory alongside explicit initialization. The eight variants are changed host, changed port,
subdomain, same-origin, multihop, loop, synthetic-CA HTTPS and HTTPS downgrade.
Counters identify actual method/body categories, redirect exercise, sink contact,
protected-header/body diversion, authentication refusal, read dispatches and
returned results. A path that was never exercised cannot inherit a group PASS.

Same-origin redirects are separately checked for authentication loss; a request
that reaches the approved origin without its required bearer is a failure even
when no cross-origin leak occurs. Loop cases observe the client's per-chain hop
limit; this does not assert that a daemon never starts another polling attempt.
Retry verdicts check authenticated refusal/retry behavior in a bounded observation
window. The counters distinguish retry, recovery and refusal; a PASS does not
promise automatic recovery from every HTTP error. Lost-response coverage uses
only a synthetic read operation and checks dispatch count rather than replaying
a broker write.

This is a finite matrix for the configured Streamable HTTP integration, not a
claim to cover every possible HTTP payload or optional tunnel-client backend.
The private source-review candidate also calls out secondary control-plane clients.
Those review obligations cannot be discharged by the fixture inventory alone.

## Isolation and provenance

The matrix uses the **real official binary** against standard-library control-
plane and MCP protocol fixtures. Actual brokerage FastMCP/supervisor, secrets,
rotation and network behavior are covered by the separate rootful/rootless
container jobs. Neither fixture type claims live OpenAI or broker acceptance.

The tested binary matches the executable inside the digest-verified v0.0.14
archive: SHA-256 `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`;
reported source/version `0.0.14+0f870e50a973fa820d4c409000059e181e8d242b`.
The same pinned Python image as the earlier matrix runs under numeric UID/GID
10002, read-only root, dropped capabilities, no-new-privileges and 32 MiB tmpfs.
`--network none` plus fixture-only loopback host aliases prevents real egress.
Six workers use independent ephemeral ports, configs and generated credentials.

`tests/fixtures/tunnel_compatibility_matrix.py` supports `--case` and `--match`
for reproduction. The CI workflow supplies the exact isolated invocation and a
30-minute job deadline. It returns nonzero for FAIL or INCONCLUSIVE; no xfail,
continue-on-error, stub replacement or aggregate green result clears the gate.
Child output, raw headers/bodies and generated credentials are never printed.

Proxy sinks emulate responses themselves and never forward network traffic.
Raw-client inherited-proxy characterization is distinct from the production
manager's successful environment-scrubbing canary tests. An anonymous HTTPS
CONNECT that is refused does not prove exposure of encrypted Authorization or
successful operation through a proxy. Independently authenticated HTTP/HTTPS
positive controls remain mandatory.

The preserved historical reproduction, `compatibility.md`, `verification.md`,
archived PR #36 artifacts and production release manifest are unchanged.

## Results

Final fixture code: `5790462d06eb419612b3f2a7e2ef12c0739fcd80`.
Fixture SHA-256: `c3186af31be7630c5ef8c553b69208b37a75fb343ee36866b432fcd0763068eb`.

[General CI](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36948401319)
**PASSED**: 1336 tests passed, 1 skipped, 30 warnings (58.51 seconds); Ruff lint,
98-file formatting, 0 type errors/warnings/notes, strict OpenSpec, fresh image build
and container smoke.

[The tunnel workflow](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36948401274)
**FAILED at both mandatory security jobs**, with independent rootful and rootless
container jobs **PASSED** on fresh images. The unchanged historical reproduction
still demonstrates both original runtime-key redirect failures. The expanded
matrix completed **515 cases: 361 PASS, 154 FAIL, zero INCONCLUSIVE and zero
fixture errors**. Its emitted case-ID set exactly matches the final inventory;
no missing case is hidden by the aggregate.

[Every case and its counters](synthetic-matrix-results-20261002.json) includes the
exact head/run, fixture hash, binary/archive and base-image provenance. Each row
retains its own result. The failure observations are:

| Observation | Cases | Credential/path distinction |
| --- | --- | --- |
| Runtime key reached an unapproved sink | 104 | OpenAI control-plane credential; redirect and direct-client proxy scenarios |
| Ordinary bearer reached a proxy sink | 7 | MCP credential; raw-client inherited HTTP proxy behavior |
| Required MCP authentication was omitted | 43 | Redirected requests or the separate doctor probe; authentication loss rather than cross-origin key leakage |
| Request body reached an unapproved sink | 19 | Overlaps the credential failures above; includes response POST 307/308 diversion |

These are scenario counts, not 154 distinct vulnerabilities. No runtime/MCP
credential crossover was observed in the fixtures. Authenticated OAuth discovery,
initialize, tools/list and READ_ONLY health controls pass. Missing/wrong credentials,
forwarded ambiguity, per-path authentication-retry observations and the single
lost-read-response dispatch check pass in their named cases.

Both actual-image jobs again passed permissions, distinct credential rotations,
manager proxy scrubbing, Host/Origin/auth and READ_ONLY startup, loopback OpenD
isolation, lifecycle/DNS recovery, journal/state preservation and scoped disable.
Their outbound probes carry no credentials. These passing jobs do not override
the failed official-client gate.

## Development results and corrections

- First hosted expanded run at `daa6154`: 471 cases, 315 PASS / 156 FAIL, zero
  inconclusive cases and fixture errors. Both rootful/rootless jobs passed.
- Two discovery-negative tests initially mistook a valid inherited runtime bearer
  for missing discovery authentication and mixed valid forwarding into discovery
  refusal. The corrected fixture removes both headers for genuine absence and
  explicitly requests OAuth discovery. It requires actual discovery requests and
  rejection with zero tool dispatches. These were fixture errors, not new client
  vulnerabilities, and the earlier two FAIL verdicts are superseded.
- Corrected hosted run at `16f6f83`: 471 cases, 317 PASS / 154 FAIL, zero
  inconclusive cases and fixture errors. Both container jobs passed.
- `9d596df` explicitly targets `initialize`: hosted 471-case result was 327 PASS /
  144 FAIL with zero fixture errors/inconclusive cases. All 44 affected cases also
  passed locally. Final `5790462` retains the 44 generic startup cases as well,
  expanding the inventory to 515 so earlier failed observations remain covered.
- The first full local run under memory pressure recorded 310 PASS / 156 FAIL /
  5 INCONCLUSIVE. All five inconclusive cases passed when re-exercised serially,
  with zero fixture errors. That partial run is not represented as a clean full
  run. Redundant local full-suite/type processes were interrupted to free memory;
  completed hosted full-suite/type evidence above is authoritative.
- Local fixture Ruff/type checks passed. Strict OpenSpec: 27 passed, 0 failed.

## Remaining gate

The enumerated synthetic work can be complete while task 1.2 remains failed.
The official runtime-key redirect/injection defect still requires reviewed
upstream remediation and passing evidence on the exact final official artifact.
Direct-client proxy and MCP authentication failures also retain their individual
verdicts; the manager's passing proxy filter does not relabel them. Source review,
final-image reruns for any future candidate and explicit owner authorization remain
mandatory. Task 8.3, production enablement and all live acceptance remain blocked.
The private report is prepared for review only and has not been transmitted.
