# October 2026 CA trust update

The API uses Ubuntu 22.04 `ca-certificates >=20260816ubuntu0.22.04.1`,
uv 0.13.5, its official managed CPython 3.12.15 build from
python-build-standalone 20261021, and application `certifi >=2026.10.19`.
That managed runtime includes pip 26.2.2 both installed and in ensurepip.
Updating application certifi or system certificates alone does not fix an older
pip, which also trusts its own vendored certificate bundle.

The tunnel installs the unmodified, SHA-256-pinned official v0.0.17 binary.
Its Python wrapper uses the official Python 3.13.17 slim bookworm image, whose
installed pip and ensurepip seed are both 26.2.2, and requires Debian
`ca-certificates >=20250419~deb12u2`. The official Python 3.12.15 image still
contains pip 25.0.1, so it does not provide equivalent seed protection.
Neither image rewrites CA bundles, pip resources, or seed wheels.

The official tunnel v0.0.17 binary was compiled with Go 1.27.0. Updated native
CA stores mitigate the compromised original, bridge, and AAA cross-sign paths
under the default trust configuration. This does not provide the intermediate
SPKI denylist in Go 1.27.3: importing affected roots through `CA_BUNDLE`,
`SSL_CERT_FILE`, `SSL_CERT_DIR`, or a custom certificate directory can restore
trust in compromised chains. The pinned artifact needs a patched vendor build
for that protection; the current availability and adoption criteria follow.

## Draft status and upstream waiting criteria (October 24)

- **The upstream Go fix is now available.** Official tunnel v0.0.18 was published
  October 23. Its Linux amd64 ZIP matches the publisher's SHA-256
  `3c82307316acd93c88a384b62af1d81d3f4cb0efa975f9c0bfaef922866a04c4`;
  static `go version -m` inspection identifies Go 1.27.3 in both `tunnel-client`
  and its bundled `cloudflared`. Neither binary was executed for this inspection.
  Waiting for another compiler-fix release or maintaining our own Go rebuild is
  unnecessary once this official artifact is adopted and validated.
- **PR #70 still pins v0.0.17.** Keep it draft until the fixed official artifact
  is pinned and its installer/runtime assets, existing API-key configuration,
  managed forwarding, authorization and recovery behavior are validated.
  Review the release's SBOM, provenance and vulnerability evidence; its Go
  version alone does not establish complete dependency remediation. Test the
  eight distrusted keys with original, cross-sign and imported/custom trust,
  plus hostname verification and successful normal TLS controls. This dated
  follow-up changes documentation only; it does not adopt v0.0.18 yet.
- **Official CA, Python and pip fixes already exist.** This PR's supported
  runtime/CA packages address the previously tested trust-store paths without
  local bundle or ensurepip rewriting. Retest the effective system, application,
  pip and seed stores after an artifact refresh. The legacy Python 3.9
  virtualenv seed is outside the application's Python >=3.10 requirement.
- **Container zlib still needs vendor resolution.** For CVE-2026-85091, Debian
  lists Bookworm `1:1.2.13.dfsg-1` vulnerable without a fixed package; Ubuntu
  Jammy remains under evaluation. Await a fixed package for the deployed suite
  or an explicit vendor nonaffected determination. Application reachability of
  the affected gzip-writing/error-recovery paths has not been established.
- **OpenD needs version-specific vendor evidence.** Its bundled OpenSSL 3.0.17
  and internal trust policy remain unverified. An official patched release or
  vendor confirmation of applicable backports and certificate/hostname checks
  is needed before claiming complete OpenD TLS coverage. This is a vendor
  verification item, not evidence that an exploit or compromise occurred.

ECR publisher IAM authorization and production rollout approval are separate
operational prerequisites. Neither is a reason to wait for a new tunnel release.
PR #71 retains the emergency mitigations while this supported path stays draft.

Rebuild with `--pull --no-cache` to refresh distro certificates. Verify every
effective system, certifi, installed-pip and ensurepip store against all eight
affected keys and both cross-sign paths, bridge certificates, and hostname
chains. Exercise actual default Python and tunnel TLS rejection and successful
normal TLS controls. OpenD's proprietary internal TLS policy remains unverified
beyond its OS trust paths.

References:

- [Python incident guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
- [uv 0.13.5 runtime and pip update](https://github.com/astral-sh/uv/releases/tag/0.13.5)
- [Ubuntu USN-8944-1](https://ubuntu.com/security/notices/USN-8944-1)
- [Debian bookworm security update](https://tracker.debian.org/news/1813406/accepted-ca-certificates-20250419deb12u2-source-into-oldstable-security/)
- [Official tunnel v0.0.17 release](https://github.com/openai/tunnel-client/releases/tag/v0.0.17)
- [Go security announcement](https://groups.google.com/g/golang-announce/c/66pojueaQek)

- [Official tunnel v0.0.18 release](https://github.com/openai/tunnel-client/releases/tag/v0.0.18)
- [Go compromised-intermediate advisory](https://pkg.go.dev/vuln/GO-2026-6809)
- [Debian zlib status](https://security-tracker.debian.org/tracker/CVE-2026-85091)
- [Ubuntu zlib status](https://ubuntu.com/security/CVE-2026-85091)
- [OpenD vendor changelog](https://openapi.moomoo.com/moomoo-api-doc/en/changelog/changelog.html)
