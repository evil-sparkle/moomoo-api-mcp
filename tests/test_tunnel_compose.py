"""Render only public Compose files with explicit synthetic configuration."""

import pytest

from tests.test_compose_topology import PROD_ENV, _render


@pytest.mark.parametrize("mode", ["READ_ONLY", "SIMULATE"])
def test_managed_paper_tunnel_preserves_storage_and_isolates_operator(mode):
    env = {
        **PROD_ENV,
        "MOOMOO_TRADING_MODE": mode,
        "MOOMOO_SIMULATED_ACC_IDS": "123",
        "MCP_AUTH_TOKEN": "synthetic-ordinary-token",
        "MCP_OPERATOR_TOKEN": "synthetic-operator-token",
        "CHATGPT_TUNNEL_IMAGE": "sha256:" + "a" * 64,
        "CHATGPT_TUNNEL_API_KEY": "synthetic-runtime-key",
        "CHATGPT_TUNNEL_ID": "tunnel_synthetic",
    }
    model = _render(
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.paper.yml",
        "docker-compose.chatgpt.yml",
        env=env,
    )
    broker = model["services"]["moomoo-mcp"]
    journal = next(
        mount
        for mount in broker["volumes"]
        if mount["target"] == "/var/lib/moomoo-mcp/data"
    )
    assert journal["source"] == "execution-data"
    assert broker["environment"]["MOOMOO_TRADING_MODE"] == mode
    assert broker["environment"]["MOOMOO_SIMULATED_ACC_IDS"] == "123"
    assert broker["environment"]["MOOMOO_CREATE_JOURNAL"] == "0"
    assert broker["environment"]["MCP_OPERATOR_TOKEN"] == "synthetic-operator-token"
    tunnel = model["services"]["chatgpt-tunnel"]
    assert not tunnel.get("volumes")
    assert "MCP_OPERATOR_TOKEN" not in tunnel["environment"]
    assert "synthetic-operator-token" not in tunnel["environment"].values()


@pytest.mark.parametrize(
    "overlays",
    [
        [],
        ["docker-compose.prod.yml"],
        ["docker-compose.paper.yml"],
        ["docker-compose.smoke.yml"],
    ],
)
def test_default_off_and_enabled_boundaries(overlays):
    env = {
        **PROD_ENV,
        "CHATGPT_TUNNEL_IMAGE": "sha256:" + "a" * 64,
        "CHATGPT_TUNNEL_API_KEY": "synthetic-runtime-key",
        "CHATGPT_TUNNEL_ID": "tunnel_synthetic",
        "MCP_AUTH_TOKEN": "synthetic-shared-mcp-token",
    }
    default = _render("docker-compose.yml", *overlays, env=PROD_ENV)
    assert set(default["services"]) == {"moomoo-mcp"}
    enabled = _render(
        "docker-compose.yml", *overlays, "docker-compose.chatgpt.yml", env=env
    )
    assert set(enabled["services"]) == {"moomoo-mcp", "chatgpt-tunnel"}
    broker = enabled["services"]["moomoo-mcp"]
    assert broker["ports"] == default["services"]["moomoo-mcp"]["ports"]
    assert broker["environment"]["MOOMOO_OPEND_HOST"] == "127.0.0.1"
    assert broker["environment"]["MOOMOO_OPEND_PORT"] == "11111"
    assert broker["environment"]["MCP_ALLOW_CHATGPT_TUNNEL_HOST"] == "1"
    tunnel = enabled["services"]["chatgpt-tunnel"]
    assert tunnel["user"] == "10002:10002"
    assert tunnel["read_only"] is True
    assert tunnel["cap_drop"] == ["ALL"]
    assert tunnel["security_opt"] == ["no-new-privileges:true"]
    assert not tunnel.get("ports")
    assert not tunnel.get("privileged")
    assert "network_mode" not in tunnel and "pid" not in tunnel
    assert tunnel["environment"] == {
        "MCP_AUTH_TOKEN": broker["environment"]["MCP_AUTH_TOKEN"],
        "CHATGPT_TUNNEL_API_KEY": env["CHATGPT_TUNNEL_API_KEY"],
        "CHATGPT_TUNNEL_ID": env["CHATGPT_TUNNEL_ID"],
    }
    assert tunnel["environment"]["MCP_AUTH_TOKEN"] == env["MCP_AUTH_TOKEN"]
    assert not tunnel.get("volumes")
    assert enabled["networks"]["default"].get("internal", False) is False
