# October 2026 CA trust update

The application requires certifi >=2026.10.19 and the lockfile pins that release's
official PyPI wheel and sdist hashes. Ubuntu jammy builds require ca-certificates
>=20260816ubuntu0.22.04.1; Debian bookworm tunnel builds require
>=20250419~deb12u2. Both images run `deploy/trust/harden_ca.py` at build time to
exclude the exact emSign ECC G3 and Comodo AAA parent-root certificates, refresh
the hashed system CA directory, and replace pip's separate vendored certifi
bundle with the system bundle. Certificate verification remains enabled.

The tunnel Dockerfile rebuilds the same official v0.0.14 source commit
0f870e50a973fa820d4c409000059e181e8d242b using Go >=1.27.3. `source.json` pins the
source archive, go.mod and go.sum hashes. The build preserves the shipped embedded
UI and uses the vendor's version-stamping variables. It is a local source rebuild,
not the vendor release binary identified by `release.json`; that file remains the
reference for the original, unmodified official binary installer. Go's patched
crypto/x509 rejects the affected intermediate SPKIs even if a parent root is
introduced again. The explicit CA policy also protects the Python wrapper.

Production emergency overlays can be constructed from the exact deployed images
in disposable containers without credentials or state mounts, upgrading only CA
packages, application certifi and the same-source tunnel binary. Restore the
original image configuration when committing an overlay. Record the base image,
original app revision, security source commit, resulting image ID and rebuilt
binary hash separately; the original app revision is not the security commit.
A later normal CI build uses the source changes above and does not depend on
those local images or on the emergency host's public build artifacts.

Before deployment, test every effective bundle against all eight compromised
keys through both original and AAA cross-sign chains, and all twelve TLS test
leaf chains with hostname verification. Check actual stdlib/certifi TLS rejection
and successful normal TLS controls. Use synthetic credentials and isolated
control-plane fixtures for tunnel acceptance. Preserve every runtime setting,
OpenD/state volume, tunnel identity and authorization setting. Hold both the
shared operations flock and this repository's deployment lock; recreate the API
and tunnel separately, retaining a protected frozen rollback model. Manual
production probes can use initialize, ping and tools/list without invoking broker
tools; the unchanged tunnel wrapper performs its existing read-only startup gate.

OpenD remains a proprietary vendor binary with its own OpenSSL. Updating OS trust
protects its system trust paths; it does not prove its complete internal TLS or
certificate policy. No authenticated brokerage request should be redirected to
a public test endpoint.

References:

- [Ubuntu USN-8944-1](https://ubuntu.com/security/notices/USN-8944-1)
- [Debian bookworm security upload](https://tracker.debian.org/news/1813406/accepted-ca-certificates-20250419deb12u2-source-into-oldstable-security/)
- [Python incident guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
- [Go security announcement](https://groups.google.com/g/golang-announce/c/66pojueaQek)
- [Pinned tunnel source](https://github.com/openai/tunnel-client/tree/0f870e50a973fa820d4c409000059e181e8d242b)
