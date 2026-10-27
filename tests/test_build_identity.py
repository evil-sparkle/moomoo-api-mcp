"""Public build metadata generation and health identity; no broker/Git reads."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from moomoo_mcp import build_identity
from moomoo_mcp.tools import trading_contract

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


@pytest.mark.parametrize("revision", [REVISION, "0123456789abcdef" * 4])
def test_canonical_build_identity_contains_only_version_and_full_sha(revision):
    payload = build_identity.encode_build_identity(revision)
    assert payload == (
        '{"schema_version":1,"source_revision":"' + revision + '"}\n'
    ).encode("ascii")
    assert len(payload) <= build_identity.MAX_METADATA_BYTES


@pytest.mark.parametrize(
    "revision",
    [
        None,
        True,
        123,
        b"a" * 40,
        "",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "g" * 40,
        " " + REVISION,
        REVISION + "\n",
        "sha256:" + "a" * 64,
    ],
)
def test_invalid_source_revision_is_rejected_without_normalizing(revision):
    with pytest.raises(ValueError) as failure:
        build_identity.encode_build_identity(revision)
    assert "full lowercase hexadecimal" in str(failure.value)


def install_resource(monkeypatch, tmp_path, payload=None):
    if payload is not None:
        (tmp_path / build_identity.METADATA_FILENAME).write_bytes(payload)
    monkeypatch.setattr(build_identity.resources, "files", lambda _package: tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"not JSON",
        b"\xff",
        b"[]",
        b"null",
        b"{}",
        b" " * (build_identity.MAX_METADATA_BYTES + 1),
        b'{"schema_version":true,"source_revision":"' + REVISION.encode() + b'"}\n',
        b'{"schema_version":2,"source_revision":"' + REVISION.encode() + b'"}\n',
        b'{"schema_version":1,"source_revision":null}\n',
        b'{"schema_version":1,"source_revision":123}\n',
        b'{"schema_version":1,"source_revision":"' + b"A" * 40 + b'"}\n',
        b'{"schema_version":1,"source_revision":"'
        + REVISION.encode()
        + b'","source_revision":"'
        + REVISION.encode()
        + b'"}\n',
        b'{"schema_version":1,"source_revision":"'
        + REVISION.encode()
        + b'","unneeded":"discard"}\n',
        b' {"schema_version":1,"source_revision":"' + REVISION.encode() + b'"}\n',
    ],
)
def test_absent_or_noncanonical_resource_keeps_identity_unknown(
    monkeypatch, tmp_path, payload
):
    install_resource(monkeypatch, tmp_path, payload)
    assert build_identity.embedded_source_revision() is None
    result = trading_contract.server_contract_identity([])
    assert result["source_revision"] == "unknown"
    assert result["build_revision"] is None


def test_embedded_resource_controls_existing_health_fields_without_env_or_git(
    monkeypatch, tmp_path
):
    install_resource(
        monkeypatch, tmp_path, build_identity.encode_build_identity(REVISION)
    )
    baseline = trading_contract.server_contract_identity([])
    monkeypatch.setenv("SOURCE_REVISION", "b" * 40)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    with (
        patch("os.getenv", side_effect=AssertionError("runtime environment lookup")),
        patch("subprocess.run", side_effect=AssertionError("runtime process lookup")),
        patch(
            "subprocess.check_output", side_effect=AssertionError("runtime Git lookup")
        ),
    ):
        result = trading_contract.server_contract_identity([])
    assert result == baseline
    assert result["source_revision"] == REVISION
    assert result["build_revision"] == REVISION


def test_build_cli_writes_only_its_fixed_adjacent_resource(tmp_path):
    script = tmp_path / "build_identity.py"
    script.write_bytes(Path(build_identity.__file__).read_bytes())
    subprocess.run(
        [sys.executable, str(script), "--source-revision", REVISION],
        check=True,
        capture_output=True,
        timeout=10,
    )
    artifact = tmp_path / build_identity.METADATA_FILENAME
    assert artifact.read_bytes() == build_identity.encode_build_identity(REVISION)
    assert {path.name for path in tmp_path.iterdir()} == {script.name, artifact.name}
    previous = artifact.read_bytes()
    rejected = subprocess.run(
        [sys.executable, str(script), "--source-revision", "malformed-revision"],
        capture_output=True,
        timeout=10,
    )
    assert rejected.returncode != 0
    assert artifact.read_bytes() == previous
    assert b"malformed-revision" not in rejected.stderr


def test_image_generates_resource_before_install_and_never_copies_host_metadata():
    dockerfile = (ROOT / "Dockerfile").read_text()
    copy_source = dockerfile.index("COPY src/ ./src/")
    argument = dockerfile.index('ARG SOURCE_REVISION=""')
    generate = dockerfile.index("./src/moomoo_mcp/build_identity.py --source-revision")
    install = dockerfile.index("uv sync --frozen --no-dev --no-editable")
    assert copy_source < argument < generate < install
    assert 'if [ -n "${SOURCE_REVISION}" ]; then' in dockerfile
    resource = "src/moomoo_mcp/" + build_identity.METADATA_FILENAME
    assert resource in (ROOT / ".dockerignore").read_text().splitlines()
    assert "/" + resource in (ROOT / ".gitignore").read_text().splitlines()


def test_json_type_mismatch_never_uses_a_stringified_revision(monkeypatch, tmp_path):
    payload = (
        json.dumps(
            {"schema_version": 1, "source_revision": [REVISION]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    install_resource(monkeypatch, tmp_path, payload)
    assert build_identity.embedded_source_revision() is None
