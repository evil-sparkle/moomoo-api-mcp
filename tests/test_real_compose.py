"""REAL deployment selects its own persistent volume and retains configured paper."""

import json
import subprocess
from unittest.mock import patch

import pytest

from scripts import deploy_verify
from tests.compose_helpers import (
    OPEND_DATA_PATH,
    SERVICE,
    production_wrapper,
    render_compose,
)


@pytest.mark.parametrize("paper", [False, True])
def test_real_overlay_owns_separate_volume_without_moving_existing_state(paper):
    overlays = ["docker-compose.yml"]
    if paper:
        overlays.append("docker-compose.paper.yml")
    before = render_compose(*overlays, env={"MOOMOO_SIMULATED_ACC_IDS": "123"})
    rendered = render_compose(
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
        pytest.raises(deploy_verify.ConfigError) as failure,
    ):
        deploy_verify.execution_overlays()
    message = str(failure.value)
    assert "MOOMOO_TRADING_MODE" in message
    assert "READ_ONLY" in message and "SIMULATE" in message and "REAL" in message


@pytest.mark.parametrize("paper", [False, True])
def test_production_wrapper_passes_all_real_storage_overlays(tmp_path, paper):
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
    scripts, environment = production_wrapper(tmp_path, model["services"])
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), "up", "-d"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments, _ = json.loads((tmp_path / "docker-call.json").read_text())
    assert "docker-compose.real.yml" in arguments
    assert ("docker-compose.paper.yml" in arguments) is paper
    assert arguments[-2:] == ["up", "-d"]
