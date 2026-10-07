"""Static and isolated tests for the official container client."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import yaml

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "deploy" / "tunnel-client"
EXPECTED_VERSION = "v0.0.14"
EXPECTED_SHA256 = "15bd17e805cad39d412199115bb9e10a978dd35258a114cdf25dd2ae6681c7d3"


def fake_release(tmp_path: Path) -> tuple[dict[str, str], Path]:
    archive = tmp_path / "fixture.zip"
    binary = b"#!/bin/sh\nprintf 'tunnel-client 0.0.14\\n'\n"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("tunnel-client", binary)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return (
        {
            "version": EXPECTED_VERSION,
            "archive": archive.name,
            "url": "https://example.invalid/fixture.zip",
            "sha256": digest,
        },
        archive,
    )


def test_release_manifest_pins_the_reviewed_official_asset() -> None:
    manifest = json.loads((ASSETS / "release.json").read_text(encoding="utf-8"))

    assert manifest == {
        "version": EXPECTED_VERSION,
        "published_at": "2026-09-01T21:04:04Z",
        "tag_commit": "0f870e50a973fa820d4c409000059e181e8d242b",
        "archive": "tunnel-client-v0.0.14-linux-amd64.zip",
        "url": (
            "https://github.com/openai/tunnel-client/releases/download/v0.0.14/"
            "tunnel-client-v0.0.14-linux-amd64.zip"
        ),
        "sha256": EXPECTED_SHA256,
    }


def test_verify_only_cli_does_not_install_or_start_any_service(tmp_path: Path) -> None:
    release, archive = fake_release(tmp_path)
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps(release), encoding="utf-8")
    destination = tmp_path / "must-not-exist"

    completed = subprocess.run(
        [
            sys.executable,
            str(ASSETS / "install.py"),
            "--manifest",
            str(manifest),
            "--archive",
            str(archive),
            "--destination",
            str(destination),
            "--verify-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert EXPECTED_VERSION in completed.stdout
    assert not destination.exists()


def test_verify_only_cli_rejects_digest_mismatch_before_installing(
    tmp_path: Path,
) -> None:
    release, archive = fake_release(tmp_path)
    release["sha256"] = "0" * 64
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps(release), encoding="utf-8")
    destination = tmp_path / "must-not-exist"

    completed = subprocess.run(
        [
            sys.executable,
            str(ASSETS / "install.py"),
            "--manifest",
            str(manifest),
            "--archive",
            str(archive),
            "--destination",
            str(destination),
            "--verify-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "SHA-256" in completed.stderr
    assert not destination.exists()


def test_config_uses_fixed_origins_and_environment_authentication() -> None:
    config = yaml.safe_load((ASSETS / "container/tunnel-client.yaml").read_text())
    assert config["control_plane"]["base_url"] == "https://api.openai.com"
    assert config["control_plane"]["api_key"] == "env:CONTROL_PLANE_API_KEY"
    assert config["mcp"]["server_urls"] == [
        {"channel": "main", "url": "http://moomoo-mcp:8000/mcp"}
    ]
    for section in ("extra_headers", "discovery_extra_headers"):
        assert config["mcp"][section] == {"Authorization": "env:MCP_AUTHORIZATION"}
    assert config["health"]["listen_addr"] == "127.0.0.1:8080"
    assert config["admin_ui"]["open_browser"] is False


def test_default_assets_preserve_brokerage_topology() -> None:
    compose_files = [
        ROOT / name
        for name in (
            "docker-compose.yml",
            "docker-compose.prod.yml",
            "docker-compose.paper.yml",
            "docker-compose.smoke.yml",
        )
    ]
    assert compose_files
    combined = "\n".join(path.read_text(encoding="utf-8") for path in compose_files)

    assert "tunnel-client" not in combined
    assert "moomoo-chatgpt-tunnel" not in combined
    assert "127.0.0.1:8000:8000" in combined
    assert "11111:11111" not in combined
