"""Static and isolated tests for the optional tunnel host service."""

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
RUNBOOK = ROOT / "docs" / "private-chatgpt-mcp.md"
CREDENTIAL_INSTALLER = ROOT / "scripts" / "install_private_chatgpt_credential.py"
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


def test_config_has_one_loopback_mcp_origin_and_file_backed_headers() -> None:
    raw = (ASSETS / "tunnel-client.yaml").read_text(encoding="utf-8")
    config = yaml.safe_load(raw)

    assert config["control_plane"]["base_url"] == "https://api.openai.com"
    assert config["control_plane"]["api_key"].startswith("file:/run/credentials/")
    assert config["mcp"]["server_urls"] == [
        {"channel": "main", "url": "http://127.0.0.1:8000/mcp"}
    ]
    expected_header = (
        "file:/run/credentials/moomoo-chatgpt-tunnel.service/mcp-authorization"
    )
    assert config["mcp"]["extra_headers"] == {"Authorization": expected_header}
    assert config["mcp"]["discovery_extra_headers"] == {
        "Authorization": expected_header
    }
    assert config["health"]["listen_addr"] == "127.0.0.1:8080"
    assert config["admin_ui"]["open_browser"] is False
    lowered = raw.lower()
    for forbidden in (
        "0.0.0.0",
        "cloudflared",
        "harpoon:",
        "proxy:",
        "oauth",
        "mcp_operator_token",
        "moomoo_trade_password",
        "sk-",
        "bearer ",
    ):
        assert forbidden not in lowered


def test_systemd_unit_is_unprivileged_hardened_and_independent() -> None:
    unit = (ASSETS / "moomoo-chatgpt-tunnel.service").read_text(encoding="utf-8")

    required = (
        "User=moomoo-tunnel",
        "Group=moomoo-tunnel",
        "UMask=0077",
        "LoadCredential=control-plane-api-key:",
        "LoadCredential=mcp-authorization:",
        "private_chatgpt_preflight.py --mode startup-safe",
        "tunnel-client doctor --config",
        "Restart=on-failure",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
        "ReadWritePaths=/run/moomoo-chatgpt-tunnel /var/lib/moomoo-chatgpt-tunnel",
    )
    for setting in required:
        assert setting in unit
    for forbidden in (
        "User=root",
        "docker.sock",
        "MCP_OPERATOR_TOKEN",
        "MOOMOO_TRADE_PASSWORD",
        "OPENAI_ADMIN",
        "Requires=moomoo",
        "PartOf=moomoo",
        "0.0.0.0",
        "cloudflared",
    ):
        assert forbidden not in unit
    exec_lines = [line for line in unit.splitlines() if line.startswith("Exec")]
    assert all("Bearer " not in line and "sk-" not in line for line in exec_lines)


def test_runbook_permissions_allow_config_but_protect_credential_sources() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "-o root -g moomoo-tunnel -m 0750" in text
    assert "-o root -g moomoo-tunnel -m 0640" in text
    assert "root:root` mode `0600`" in text
    assert "sudo sh -c" not in text
    assert "cat > /etc/moomoo-chatgpt-tunnel" not in text
    assert "install_private_chatgpt_credential.py" in text

    installer = CREDENTIAL_INSTALLER.read_text(encoding="utf-8")
    assert "termios.ECHO | termios.ECHONL" in installer
    assert "owner_uid=0" in installer
    assert "owner_gid=0" in installer


def test_optional_assets_do_not_modify_compose_topology() -> None:
    compose_files = sorted(ROOT.glob("docker-compose*.yml"))
    assert compose_files
    combined = "\n".join(path.read_text(encoding="utf-8") for path in compose_files)

    assert "tunnel-client" not in combined
    assert "moomoo-chatgpt-tunnel" not in combined
    assert "127.0.0.1:8000:8000" in combined
    assert "11111:11111" not in combined


def test_runbook_keeps_external_acceptance_stages_separate_and_pending() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for stage in (
        "1. Local MCP",
        "2. Tunnel runtime",
        "3. OpenAI eligibility",
        "4. ChatGPT web",
        "5. Native iPad",
    ):
        matching = [line for line in text.splitlines() if stage in line]
        assert len(matching) == 1
        assert "PENDING" in matching[0]
    assert "does not prove native\niPad support" in text
    assert "No supported per-connection ChatGPT tool allowlist was found" in text
    assert "Server-enforced `READ_ONLY` is the authorization" in text


def test_runbook_records_release_sources_operations_rotation_and_rollback() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for evidence in (
        "2026-09-24",
        EXPECTED_VERSION,
        "0f870e50a973fa820d4c409000059e181e8d242b",
        "api.openai.com:443",
        "Tunnels **Read** and **Use**",
        "tunnel-client doctor --explain",
        "/healthz",
        "/readyz",
        "## Secret rotation",
        "## Rollback",
        "systemctl disable --now moomoo-chatgpt-tunnel.service",
    ):
        assert evidence in text
    rollback = text.split("## Rollback", 1)[1]
    assert "docker compose down" not in rollback.lower()
    assert "--volumes" not in rollback
    assert "MCP_ALLOW_UNAUTHENTICATED_HTTP` is not" in text
