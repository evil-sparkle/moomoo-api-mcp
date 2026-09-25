# Independent implementation evidence — release BLOCKED

This report supplements, and does not replace or edit, historical `compatibility.md`,
`verification.md`, or `scripts/check_tunnel_client_compatibility.py`.
Task **1.2 remains BLOCKED**; task **8.3 is not complete**. PR #38 remains draft.
The approved planning revision was committed/pushed first as `36f3f69`.

## Artifact provenance and limitations

- Official production pin remains **v0.0.14**, source
  `0f870e50a973fa820d4c409000059e181e8d242b`, archive SHA-256
  `15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3`.
- Dedicated builder/runtime base: `python:3.12.12-slim-bookworm` at
  `sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c`.
  This is a new dedicated image input, not a tunnel-client upgrade.
- Actual binary executes as UID/GID 10002 under a read-only root, no capabilities,
  no-new-privileges, and only `/run/moomoo-chatgpt-tunnel` writable via tmpfs.
  Rootless Docker 29.8.1 measured host UID/GID **110001:110001**, daemon owner 1001.
  The helper measures each selected context; those numbers are not configuration.
- The full new brokerage image build was canceled at export when the shared
  filesystem reached 99% usage. Local integration instead uses the existing
  brokerage image with an explicit read-only copy of current public source and
  the actual supervisor plus OpenD network stub. This does not prove a newly built
  brokerage artifact or a broker login. CI builds the current brokerage image.
- Rootful Docker is unavailable locally (`/var/run/docker.sock` absent).
  Rootful permission/integration evidence remains pending the separate CI job.
- Official `doctor --json`, run with local fixtures and output reduced to check
  IDs/statuses, returns **2**: config/source/tunnel-id/key/target/listener parsing
  passes, but `oauth_metadata` fails because doctor probes omit ordinary MCP auth.
  This failure is preserved; doctor is not the authenticated startup gate.
- The immutable public config is hashed in the image and checked before credential
  access. Only the disposable fixture overlay mounts a matching fixture config/hash
  for its simulated control-plane endpoint. Its tunnel network has no external
  egress. Production uses the reviewed outbound-capable bridge/config.

## Observed evidence so far

| Area | Result and exact scope |
| --- | --- |
| Host/URL opt-ins | PASS: exact default-off Host, loopback regressions, unexpected Host/Origin, duplicate/wrong/missing bearers, URL variants, redirects 301/302/303/307/308, proxy traps and bounded bodies |
| Focused auth/runtime/config tests | 84 passed; subsequent manager/config-integrity/build tests: 13 passed |
| Compose and trading-policy regressions | 150 passed, 1 existing skip; ordinary default remains one brokerage service; selected overlay adds exactly one tunnel service |
| Deployment tests | 69 passed, 81 subtests passed; disposable repositories/stubbed deployment only |
| Build exclusion and master helper | 5 passed; synthetic canaries excluded from context; unchanged no-echo/terminal/atomic helper tests |
| Full pytest checkpoint | 1314 passed, 1 skipped, 2 failed: startup mock expected old argument list; paper recovery subprocess exceeded 10 seconds during parallel verification. Updated only the startup assertion; both failures then passed unchanged otherwise (2 passed). Full final rerun remains pending |
| Static checks | Ruff lint/format pass. Full basedpyright initially exhausted the default ~512 MB heap; a 1024 MB run exposed three type errors, now fixed. Focused implementation type check: 0 errors, 0 warnings. Final full type check pending |
| Strict OpenSpec | `openspec validate --all --strict --no-interactive`: 27 passed, 0 failed |
| Normal real client | PASS in local simulation: authenticated runtime-key polling and all 3 expected ordinary-bearer MCP responses (initialize, tools/list, check_health) |
| Runtime/Docker network | PASS: real manager gates READ_ONLY before client; liveness/readiness up; Docker DNS reaches MCP; bridge OpenD:11111 refused; no tunnel ports; ephemeral host-loopback MCP publication |
| Rootless filesystem | PASS: real numeric runtime reads staged files; root and secret mounts reject writes. Actual numeric unrelated identities run through `setpriv` and cannot read protected master/staged sources |
| Runtime-key rotation | PASS: atomic root-master/staged replacement leaves running file bind on old inode; old key is rejected by simulated control plane; recreated actual client authenticates using the new key. Docker 29.8.1 also refreshed the mount on container restart in this experiment; forced recreation remains the supported procedure |
| Lifecycle checkpoint | Tunnel stop/recreation leaves authenticated host MCP usable and brokerage container identity unchanged. Further DNS/MCP-bearer/hung-client checks are still running; not claimed complete |
| Known fixture corrections | Metadata GET initially consumed a command; fixed fixture routing, then 3/3 responses passed. An early numeric-user test failed to create the user context; replaced by `setpriv` with UID assertion and rerun. A stale container ID in lifecycle bookkeeping was corrected; pending rerun results remain explicit |

## Mandatory compatibility matrix status

These observations do not turn untested matrix cases into passes. Keep the design's
matrix as the authoritative required scenario list, with this report supplying new
narrow observations:

- I1: PASS, unchanged reviewed release provenance.
- **CP1/CP2: FAIL**, preserved demonstrated OpenAI runtime-key diversion.
- CP3–CP7: UNTESTED beyond historical source inspection/reproduction. Clearing the
  child environment is not proof of the official client's full proxy confinement.
- CP8: PARTIAL — normal authenticated local polling/metadata/response observed;
  comprehensive initial missing/wrong/revoked endpoint/method cases remain UNTESTED.
- CP9: local simulated runtime-key rotation PASS; final reviewed-release rerun needed.
- MD1: PARTIAL — authenticated startup and initialize observed; doctor metadata
  failure recorded separately; full official discovery matrix incomplete.
- MD2: UNTESTED official discovery redirect matrix.
- MF1: normal local READ_ONLY forwarding PASS; no live OpenAI acceptance.
- MF2: UNTESTED official forwarding redirect matrix.
- MA1: component tests PASS; complete official-client negative-auth matrix UNTESTED.
- MP1: preflight no-proxy and manager environment tests PASS; official-client
  cross-credential/proxy matrix remains UNTESTED.
- MR1: full ordinary MCP bearer rotation is still pending the integration run.
- CT1: rootless portion PASS; rootful/final image provenance pending.
- CT2: local DNS/isolation/ports PASS; production-egress behavior not claimed from
  the deliberately isolated test network.
- CT3: component Host/Origin/mode/mutation tests PASS; final real-client matrix pending.
- CT4–CT6: partial/in progress; lifecycle, failure and rollback completion pending.
- L1: VPS, real OpenAI, full account/positions, ChatGPT web and native iPad **PENDING**,
  not authorized or executed.

## Hard release conditions

No ready-for-review transition, production enablement or task 8.3 completion until
reviewed official source/release integrity, every required redirect/proxy confinement
case, normal authenticated operation, both credential paths/rotation, and all final
real-client/container gates pass on the reviewed final artifact. A source remediation
candidate is not closure. No patched client, custom credential proxy, relaxed policy
or production pin change is shipped. The production wrapper and separate CI release
gate explicitly refuse release while task 1.2 is unresolved. No new upstream issue,
report or security-disclosure material has been posted.
