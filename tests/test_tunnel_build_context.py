"""Build preparation must transmit only enumerated public inputs."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_build_context_excludes_synthetic_secret_canaries(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    assets = repo / "deploy/tunnel-client"
    (assets / "container").mkdir(parents=True)
    for name in ("Dockerfile", "runtime.py", "tunnel-client.yaml"):
        (assets / "container" / name).write_text("public fixture")
    for name in ("install.py", "release.json"):
        (assets / name).write_text("public fixture")
    (repo / "scripts/private_chatgpt_preflight.py").write_text("public fixture")
    shutil.copy2(
        ROOT / "scripts/build-tunnel-image.sh", repo / "scripts/build-tunnel-image.sh"
    )
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
    subprocess.run(
        [str(repo / "scripts/build-tunnel-image.sh")],
        check=True,
        env={"PATH": str(binaries) + ":/usr/bin:/bin", "CONTEXT_RESULT": str(result)},
        capture_output=True,
        text=True,
    )
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
