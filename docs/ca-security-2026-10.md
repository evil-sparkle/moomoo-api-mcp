# October 2026 CA trust update

The application requires certifi >=2026.10.19 and the lockfile pins that release's
official PyPI wheel and sdist hashes. Ubuntu jammy builds require ca-certificates
>=20260816ubuntu0.22.04.1; Debian bookworm tunnel builds require
>=20250419~deb12u2. Both images run `deploy/trust/harden_ca.py` at build time to
exclude the exact emSign ECC G3 and Comodo AAA parent-root certificates, refresh
the hashed system CA directory, and replace pip's separate vendored certifi
bundle with the system bundle, including managed Python under `/opt/uv-python`
and dormant ensurepip seed wheels. Seed-wheel RECORD hashes are updated for the
CA resource; the hardener leaves pip code and version metadata untouched. The API repeats
hardening after the final frozen uv sync. Certificate verification remains enabled.

The October 24 follow-up also requires Ubuntu jammy `libssl3`
`3.0.2-0ubuntu1.30` or newer and refreshes Debian bookworm `libssl3`/`openssl`
to `3.0.22-1~deb12u1` or newer in both tunnel build and runtime stages. These
are build-time minimums: a cached base with current CA certificates but older
OpenSSL must not pass. The API retains its existing OpenD asset pins and the tunnel retains its
same-source Go 1.27.3 rebuild. This does not assert that every OpenSSL issue is
fixed; Debian still tracks open issues for the 3.0 branch.


The API also carries forward the earlier supported-runtime candidate's managed
Python 3.12.15, whose bundled OpenSSL reports 3.5.9. The previous Python 3.12.13
reported OpenSSL 3.5.7 even after the distro package update. Keep these runtime
and distro checks separate; changing Ubuntu's libssl3 does not replace Python's
bundled OpenSSL. This runtime refresh leaves application dependency locks and
the emergency Go/CA policy intact.

The existing pip and ensurepip CA-resource repair remains in place. Older pip
version strings and vendored certifi metadata do not demonstrate that the
compromised roots are still trusted: validate the effective CA bundle. The live
API managed-pip and tunnel-pip bundles matched their hardened system bundles in
the October 24 read-only audit. No pip code or version update is needed for this
particular trust-store repair; the supported Python runtime can independently
bring newer official pip artifacts.

Container zlib is a separate unresolved item: Debian marks bookworm
`1:1.2.13.dfsg-1` vulnerable with no fixed package recorded for CVE-2026-85091;
Ubuntu jammy remains under evaluation. Application reachability of the affected
`gz*` writing/error-recovery paths has not been demonstrated. Do not claim these
containers are fixed because the host's Ubuntu noble package is patched, or
replace vendor libraries with an unverified upstream build.

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
tools; the tunnel wrapper performs its read-only startup gate. The synchronized
wrapper retains main's restricted-client checks for REAL backends and rejects
live-write and gateway-control permissions.

OpenD remains a proprietary vendor binary with its own OpenSSL. The October 24
audit found the running OpenD process loading its bundled OpenSSL 3.0.17; its
vendor backport status is unverified. Updating the distro package does not
replace that library. Use a vendor-supported OpenD release when available.
Updating OS trust protects its system trust paths; it does not prove its complete
internal TLS or certificate policy. No authenticated brokerage request should be
redirected to a public test endpoint.

References:

- [Ubuntu USN-8944-1](https://ubuntu.com/security/notices/USN-8944-1)
- [Debian bookworm security upload](https://tracker.debian.org/news/1813406/accepted-ca-certificates-20250419deb12u2-source-into-oldstable-security/)
- [Python incident guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
- [Go security announcement](https://groups.google.com/g/golang-announce/c/66pojueaQek)
- [Pinned tunnel source](https://github.com/openai/tunnel-client/tree/0f870e50a973fa820d4c409000059e181e8d242b)
- [Ubuntu OpenSSL security backports, USN-8847-1](https://ubuntu.com/security/notices/USN-8847-1)
- [Debian OpenSSL security package status](https://security-tracker.debian.org/tracker/source-package/openssl)
- [Debian zlib CVE status](https://security-tracker.debian.org/tracker/CVE-2026-85091)
- [Ubuntu zlib CVE status](https://ubuntu.com/security/CVE-2026-85091)
