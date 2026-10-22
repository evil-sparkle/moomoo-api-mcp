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
trust in compromised chains. A vendor release built with patched Go is still
needed for that protection.

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
