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
