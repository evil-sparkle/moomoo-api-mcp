"""The paper overlay persists one SIMULATE journal across deployment modes."""

import pytest

from tests.compose_helpers import OPEND_DATA_PATH, SERVICE, render_compose


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_paper_volume_survives_mode_changes_without_moving_gateway(mode):
    base = render_compose("docker-compose.yml")
    rendered = render_compose(
        "docker-compose.yml",
        "docker-compose.paper.yml",
        env={"MOOMOO_TRADING_MODE": mode, "MOOMOO_SIMULATED_ACC_IDS": "123"},
    )
    service = rendered["services"][SERVICE]
    mounts = {item["target"]: item for item in service["volumes"]}
    original = {item["target"]: item for item in base["services"][SERVICE]["volumes"]}
    assert mounts[OPEND_DATA_PATH] == original[OPEND_DATA_PATH]
    assert mounts["/var/lib/moomoo-mcp/data"]["source"] == "execution-data"
    assert mounts["/var/lib/moomoo-mcp/data"]["type"] == "volume"
    assert service["environment"]["MOOMOO_TRADING_MODE"] == mode
    assert service["environment"]["MOOMOO_CREATE_JOURNAL"] == "0"
    assert service["environment"]["MOOMOO_SIMULATED_ACC_IDS"] == "123"


def test_default_read_only_retains_execution_volumes_without_enabling_writes():
    rendered = render_compose("docker-compose.yml")
    service = rendered["services"][SERVICE]
    assert service["environment"]["MOOMOO_TRADING_MODE"] == "READ_ONLY"
    targets = {item["target"] for item in service["volumes"]}
    assert {
        "/var/lib/moomoo-mcp/data",
        "/app/real-execution-data",
        "/var/lib/moomoo/trading-controls",
    } <= targets
