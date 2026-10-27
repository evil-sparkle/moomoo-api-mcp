"""Exclude the two public parent roots for compromised eMudhra intermediates.

This is a trust-store mitigation, not Go's intermediate SPKI denylist.
Keep it even with patched runtimes to protect Python and OpenD's OS trust paths.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import ssl
import stat
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

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


def refresh_pip(root: Path, bundle: str) -> None:
    """Refresh installed pip and its dormant ensurepip seed without changing code."""
    prefixes = [root / "usr/local/lib", root / "app/.venv/lib"]
    prefixes.extend((root / "opt/uv-python").glob("*/lib"))
    for prefix in prefixes:
        for path in prefix.glob("python*/site-packages/pip/_vendor/certifi/cacert.pem"):
            path.write_text(bundle)
        for path in prefix.glob("python*/ensurepip/_bundled/pip-*.whl"):
            refresh_seed_wheel(path, bundle.encode())


def refresh_seed_wheel(path: Path, bundle: bytes) -> None:
    target = "pip/_vendor/certifi/cacert.pem"
    with ZipFile(path) as original:
        if target not in original.namelist():
            return
        if original.read(target) == bundle:
            return
        records = [
            name for name in original.namelist() if name.endswith(".dist-info/RECORD")
        ]
        if len(records) != 1:
            raise ValueError("Unexpected ensurepip wheel RECORD")
        rows = list(csv.reader(io.StringIO(original.read(records[0]).decode())))
        matches = [row for row in rows if row[0] == target]
        if len(matches) != 1:
            raise ValueError("Unexpected ensurepip CA RECORD entry")
        matches[0][1] = "sha256=" + base64.urlsafe_b64encode(
            hashlib.sha256(bundle).digest()
        ).decode().rstrip("=")
        matches[0][2] = str(len(bundle))
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="\n").writerows(rows)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            replacement = Path(temporary.name)
        try:
            with ZipFile(replacement, "w") as updated:
                for info in original.infolist():
                    data = original.read(info.filename)
                    if info.filename == target:
                        data = bundle
                    elif info.filename == records[0]:
                        data = output.getvalue().encode()
                    updated.writestr(info, data)
            replacement.chmod(stat.S_IMODE(path.stat().st_mode))
            replacement.replace(path)
        finally:
            replacement.unlink(missing_ok=True)


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
    # pip is separate from application certifi, including managed Python seeds.
    refresh_pip(root, bundle)
    print("CA parent roots excluded; system and pip trust refreshed")


if __name__ == "__main__":
    main()
