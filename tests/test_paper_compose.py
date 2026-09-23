"""The paper overlay persists one SIMULATE journal across deployment modes."""

import pytest
from test_compose_topology import OPEND_DATA_PATH, SERVICE, _render


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_paper_volume_survives_mode_changes_without_moving_gateway(mode):
    base = _render("docker-compose.yml")
    rendered = _render(
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


def test_default_read_only_has_no_execution_volume():
    rendered = _render("docker-compose.yml")
    service = rendered["services"][SERVICE]
    assert service["environment"]["MOOMOO_TRADING_MODE"] == "READ_ONLY"
    assert all(
        item["target"] != "/var/lib/moomoo-mcp/data" for item in service["volumes"]
    )
