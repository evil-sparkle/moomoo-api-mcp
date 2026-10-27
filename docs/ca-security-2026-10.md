# October 2026 CA trust update — v0.0.18 candidate

**Status (October 25, 2026): PR #70 remains DRAFT.** The supported tunnel
image now references the original, publisher-hash-pinned, unmodified upstream
`tunnel-client` **v0.0.18** Linux amd64 archive. This is a *candidate pin*,
not evidence that repository-specific TLS acceptance or the full managed
integration passed after the upgrade. **Do not deploy or merge on the basis
of the pin alone.** PR #71 was merged then reverted from main by PR #72;
the emergency mitigations remain on the separate hotfix branch and deployed
image changes are not implied by this PR.

## Upstream integrity and security boundary

- The release tag is `v0.0.18`, source commit
  `274a5588414819f063d27100a4fb1096aae3830f`, released October 23.
  The public release UI reports 20:25 UTC (minute precision). This repository
  pins `tunnel-client-v0.0.18-linux-amd64.zip` to SHA-256
  `3c82307316acd93c88a384b62af1d81d3f4cb0efa975f9c0bfaef922866a04c4`.
  The checksum and static Go metadata match the upstream-published archive
  according to the October 24 verification already recorded in this PR;
  **they have not been independently re-executed in the October 25 review**.
- The release notes and upstream workflow target Go 1.27.3. Upstream
  GO-2026-6809 / CVE-2026-111943 states that `crypto/x509`
  `Certificate.Verify` now rejects chains containing certain distrusted
  intermediates, even with stale or imported roots. Compiler metadata, a
  clean default CA store, or an upstream advisory alone cannot prove every
  TLS call site in this application uses the protected verifier.
- v0.0.18 adds **optional** AWS workload identity and Harpoon loopback HTTP
  templates. The checked-in tunnel config continues to use the static
  `CONTROL_PLANE_API_KEY`, scoped MCP authorization, a fixed MCP endpoint,
  and loopback-only health. The new opt-in features are not enabled.
- Official releases publish SHA256SUMS, a signed Sigstore/SLSA bundle,
  per-archive SPDX/license sidecars, vulnerability reports and enterprise
  evidence. **Signature/provenance verification and vulnerability triage
  remain to be completed**; an archive checksum is not a signature, nor
  does a Go patch prove every third-party dependency is free of advisories.

### Required release-evidence verification (not yet completed)

Use a disposable machine with `gh`, Python, Git and the official
verification scripts from the **matching** upstream tag (not `master`).
Fetch all release assets to validate the complete subject/evidence set:

```bash
git clone --depth 1 --branch v0.0.18 https://github.com/openai/tunnel-client.git /tmp/tunnel-client-v18
mkdir -p /tmp/tunnel-client-v18-assets
gh release download v0.0.18 --repo openai/tunnel-client --dir /tmp/tunnel-client-v18-assets
release_sha=274a5588414819f063d27100a4fb1096aae3830f
/tmp/tunnel-client-v18/scripts/verify_release_provenance.sh \
  --bundle /tmp/tunnel-client-v18-assets/tunnel-client-v0.0.18-provenance.sigstore.json \
  --artifact-dir /tmp/tunnel-client-v18-assets --release v0.0.18 --source-digest "$release_sha"
/tmp/tunnel-client-v18/scripts/verify_release_evidence.sh \
  --artifact-dir /tmp/tunnel-client-v18-assets --release v0.0.18 --source-digest "$release_sha"
grep 'tunnel-client-v0.0.18-linux-amd64.zip' /tmp/tunnel-client-v18-assets/SHA256SUMS.txt
sha256sum /tmp/tunnel-client-v18-assets/tunnel-client-v0.0.18-linux-amd64.zip
```

Cross-check the hash with `deploy/tunnel-client/release.json`, inspect the
matching archive sidecar and the release-specific vulnerability report, and
use `go version -m` on the extracted binaries. The official build includes
`cloudflared` in the archive, although our installer copies only
`tunnel-client`. Record actual verification outputs before approval.

