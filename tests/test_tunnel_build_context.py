"""Build preparation must transmit only enumerated public inputs."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("local_build", [True, False])
def test_build_context_excludes_synthetic_secret_canaries(tmp_path, local_build):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    assets = repo / "deploy/tunnel-client"
    (assets / "container").mkdir(parents=True)
    for name in ("Dockerfile", "runtime.py", "tunnel-client.yaml"):
        (assets / "container" / name).write_text("public fixture")
    for name in ("install.py", "release.json"):
        (assets / name).write_text("public fixture")
    (repo / "scripts/private_chatgpt_preflight.py").write_text("public fixture")
    for name in ("build-tunnel-image.sh", "prepare-tunnel-build-context.sh"):
        shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
    (repo / ".env").write_text("SYNTHETIC_SECRET_CANARY=never-send-this")
    (repo / "private-credential").write_text("never-send-this")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "git").write_text(
        "#!/bin/sh\necho 0000000000000000000000000000000000000000\n"
    )
    (binaries / "docker").write_text("""#!/usr/bin/python3
import json,pathlib,sys,os
context=pathlib.Path(sys.argv[-1])
files=sorted(p.name for p in context.iterdir())
assert all('never-send-this' not in p.read_text() for p in context.iterdir())
pathlib.Path(os.environ['CONTEXT_RESULT']).write_text(json.dumps(files))
""")
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    result = tmp_path / "result.json"
    context = tmp_path / "ci-context"
    command = [str(repo / "scripts/build-tunnel-image.sh")]
    if not local_build:
        command = [str(repo / "scripts/prepare-tunnel-build-context.sh"), str(context)]
    completed = subprocess.run(
        command,
        check=True,
        env={"PATH": str(binaries) + ":/usr/bin:/bin", "CONTEXT_RESULT": str(result)},
        capture_output=True,
        text=True,
    )
    if not local_build:
        assert re.fullmatch(r"[0-9a-f]{64}\n", completed.stdout)
        assert not result.exists()  # CI preparation never invokes Docker.
        result.write_text(json.dumps(sorted(p.name for p in context.iterdir())))
        assert all("never-send-this" not in p.read_text() for p in context.iterdir())
    assert json.loads(result.read_text()) == sorted(
        [
            "Dockerfile",
            "runtime.py",
            "tunnel-client.yaml",
            "install.py",
            "release.json",
            "private_chatgpt_preflight.py",
        ]
    )


def test_ci_preparation_refuses_a_context_with_existing_files(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (context / "unlisted-file").write_text("synthetic-canary")
    result = subprocess.run(
        [str(ROOT / "scripts/prepare-tunnel-build-context.sh"), str(context)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert [p.name for p in context.iterdir()] == ["unlisted-file"]
