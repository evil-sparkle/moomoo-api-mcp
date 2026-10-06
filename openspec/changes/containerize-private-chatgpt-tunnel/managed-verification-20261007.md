# Managed official-client revision — 2026-10-07

The owner authorized updating requirements and implementation to use the
unmodified official client, ending the fork experiment. This report separates
managed-runtime acceptance from direct-client characterization. Historical
findings remain valid; this revision does not repair upstream behavior.

## Tested inputs

- Branch: `feat/containerize-private-chatgpt-tunnel`; draft PR #38.
- Base before this revision: `d16ca4e9a0a0de6f472e46e0335108fed70f1bb8`.
- Tested implementation commit: `c41345b3cdf9697c94463d6e63c2ecdef4bcc9cf`.
- Hosted PR checkout/image revision:
  `2748baddd6746bae575fb2169b7d4cf4a86ad948` (merge ref for that head).
- Official release: v0.0.14, source/tag commit
  `0f870e50a973fa820d4c409000059e181e8d242b`.
- Official archive SHA-256:
  `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`.
- Binary SHA-256, measured inside the local test image:
  `472eb9dd9dd625b4e6023c3b4a5736b3a2e5a1b6dbe9338e001887a64ec992a6`.
- Actual binary version output:
  `0.0.14+0f870e50a973fa820d4c409000059e181e8d242b (git sha: 0f870e50a973fa820d4c409000059e181e8d242b)`.
- Local test image: `moomoo-chatgpt-tunnel:managed-review-20261007`.
  Docker image inspection ID:
  `sha256:292e35ca30304eabf9df3d8bc20d538f08c497b4c59893af839848cf15cd3176`.
- Allowlisted build-input SHA-256:
  `ea2f41f12c45444ca7f2f142dfdd2b5482da3351ec1455cb4b19743447805aca`.
- Local image label: `org.moomoo.tunnel.acceptance-profile=managed-official-client`.
  Its revision label is the base commit above because it was built from the
  working tree before committing. This is not a clean final-commit build claim.
  Official binary/runtime/config inputs are unchanged; the profile label changed.
- Broker fixture image: `moomoo-smoke-moomoo-mcp:latest`, reused locally.
  Hosted CI builds fresh broker and tunnel images from its tested checkout.
- Local Docker context: rootless. No rootful daemon is available on this VPS;
  rootful verification remains a required hosted job.
- Local Docker Engine 29.8.1; Compose 5.5.1.
- Fresh hosted rootful tunnel image:
  `sha256:2d02171aa93832e8b84a94c6eacfbb80b04c273149c7a313292888a6fc5ec48a`.
- Fresh hosted rootless tunnel image:
  `sha256:21e9a50b94c55c70d48adaca6a4e43e19e53fb48dce9d689c987d83deb1c208d`.
  Hosted IDs are taken from the actual build logs for the tested PR head;
  each job built both images fresh rather than reusing the local broker image.

The existing manifest, installer, official configuration/runtime, original
redirect reproduction, 515-case matrix fixture and historical evidence files
remain unchanged. Local image construction reused previously integrity-verified
official build layers; hosted CI also runs the installer explicitly.

## Implemented acceptance

Requirements now trust the fixed genuine OpenAI HTTPS endpoint with certificate
verification, the approved MCP destination and the controlled Docker host.
Managed startup preserves a scrubbed environment, protected credential mounts,
authenticated MCP and server-enforced READ_ONLY. Deployment selection remains
explicit and immutable; invalid metadata, empty authentication, non-READ_ONLY
mode and active/enabled/unknown legacy states prevent startup.

The unconditional `RELEASE_GATE_PASSED=False` refusal is replaced by those
operational checks. `check-release` remains an alias for existing scripts.
The optional Compose service, network topology, image pin and brokerage controls
are unchanged. Default deployment remains tunnel-free.

The CI diagnostic reporter checks original runner exits, complete case inventory,
valid verdicts, fixture errors and summary consistency. Genuine upstream FAIL
and INCONCLUSIVE results remain visible in job summaries and artifacts. Report
validation passing does not mean those security tests pass. Real managed-image
integration in rootful/rootless contexts remains required.

## Verification status

