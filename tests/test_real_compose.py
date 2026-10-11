"""REAL deployment selects its own persistent volume and retains configured paper."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from test_compose_topology import OPEND_DATA_PATH, SERVICE, _render

from scripts import deploy_verify


@pytest.mark.parametrize("paper", [False, True])
def test_real_overlay_owns_separate_volume_without_moving_existing_state(paper):
    overlays = ["docker-compose.yml"]
    if paper:
        overlays.append("docker-compose.paper.yml")
    before = _render(*overlays, env={"MOOMOO_SIMULATED_ACC_IDS": "123"})
    rendered = _render(
        *overlays,
        "docker-compose.real.yml",
        env={
            "MOOMOO_TRADING_MODE": "REAL",
            "MOOMOO_REAL_ACC_IDS": "456",
            "MOOMOO_SIMULATED_ACC_IDS": "123",
        },
    )
    service = rendered["services"][SERVICE]
    mounts = {item["target"]: item for item in service["volumes"]}
    original = {item["target"]: item for item in before["services"][SERVICE]["volumes"]}
    assert mounts[OPEND_DATA_PATH] == original[OPEND_DATA_PATH]
    assert mounts["/app/real-execution-data"]["source"] == "real-execution-data"
    assert mounts["/app/real-execution-data"]["type"] == "volume"
    if paper:
        assert (
            mounts["/var/lib/moomoo-mcp/data"] == original["/var/lib/moomoo-mcp/data"]
        )
    assert (
        service["environment"]["MOOMOO_REAL_JOURNAL_PATH"]
        == "/app/real-execution-data/execution.db"
    )
    assert service["environment"]["MOOMOO_CREATE_REAL_JOURNAL"] == "0"


@pytest.mark.parametrize(
    "mode,accounts,expected",
    [
        ("READ_ONLY", "123", []),
        ("SIMULATE", "123", ["docker-compose.paper.yml"]),
        ("REAL", "", ["docker-compose.real.yml"]),
        (" real ", "123", ["docker-compose.paper.yml", "docker-compose.real.yml"]),
    ],
)
def test_production_storage_selection_matches_mode_and_paper_configuration(
    mode, accounts, expected
):
    services = {
        SERVICE: {
            "environment": {
                "MOOMOO_TRADING_MODE": mode,
                "MOOMOO_SIMULATED_ACC_IDS": accounts,
            }
        }
    }
    with patch.object(deploy_verify, "resolve_services", return_value=services):
        assert deploy_verify.execution_overlays() == expected


def test_invalid_deployment_mode_cannot_skip_required_storage():
    with (
        patch.object(
            deploy_verify,
            "resolve_services",
            return_value={SERVICE: {"environment": {"MOOMOO_TRADING_MODE": "UNKNOWN"}}},
        ),
        pytest.raises(deploy_verify.ConfigError),
    ):
        deploy_verify.execution_overlays()


@pytest.mark.parametrize("paper", [False, True])
def test_production_wrapper_passes_all_real_storage_overlays(tmp_path, paper):
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("compose-prod.sh", "deploy_verify.py"):
        shutil.copy2(root / "scripts" / name, scripts / name)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    model = {
        "services": {
            SERVICE: {
                "environment": {
                    "MOOMOO_TRADING_MODE": "REAL",
                    "MOOMOO_SIMULATED_ACC_IDS": "123" if paper else "",
                }
            }
        }
    }
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport json,pathlib,sys\n"
        "if 'config' in sys.argv: print(" + repr(json.dumps(model)) + ")\n"
        "else: pathlib.Path('docker-call.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), "up", "-d"],
        cwd=tmp_path,
        env={"PATH": str(fake_bin) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads((tmp_path / "docker-call.json").read_text())
    assert "docker-compose.real.yml" in arguments
    assert ("docker-compose.paper.yml" in arguments) is paper
    assert arguments[-2:] == ["up", "-d"]
