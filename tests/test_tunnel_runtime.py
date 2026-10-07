"""Deterministic manager failure tests supplement (never replace) real-client tests."""

import importlib.util
import signal
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import private_chatgpt_preflight as preflight

ROOT = Path(__file__).resolve().parents[1]
sys.modules["private_chatgpt_preflight"] = preflight
spec = importlib.util.spec_from_file_location(
    "tunnel_runtime", ROOT / "deploy/tunnel-client/container/runtime.py"
)
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def authorization(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "synthetic-mcp")
    monkeypatch.setenv("CHATGPT_TUNNEL_API_KEY", "synthetic-runtime-key")
    monkeypatch.setenv("CHATGPT_TUNNEL_ID", "tunnel_synthetic")


def test_shared_token_normalization_matches_mcp_settings(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "  synthetic-mcp  ")
    assert runtime.authorization_header() == "Bearer synthetic-mcp"


@pytest.mark.usefixtures("authorization")
@pytest.mark.parametrize("mode", ["SIMULATE", "REAL", None])
def test_fatal_mode_never_spawns_client(monkeypatch, mode):
    client = Mock()
    client.call_tool.return_value = {"trading_mode": mode}
    monkeypatch.setattr(runtime, "McpClient", Mock(return_value=client))
    spawn = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert runtime.gate(threading.Event()) is False
    spawn.assert_not_called()


@pytest.mark.usefixtures("authorization")
def test_degraded_read_only_is_valid(monkeypatch):
    client = Mock()
    client.call_tool.return_value = {"trading_mode": "READ_ONLY", "status": "degraded"}
    monkeypatch.setattr(runtime, "McpClient", Mock(return_value=client))
    assert runtime.gate(threading.Event())
    client.initialize.assert_called_once()
    client.list_tools.assert_called_once()
    client.call_tool.assert_called_once_with("check_health", {})
    runtime.McpClient.assert_called_once_with(
        preflight.COMPOSE_URL,
        "Bearer synthetic-mcp",
        timeout=10,
        allow_compose_mcp=True,
    )


@pytest.mark.usefixtures("authorization")
@pytest.mark.parametrize(
    "message", ["HTTP 401", "HTTP 403", "HTTP 421", "malformed JSON"]
)
def test_fatal_errors_do_not_retry(monkeypatch, message):
    client = Mock()
    client.initialize.side_effect = preflight.PreflightError(message)
    monkeypatch.setattr(runtime, "McpClient", Mock(return_value=client))
    event = Mock()
    event.is_set.return_value = False
    assert not runtime.gate(event)
    event.wait.assert_not_called()


@pytest.mark.usefixtures("authorization")
def test_deadline_bounds_request_timeout(monkeypatch):
    client = Mock()
    client.initialize.side_effect = preflight.PreflightError(
        "unreachable", transient=True
    )
    monkeypatch.setattr(runtime, "McpClient", Mock(return_value=client))
    monkeypatch.setattr(runtime.time, "monotonic", Mock(side_effect=[0, 89.5, 90.1]))
    assert not runtime.gate(threading.Event())
    assert client.timeout == 0.5


def test_signal_forwarding_and_reaping():
    # A disposable Python child is limited to deterministic signal behavior.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    runtime.stop_child(child, signal.SIGTERM)
    assert child.returncode == -signal.SIGTERM


@pytest.mark.usefixtures("authorization")
def test_child_environment_discards_proxy_and_credential_overrides(monkeypatch):
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "CONTROL_PLANE_API_KEY",
        "MCP_EXTRA_HEADERS",
        "MOOMOO_LOGIN_ACCOUNT",
        "MCP_OPERATOR_TOKEN",
        "MCP_AUTHORIZATION",
    ):
        monkeypatch.setenv(key, "synthetic-must-not-inherit")
    environment = runtime.child_environment()
    assert set(environment) == {
        "HOME",
        "PATH",
        "CONTROL_PLANE_TUNNEL_ID",
        "CONTROL_PLANE_API_KEY",
        "MCP_AUTHORIZATION",
    }
    assert environment["CONTROL_PLANE_API_KEY"] == "synthetic-runtime-key"
    assert environment["CONTROL_PLANE_TUNNEL_ID"] == "tunnel_synthetic"
    assert environment["MCP_AUTHORIZATION"] == "Bearer synthetic-mcp"
    assert "synthetic-must-not-inherit" not in environment.values()


@pytest.mark.parametrize(
    "token", [None, "", " ", "synthetic\r\ninjection", "bad\ttoken", "bad\u00e9token"]
)
def test_invalid_deployment_token_never_connects_or_spawns(monkeypatch, capsys, token):
    if token is None:
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MCP_AUTH_TOKEN", token)
    connect, spawn = Mock(), Mock()
    monkeypatch.setattr(runtime, "McpClient", connect)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert not runtime.gate(threading.Event())
    with pytest.raises(preflight.PreflightError, match="MCP_AUTH_TOKEN"):
        runtime.authorization_header()
    connect.assert_not_called()
    spawn.assert_not_called()
    assert "synthetic" not in capsys.readouterr().out


def test_unapproved_config_is_rejected_before_credentials(tmp_path, monkeypatch):
    import hashlib

    config = tmp_path / "public-config"
    digest = tmp_path / "digest"
    config.write_text("approved public fixture")
    digest.write_text(hashlib.sha256(config.read_bytes()).hexdigest())
    monkeypatch.setattr(runtime, "CONFIG", str(config))
    monkeypatch.setattr(runtime, "CONFIG_DIGEST", digest)
    runtime.verify_config()
    config.write_text("unapproved destination")
    with pytest.raises(ValueError, match="unapproved"):
        runtime.verify_config()


def test_main_never_launches_child_when_authenticated_gate_fails(monkeypatch):
    monkeypatch.setattr(runtime, "verify_config", Mock())
    monkeypatch.setattr(runtime, "child_environment", Mock(return_value={}))
    monkeypatch.setattr(runtime, "gate", Mock(return_value=False))
    monkeypatch.setattr(runtime.sys, "argv", ["runtime.py"])
    spawn = Mock()
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert runtime.main() == 1
    spawn.assert_not_called()


@pytest.mark.usefixtures("authorization")
@pytest.mark.parametrize(
    "variable,value",
    [
        ("CHATGPT_TUNNEL_API_KEY", ""),
        ("CHATGPT_TUNNEL_API_KEY", "synthetic\r\ninjection"),
        ("CHATGPT_TUNNEL_ID", ""),
        ("CHATGPT_TUNNEL_ID", "https://unexpected.invalid"),
    ],
)
def test_invalid_openai_settings_never_connect_or_spawn(
    monkeypatch, capsys, variable, value
):
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(runtime, "verify_config", Mock())
    monkeypatch.setattr(runtime.sys, "argv", ["runtime.py"])
    connect, spawn = Mock(), Mock()
    monkeypatch.setattr(runtime, "McpClient", connect)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert runtime.main() == 1
    connect.assert_not_called()
    spawn.assert_not_called()
    assert "synthetic" not in capsys.readouterr().out
