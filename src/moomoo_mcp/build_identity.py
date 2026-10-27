"""Versioned public build provenance, generated before image package installation.

Runtime reads only a bounded resource in this installed package. It never derives
a revision from Git, environment variables, tags, credentials or the gateway.
Embedded metadata is a build declaration, not remote source attestation.
"""

from __future__ import annotations

import argparse
import json
from importlib import resources
from pathlib import Path

METADATA_VERSION = 1
METADATA_FILENAME = "_build_identity.json"
MAX_METADATA_BYTES = 160


def encode_build_identity(source_revision: object) -> bytes:
    """Validate one canonical full Git SHA and encode only the public identity."""
    if (
        type(source_revision) is not str
        or len(source_revision) not in (40, 64)
        or any(character not in "0123456789abcdef" for character in source_revision)
    ):
        raise ValueError("source revision must be a full lowercase hexadecimal Git SHA")
    return (
        json.dumps(
            {"schema_version": METADATA_VERSION, "source_revision": source_revision},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def embedded_source_revision() -> str | None:
    """Return valid embedded build provenance or the explicit unknown fallback."""
    try:
        resource = resources.files("moomoo_mcp").joinpath(METADATA_FILENAME)
        with resource.open("rb") as stream:
            payload = stream.read(MAX_METADATA_BYTES + 1)
        if len(payload) > MAX_METADATA_BYTES:
            return None
        metadata = json.loads(payload)
        if (
            type(metadata) is not dict
            or set(metadata) != {"schema_version", "source_revision"}
            or type(metadata["schema_version"]) is not int
            or metadata["schema_version"] != METADATA_VERSION
        ):
            return None
        revision = metadata["source_revision"]
        # Canonical encoding also refuses duplicate keys and extra whitespace;
        # an accepted resource is exactly what the image generator produces.
        if payload != encode_build_identity(revision):
            return None
        return revision
    except (OSError, ValueError, TypeError, UnicodeError):
        # Missing/unreadable/invalid metadata must not masquerade as a known SHA
        # or prevent ordinary developer/wheel health checks from remaining usable.
        return None


def main() -> None:
    """Build-time-only writer; the Dockerfile invokes this before wheel install."""
    parser = argparse.ArgumentParser(description="Generate public image build identity")
    parser.add_argument("--source-revision", required=True)
    arguments = parser.parse_args()
    try:
        payload = encode_build_identity(arguments.source_revision)
    except ValueError as error:
        parser.error(str(error))
    else:
        Path(__file__).with_name(METADATA_FILENAME).write_bytes(payload)


if __name__ == "__main__":
    main()