## Supported image requirements

The API image requires Ubuntu 22.04
`ca-certificates >= 20260816ubuntu0.22.04.1` and
`libssl3 >= 3.0.2-0ubuntu1.30`. It uses uv 0.13.5, official managed
CPython 3.12.15, and application `certifi >= 2026.10.19`.
The managed Python includes pip 26.2.2 installed and in ensurepip.
The tunnel wrapper uses official Python 3.13.17 slim Bookworm and
requires Debian `ca-certificates >= 20250419~deb12u2` and
`libssl3 >= 3.0.22-1~deb12u1`. Both images use vendor security
packages without local CA or pip-wheel rewriting. These distro OpenSSL
requirements are independent from Go's certificate denylist and do
not replace OpenD's own bundled TLS library.

## Acceptance gates before marking the PR ready

1. **Certificate rejection:** Against the *actual* official client and
   configured TLS paths, verify rejection for all eight affected
   intermediate SPKIs across original, cross-sign, bridge and imported
   trust paths. Include ordinary default system roots, `SSL_CERT_FILE`,
   `SSL_CERT_DIR`, `CA_BUNDLE` where supported, and custom trust
   roots/directories. Explicitly fail the test if a chain is accepted
   because the bad issuer was reintroduced. Run normal trusted TLS,
   hostname-mismatch, untrusted-issuer and expiration controls.
2. **Compatibility:** Build both supported images and run the real official
   client with the repository's synthetic internal control-plane fixture.
   Verify API-key authentication, invalid and rotated bearer rejection,
   scoped MCP authorization, only allowed request forwarding, no secret
   leakage across redirect/origin changes, health/readiness and normal
   request/response transport. Confirm v0.0.18's new AWS/Harpoon opt-ins
   are not accidentally active.
3. **Recovery and isolation:** Run both default and rootless managed
   integration, including process crash watchdog, control-plane outage,
   broker container replacement/IP change without tunnel restart,
   fresh forwarded `tools/list` after terminal errors, persistent journal
   and OpenD state, READ_ONLY/REAL gate, isolated volumes, localhost
   bindings and no production credentials. The pre-upgrade rootless job
   failed late due to a Docker RootlessKit dynamic-port collision;
   do not treat that historical run as complete acceptance.
4. **Gate:** Standard CI, image builds, managed integration and release
   provenance must pass at the **new exact PR SHA**. Preserve the
   emergency hotfix branch independently; rollout and ECR/IAM approvals
   are separate and must not be inferred from passing CI.

## Unresolved vendor and rollout items

- Debian Bookworm zlib remains vendor-unfixed and Ubuntu Jammy is under
  evaluation for CVE-2026-85091 at the last recorded check; actual
  application reachability of the affected code paths is unconfirmed.
- OpenD's proprietary bundled OpenSSL 3.0.17 and trust/hostname behavior
  lack official version-specific confirmation. A patched vendor release
  or substantive vendor evidence is still needed to claim full TLS coverage.
- The Python 3.9 virtualenv pip seed and unsupported platform wheels are
  outside the deployed Python 3.12/3.13 images; they were not revalidated.
- No production services, host credentials, AWS IAM roles, broker orders or
  VPS deployment settings are changed by this source-only PR.

References: [upstream release](https://github.com/openai/tunnel-client/releases/tag/v0.0.18),
[upstream Go fix](https://pkg.go.dev/vuln/GO-2026-6809),
[GitHub signed release instructions](https://github.com/openai/tunnel-client#validate-a-downloaded-release),
[Debian zlib](https://security-tracker.debian.org/tracker/CVE-2026-85091),
[Ubuntu zlib](https://ubuntu.com/security/CVE-2026-85091),
[OpenD vendor log](https://openapi.moomoo.com/moomoo-api-doc/en/changelog/changelog.html).
