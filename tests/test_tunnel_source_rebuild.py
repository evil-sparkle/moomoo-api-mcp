"""The source rebuild pins dependencies and rejects unsafe archive/toolchain input."""

import hashlib
import io
import runpy
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

REBUILD = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "deploy/tunnel-client/rebuild.py")
)


def source_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    values = {"go.mod": "module public.fixture\n", "go.sum": "pinned fixture\n"}
    manifest = {"version": "0.0.14", "minimum_go": "1.27.3"}
    for name, body in values.items():
        (source / name).write_text(body)
        manifest[name.replace(".", "_") + "_sha256"] = hashlib.sha256(
            body.encode()
        ).hexdigest()
    (source / "pkg/version").mkdir(parents=True)
    (source / "pkg/version/VERSION").write_text(manifest["version"])
    return source, manifest


def test_changed_dependency_lock_aborts(tmp_path):
    source, manifest = source_fixture(tmp_path)
    REBUILD["verify_source"](source, manifest)
    (source / "go.sum").write_text("changed dependency")
    with pytest.raises(ValueError, match="go.sum"):
        REBUILD["verify_source"](source, manifest)


def test_older_go_cannot_compile_the_tunnel(tmp_path):
    source, manifest = source_fixture(tmp_path)
    with (
        patch(
            "subprocess.check_output", return_value="go version go1.27.0 linux/amd64"
        ),
        patch("subprocess.run") as execute,
        pytest.raises(ValueError, match="SPKI"),
    ):
        REBUILD["build"](source, tmp_path / "client", manifest)
    execute.assert_not_called()


def test_archive_hash_mismatch_is_rejected(tmp_path):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"untrusted archive")
    with pytest.raises(ValueError, match="SHA-256"):
        REBUILD["fetch"](
            tmp_path / "output", {"url": archive.as_uri(), "sha256": "0" * 64}
        )
    assert not (tmp_path / "output").exists()


def test_archive_path_escape_is_rejected_even_with_matching_hash(tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("../../escaped-file")
        entry.size = 3
        bundle.addfile(entry, io.BytesIO(b"bad"))
    manifest = {
        "url": archive.as_uri(),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    with pytest.raises(tarfile.FilterError):
        REBUILD["fetch"](tmp_path / "output", manifest)
    assert not (tmp_path / "escaped-file").exists()
