"""Non-secret selection and release refusal, with no deployment daemon access."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import tunnel_deployment as selection

ROOT = Path(__file__).resolve().parents[1]


def fixture_selection():
    return {
        "version": 1,
        "image": "sha256:" + "a" * 64,
        "secret_directory": "/synthetic/protected",
        "project": "existing-project",
    }


def test_atomic_selection_preserves_identity(tmp_path):
    path = tmp_path / "selection.json"
    selection.save(fixture_selection(), path)
    assert selection.load(path) == fixture_selection()
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".chatgpt-selection-*"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "latest"),
        ("project", "bad project"),
        ("secret_directory", "/tmp/../credentials"),
        ("secret_directory", "/tmp/path\nextra"),
    ],
)
def test_selection_rejects_ambiguous_inputs(field, value):
    data = fixture_selection()
    data[field] = value
    with pytest.raises(ValueError):
        selection.validate(data)


@pytest.mark.parametrize("operation", ["up", "start", "restart", "run", "create"])
def test_production_wrapper_refuses_known_failing_pin(tmp_path, operation):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("compose-prod.sh", "tunnel_deployment.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    (tmp_path / ".chatgpt-deploy.json").write_text(json.dumps(fixture_selection()))
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), operation],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "RELEASE BLOCKED" in result.stdout
    assert "synthetic/protected" not in result.stdout


@pytest.mark.parametrize("codes", [(0, 1), (3, 0), (1, 1), (3, 2)])
def test_legacy_active_enabled_or_unknown_refuses_start(monkeypatch, codes):
    from unittest.mock import Mock

    monkeypatch.setattr(
        selection.subprocess,
        "run",
        Mock(side_effect=[subprocess.CompletedProcess([], code) for code in codes]),
    )
    with pytest.raises(ValueError, match="legacy"):
        selection.check_legacy_inactive()


def test_legacy_inactive_and_disabled_can_be_confirmed_without_starting_it(monkeypatch):
    from unittest.mock import Mock

    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 3),
            subprocess.CompletedProcess([], 1),
        ]
    )
    monkeypatch.setattr(selection.subprocess, "run", runner)
    selection.check_legacy_inactive()
    assert [call.args[0][1] for call in runner.call_args_list] == [
        "is-active",
        "is-enabled",
    ]


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_resolved_selected_tunnel_refuses_non_read_only(monkeypatch, mode):
    from unittest.mock import Mock

    from scripts import deploy_verify

    model = {
        "services": {
            "moomoo-mcp": {
                "environment": {
                    "MCP_AUTH_TOKEN": "synthetic",
                    "MOOMOO_TRADING_MODE": mode,
                }
            },
            "chatgpt-tunnel": {},
        }
    }
    monkeypatch.setattr(
        deploy_verify.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, stdout=json.dumps(model).encode()
            )
        ),
    )
    with pytest.raises(deploy_verify.ConfigError, match="READ_ONLY"):
        deploy_verify.resolve_token(["fixture-only"])


def test_disable_only_stops_and_removes_tunnel_then_clears_selection(
    tmp_path, monkeypatch
):
    import sys
    from unittest.mock import Mock

    selected = tmp_path / ".chatgpt-deploy.json"
    selection.save(fixture_selection(), selected)
    marker = tmp_path / "synthetic-brokerage-state"
    marker.write_text("preserve")
    monkeypatch.setattr(selection, "SELECTION", selected)
    monkeypatch.setattr(selection, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["tunnel_deployment.py", "disable"])
    runner = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(selection.subprocess, "run", runner)
    assert selection.main() == 0
    assert [call.args[0][1:] for call in runner.call_args_list] == [
        ["stop", "chatgpt-tunnel"],
        ["rm", "-f", "chatgpt-tunnel"],
    ]
    assert not selected.exists()
    assert marker.read_text() == "preserve"


@pytest.mark.parametrize("mode", ["SIMULATE", "REAL"])
def test_future_approved_wrapper_checks_mode_before_start(tmp_path, mode):
    # Only disposable script copies simulate a cleared gate. Production stays false.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("compose-prod.sh", "tunnel_deployment.py", "deploy_verify.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    helper = scripts / "tunnel_deployment.py"
    helper.write_text(
        helper.read_text().replace(
            "RELEASE_GATE_PASSED = False", "RELEASE_GATE_PASSED = True"
        )
    )
    selection.save(fixture_selection(), tmp_path / ".chatgpt-deploy.json")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text('#!/bin/sh\n[ "$1" = is-active ] && exit 3\nexit 1\n')
    systemctl.chmod(0o755)
    model = {
        "services": {
            "moomoo-mcp": {
                "environment": {
                    "MCP_AUTH_TOKEN": "synthetic-only",
                    "MOOMOO_TRADING_MODE": mode,
                }
            },
            "chatgpt-tunnel": {},
        }
    }
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys\n"
        + "if 'config' in sys.argv: print("
        + repr(json.dumps(model))
        + ")\n"
        + "else: pathlib.Path('unexpected-start').touch()\n"
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), "up", "-d"],
        cwd=tmp_path,
        env={"PATH": str(fake_bin) + ":" + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "READ_ONLY" in result.stdout + result.stderr
    assert not (tmp_path / "unexpected-start").exists()
    assert "synthetic-only" not in result.stdout + result.stderr
