# Continuation verification — 2026-09-30

Tasks **1.2 and 8.3 remain incomplete**. All credentials and broker/journal records
used here are synthetic. No live OpenAI endpoint, real credential source, VPS
configuration, trading mode, firewall or Tailscale setting was changed.

## Branch and hosted evidence

- `4eb7b47` integrated current main `d09217b`. Main contained the original approved
  proposal; conflicts were resolved in favor of the subsequently approved planning
  revision and accumulated evidence. The PR became mergeable and stayed draft.
- `4f57ae1` added actual journal/volume identity and scoped-disable evidence,
  credential-free outbound checks, and fresh-image-source CI mode.
- `89cac04` corrected two **observed hosted fixture failures**: missing rootless
  tooling/package repository, and Docker 28 requiring an explicit test-only subnet
  for the old-IP holder. Existing networks are inspected to choose an unused
  fixture subnet; production still uses service DNS without fixed container IPs.
- [General CI at 89cac04](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36652723276):
  **SUCCESS** (tests/lint/types, OpenSpec, fresh image build, container smoke).
- [Tunnel CI at 89cac04](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36652723262):
  **FAILURE at the required security gate**. Independent rootful and rootless
  container jobs both **SUCCESS**, with newly built brokerage source used directly
  from the image rather than an injected source-tree replacement.
- `ebd1851` adds the separate expanded official-client matrix.
  [Hosted run](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36653256572)
  completed its expanded matrix: **62 cases: 48 PASS, 13 FAIL, 1 INCONCLUSIVE**.
  The earlier result is retained here as history; [per-case results](compatibility-matrix-results.json) contain the corrected run.
  The inconclusive conflicting-header case exposed a fixture issue: Python's
  single-header getter accepted the first of duplicate Authorization values.
  The real MCP server already rejects duplicates (covered by actual container
  tests). The fixture now rejects duplicates too. At `bbd33f6`, the corrected
  [hosted matrix](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36653940564)
  reports **62 cases: 49 PASS, 13 FAIL, zero fixture errors**, including PASS for
  missing/wrong/conflicting bearer refusal. The per-case JSON now records this
  corrected run with its exact source head.

The earlier OAuth workflow-scope rejection is resolved. There is no current
GitHub-permission or main-branch conflict blocker. No workflow file was omitted.

## Independent runtime coverage now observed

Both hosted Docker contexts pass actual-image permissions, protected master/staged
files, no root runtime, no published tunnel port, positive OpenD loopback and
negative bridge access, private admin listener, authenticated MCP over DNS,
Host/Origin/auth failures, both real credential rotations, delayed startup and
fatal refusal, bounded timeout, signals/hang/restart, upstream outage recovery,
and MCP recreation at a changed IP. They preserve an actual ExecutionStore row,
OpenD marker and named-volume identities. The real disable helper removes only
the tunnel and its non-secret selection metadata, leaving authenticated local MCP
and both persistent stores intact. Systemd migration/exclusivity is simulated;
no real host service is started or stopped.

Outbound evidence uses each image's numeric runtime identity on the normal bridge
for a verified TLS handshake to example.com. Those probes run no tunnel client,
mount no credentials, and do not contact OpenAI. All credential-bearing fixtures
remain on an internal bridge or a `--network none` loopback-only container.

A subsequent synthetic regression proves a future release-approved direct wrapper
start checks READ_ONLY before any Docker start action. The real production release
constant remains false. Focused deployment/selection tests: **52 passed,
12 subtests passed, 1 warning**. Added fixture type checks: **0 errors/warnings/notes**.

## Compatibility evidence and limits

The unmodified verified v0.0.14 binary remains the only production pin. The original
CP1/CP2 reproduction is byte-for-byte unchanged and still fails. Additional
fixtures test distinct runtime and ordinary bearer values, per-endpoint redirects,
301/302/303/307/308, subdomains, port changes, multihop, synthetic-CA HTTPS/downgrade,
normal authenticated traffic, revoked/wrong keys and MCP auth conflicts.

Development runs observed additional runtime-key confinement failures, and direct
client MCP-bearer diversion through inherited proxy configuration. These are
**separate credential paths**. The production manager strips inherited proxies;
component tests cover that filter and a new actual-entrypoint canary test checks
it separately. A direct-client proxy failure is not described as an observed leak
through the manager. No real credential exposure or behavior of the actual OpenAI
HTTPS endpoint is inferred.

Successful MCP redirect cases mean only that the named redirect was exercised and
no protected header reached its sink. An independent authenticated READ_ONLY health
positive control must pass. Each observed FAIL remains a failure; missing endpoints,
methods, proxy-variable permutations or redirect-chain members stay untested rather
than inheriting a group PASS. The full observed per-case results are recorded in `compatibility-matrix-results.json`. Fixture config/framing issues discovered during development were
fixed; early inconclusive attempts do not count as compatibility evidence.

## Remaining release conditions

Task 1.2 requires the entire mandatory matrix to pass on a reviewed official
artifact. An upstream candidate alone is insufficient; no pin change is authorized.
Task 8.3 also requires explicit release review/authorization. The draft PR and
production refusal remain in place regardless of the independent green jobs.
Live OpenAI, real account/positions, VPS, ChatGPT web and iPad remain PENDING.

## Latest tested code head

At `bbd33f6df6d63ac9b51ccd2cdeb1e102e9219b96`,
[general CI](https://github.com/evil-sparkle/moomoo-api-mcp/actions/runs/36653940502)
passed: **1336 pytest tests passed, 1 skipped, 29 warnings** (60.56 seconds),
Ruff lint/98-file formatting, **0 type errors/warnings/notes**, strict OpenSpec,
fresh brokerage build and container smoke. The original confinement job failed;
the expanded matrix has the 49/13 result above. Documentation-only descendants
retain this exact tested code provenance; they do not reclassify the failed gates.

Both container jobs at `bbd33f6` also **PASSED**. Their logs confirm that the
reachable proxy canary received only its deliberate unauthenticated positive-control
request, while the poisoned manager environment could not divert preflight,
discovery or official-client forwarding traffic. This is separate from the direct
client proxy FAIL cases. Both jobs again passed the actual scoped disable, journal
and OpenD preservation, outbound checks and bounded startup deadline.

Untested release-gate members remain explicit: same-origin/loop redirect cases,
exhaustive endpoint/method/body combinations, isolated proxy-variable permutations,
missing runtime-key cases and comprehensive authentication/retry ambiguities. No
per-case PASS is promoted to a group PASS across unexecuted combinations. Official
doctor's unauthenticated OAuth probe remains failed. No upstream remediation is
shipped; the private report is prepared for owner review only, outside tracked files.
