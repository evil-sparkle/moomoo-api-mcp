"""Non-secret selection and managed startup, with no deployment daemon access."""

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
        "version": 2,
        "image": "sha256:" + "a" * 64,
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
        (
            "image",
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp:tunnel-latest",
        ),
        ("image", "untrusted.example/moomoo-api-mcp@sha256:" + "a" * 64),
        (
            "image",
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/other@sha256:" + "a" * 64,
        ),
        ("project", "bad project"),
        ("version", 1),
        ("unexpected_field", "synthetic"),
    ],
)
def test_selection_rejects_ambiguous_inputs(field, value):
    data = fixture_selection()
    data[field] = value
    with pytest.raises(ValueError):
        selection.validate(data)


def wrapper_fixture(
    tmp_path,
    *,
    mode="READ_ONLY",
    token="synthetic-only",
    key="synthetic-key",
    tunnel_id="tunnel_synthetic",
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("compose-prod.sh", "tunnel_deployment.py", "deploy_verify.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    selection.save(fixture_selection(), tmp_path / ".chatgpt-deploy.json")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    model = {
        "services": {
            "moomoo-mcp": {
                "environment": {
                    "MCP_AUTH_TOKEN": token,
                    "MOOMOO_TRADING_MODE": mode,
                }
            },
            "chatgpt-tunnel": {
                "environment": {
                    "CHATGPT_TUNNEL_API_KEY": key,
                    "CHATGPT_TUNNEL_ID": tunnel_id,
                }
            },
        }
    }
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport pathlib,sys,os,json\n"
        + "image=os.environ.get('CHATGPT_TUNNEL_IMAGE')\n"
        + "pathlib.Path('docker-call.json').write_text("
        + "json.dumps([sys.argv[1:], image]))\n"
        + "if 'config' in sys.argv: print("
        + repr(json.dumps(model))
        + ")\n"
        + "else: pathlib.Path('started').touch()\n"
    )
    docker.chmod(0o755)
    return scripts, {"PATH": str(fake_bin) + ":" + os.environ["PATH"]}


def test_ecr_image_update_preserves_project(tmp_path, monkeypatch):
    import sys

    selected = tmp_path / "selection.json"
    before = fixture_selection()
    selection.save(before, selected)
    monkeypatch.setattr(selection, "SELECTION", selected)
    # save/load defaults bind the repository path; redirect them for this fixture.
    original_save, original_load = selection.save, selection.load
    monkeypatch.setattr(selection, "save", lambda data: original_save(data, selected))
    monkeypatch.setattr(selection, "load", lambda: original_load(selected))
    image = (
        "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp@sha256:" + "b" * 64
    )
    monkeypatch.setattr(
        sys, "argv", ["tunnel_deployment.py", "set-image", "--image", image]
    )
    assert selection.main() == 0
    assert selection.load() == {**before, "image": image}


@pytest.mark.parametrize("registry_image", [False, True])
def test_wrapper_pulls_selected_images_and_reuses_digest(tmp_path, registry_image):
    scripts, environment = wrapper_fixture(tmp_path)
    selected = fixture_selection()
    if registry_image:
        selected["image"] = (
            "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp@"
            + selected["image"]
        )
    selection.save(selected, tmp_path / ".chatgpt-deploy.json")
    for operation in ("pull", "restart"):
        result = subprocess.run(
            [str(scripts / "compose-prod.sh"), operation],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        args, image = json.loads((tmp_path / "docker-call.json").read_text())
        assert image == selected["image"]
        if operation == "pull":
            assert args[args.index("pull") + 1 :] == ["moomoo-mcp"] + (
                ["chatgpt-tunnel"] if registry_image else []
            )


@pytest.mark.parametrize("operation", ["up", "start", "restart", "run", "create"])
def test_wrapper_allows_explicit_authenticated_read_only_start(tmp_path, operation):
    scripts, environment = wrapper_fixture(tmp_path)
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), operation],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "started").exists()
    assert "synthetic-only" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "options,diagnostic",
    [
        ({"mode": "SIMULATE"}, "READ_ONLY"),
        ({"mode": "REAL"}, "READ_ONLY"),
        ({"token": ""}, "MCP_AUTH_TOKEN"),
        ({"token": "   "}, "MCP_AUTH_TOKEN"),
        ({"key": ""}, "CHATGPT_TUNNEL_API_KEY"),
        ({"tunnel_id": "invalid"}, "CHATGPT_TUNNEL_ID"),
    ],
)
def test_wrapper_rejects_unsafe_start_before_daemon_action(
    tmp_path, options, diagnostic
):
    scripts, environment = wrapper_fixture(tmp_path, **options)
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), "up", "-d"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert diagnostic in result.stdout + result.stderr
    assert not (tmp_path / "started").exists()
    assert "synthetic-only" not in result.stdout + result.stderr


def test_wrapper_rejects_invalid_selection_before_daemon_action(tmp_path):
    scripts, environment = wrapper_fixture(tmp_path)
    selected = fixture_selection()
    selected["image"] = "latest"
    (tmp_path / ".chatgpt-deploy.json").write_text(json.dumps(selected))
    result = subprocess.run(
        [str(scripts / "compose-prod.sh"), "up"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Invalid tunnel selection" in result.stdout + result.stderr
    assert not (tmp_path / "started").exists()


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
