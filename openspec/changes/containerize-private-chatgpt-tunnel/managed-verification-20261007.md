# Managed official-client revision — 2026-10-07

The owner authorized updating requirements and implementation to use the
unmodified official client, ending the fork experiment. This report separates
managed-runtime acceptance from direct-client characterization. Historical
findings remain valid; this revision does not repair upstream behavior.

## Tested inputs

- Branch: `feat/containerize-private-chatgpt-tunnel`; draft PR #38.
- Base before this revision: `d16ca4e9a0a0de6f472e46e0335108fed70f1bb8`.
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
| Fresh hosted rootful/rootless integration | PENDING publication |
| Hosted direct-client characterization | PENDING publication |

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

## Preserved diagnostic findings

Historical direct-client matrix: **361 PASS / 154 FAIL / 0 INCONCLUSIVE**, all
515 cases, zero fixture errors. The original two redirect reproductions failed.
See `synthetic-compatibility-20261002.md` and
`synthetic-matrix-results-20261002.json` for those exact runs and per-case results.
Local reporter validation against those recorded matrix rows preserved their
verdicts; that validation is not a fresh execution of the full official binary
matrix. Newly published CI executes the unchanged original runners again.

Conditional upstream redirect, inherited-proxy and doctor-authentication findings
are accepted for the fixed managed scope. Manager proxy filtering does not repair
redirect behavior. No malicious redirect from the real OpenAI endpoint or actual
credential exposure has been established.

## Separate milestones

VPS production migration, real OpenAI authentication, ChatGPT web access and
native iPad access remain **PENDING**. Only synthetic credentials and disposable
resources were used. No production selection/start, trading, merge or draft
promotion is performed by this revision.