| Check | Result |
| --- | --- |
| Ruff lint and formatting | PASS; 100 files already formatted |
| Changed-file basedpyright | PASS; zero errors/warnings/notes |
| Full basedpyright | PASS; zero errors/warnings/notes |
| Full pytest | 1352 passed, 2 subprocess timeout failures, 1 skipped, 81 subtests passed; isolated rerun: shutdown PASS, journal restart still times out |
| Strict OpenSpec validation | PASS; 27 passed, 0 failed |
| Workflow YAML and shell syntax | PASS |
| Rootless actual-image integration | PASS; completed with exit 0, including cleanup |
| Staged gitleaks hook | PASS |
| Hosted full quality job | PASS; 1354 tests passed, 1 skipped, 30 warnings; lint/100-file formatting/types passed |
| Hosted general CI | PASS; quality, strict OpenSpec, broker image build and container smoke |
| Fresh hosted rootful/rootless integration | PASS in both contexts |
| Hosted direct-client characterization | Valid report; 361 PASS / 154 FAIL / 0 INCONCLUSIVE across 515 cases; original redirects 0 PASS / 2 FAIL |

Local actual-image command:

```sh
uv run --frozen --all-extras python tests/fixtures/tunnel_container_checks.py \
  --docker-context rootless \
  --broker-image moomoo-smoke-moomoo-mcp:latest \
  --tunnel-image moomoo-chatgpt-tunnel:managed-review-20261007
```

The local harness initially could not mount a public fixture from this checkout
for its non-root control-plane container because the source file was mode 0600.
It now copies only that public Python fixture to disposable storage with mode
0644, as it already does for other public fixture inputs. Credential sources and
their permissions are unchanged. The clean rerun is recorded separately.

The clean rootless run passed authenticated forwarding, missing/wrong/conflicting
authentication, Host/Origin rejection, proxy poisoning resistance, READ_ONLY
startup, measured secret permissions, OpenD isolation, both credential rotations,
stale-inode/recreation behavior, preserved journal/OpenD identities, independent
restart, live DNS recovery, outage recovery, SIGINT, scoped disablement, public
credential-free DNS/TLS probes and bounded startup refusals/deadline exhaustion.

During development, an invalid-selection diagnostic assertion and a missing
type guard were corrected. One wrapper subprocess exceeded its 10-second test
timeout under resource contention; disposable wrapper tests now allow 30 seconds.
An earlier full suite was interrupted while reducing memory contention and is
not a completed test run. Final completed results replace the pending rows above.

The completed full suite took 530.80 seconds while local image/type checks were
also running. Its only failures were existing subprocess deadlines (20 and 10
seconds) in the stuck-connection shutdown and outcome-persistence restart tests.
Their isolated rerun after the heavy checks finished produced 1 PASS (shutdown)
and 1 FAIL (the journal restart child still exceeded 10 seconds), in 26.13 seconds.
Their source/timeouts are unchanged. All revised tunnel/reporting tests passed
in the full run. Hosted full-suite verification remains required; local timeout
failures are not represented as a passing full suite.

The [hosted quality job](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378611/job/112381927434)
on the tested implementation commit passed all 1354 tests in 62.25 seconds, with
1 skipped and 30 warnings. Its Ruff lint/100-file formatting and full types also
passed. This independently covers both locally timed-out tests without modifying
their existing deadlines or omitting them.

[General CI](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378611)
completed successfully. The [managed tunnel workflow](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378697)
also completed successfully: both fresh actual-image jobs
([rootful](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378697/job/112381866630),
[rootless](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378697/job/112381866154))
passed their full harness, including bounded startup deadline exhaustion and
cleanup. All hosted results above refer to implementation head `c41345b`.
Subsequent evidence/spec wording/task-status edits change no runtime, client,
workflow or test inputs; newly triggered runs must retain their own status.

## Preserved diagnostic findings

Historical direct-client matrix: **361 PASS / 154 FAIL / 0 INCONCLUSIVE**, all
515 cases, zero fixture errors. The original two redirect reproductions failed.
See `synthetic-compatibility-20261002.md` and
`synthetic-matrix-results-20261002.json` for those exact runs and per-case results.
Local reporter validation against those recorded matrix rows preserved their
verdicts; that validation was not a fresh execution of the full official binary
matrix. The [fresh hosted diagnostic job](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/37496378697/job/112381865901)
on the tested implementation commit executed both unchanged original runners.
Its downloaded sanitized artifact confirms the same 361 PASS / 154 FAIL /
0 INCONCLUSIVE across all 515 cases and two original redirect FAILs. Both runner
exit codes remain 1. Complete inventory, zero fixture errors and consistent
summaries passed report validation; no security verdict was relabeled.

Conditional upstream redirect, inherited-proxy and doctor-authentication findings
are accepted for the fixed managed scope. Manager proxy filtering does not repair
redirect behavior. No malicious redirect from the real OpenAI endpoint or actual
credential exposure has been established.

## Separate milestones

VPS production migration, real OpenAI authentication, ChatGPT web access and
native iPad access remain **PENDING**. Only synthetic credentials and disposable
resources were used. No production selection/start, trading, merge or draft
promotion is performed by this revision.
