"""Exclude the two public parent roots for compromised eMudhra intermediates.

This is a trust-store mitigation, not Go's intermediate SPKI denylist.
Keep it even with patched runtimes to protect Python and OpenD's OS trust paths.
"""

from __future__ import annotations

import hashlib
import ssl
import subprocess
from pathlib import Path

# Exact DER fingerprints, independently checked against the public roots.
# Debian bookworm's security update blacklists the same parent certificates.
BLOCKED_ROOTS = {
    "mozilla/emSign_ECC_Root_CA_-_G3.crt": (
        "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
    ),
    "mozilla/Comodo_AAA_Services_root.crt": (
        "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4"
    ),
}


def fingerprints(bundle: str) -> set[str]:
    result = set()
    for part in bundle.split("-----END CERTIFICATE-----"):
        if "-----BEGIN CERTIFICATE-----" not in part:
            continue
        pem = part[part.index("-----BEGIN CERTIFICATE-----") :]
        der = ssl.PEM_cert_to_DER_cert(pem + "-----END CERTIFICATE-----\n")
        result.add(hashlib.sha256(der).hexdigest())
    if not result:
        raise ValueError("CA bundle contains no certificates")
    return result


def disable_roots(root: Path) -> None:
    for name, expected in BLOCKED_ROOTS.items():
        path = root / "usr/share/ca-certificates" / name
        if path.exists() and fingerprints(path.read_text()) != {expected}:
            raise ValueError("Unexpected parent-root fingerprint: " + name)
    config = root / "etc/ca-certificates.conf"
    lines = config.read_text().splitlines()
    config.write_text(
        "\n".join("!" + line if line in BLOCKED_ROOTS else line for line in lines)
        + "\n"
    )


def assert_safe(bundle: str) -> None:
    if fingerprints(bundle).intersection(BLOCKED_ROOTS.values()):
        raise ValueError("Compromised parent root is still trusted")


def main() -> None:
    root = Path("/")
    disable_roots(root)
    subprocess.run(["update-ca-certificates", "--fresh"], check=True)
    system = root / "etc/ssl/certs/ca-certificates.crt"
    bundle = system.read_text()
    assert_safe(bundle)
    # python-build-standalone looks here; also refresh the hashed CA directory.
    alias = root / "etc/ssl/cert.pem"
    if not alias.exists() and not alias.is_symlink():
        alias.symlink_to("certs/ca-certificates.crt")
    # pip has a separate vendored certifi, not the application dependency.
    for prefix in (root / "usr/local/lib", root / "app/.venv/lib"):
        for path in prefix.glob("python*/site-packages/pip/_vendor/certifi/cacert.pem"):
            path.write_text(bundle)
    print("CA parent roots excluded; system and pip trust refreshed")


if __name__ == "__main__":
    main()
