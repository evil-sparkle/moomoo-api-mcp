"""Isolated Compose rendering shared by deployment contract tests."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = "moomoo-mcp"
OPEND_DATA_PATH = "/home/opend/.com.moomoo.OpenD"

# Any value: the prod overlay only requires that these are set (`${VAR:?}`).
PROD_ENV = {
    "ECR_REGISTRY": "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com",
    "IMAGE_TAG": "0000000",
}


def compose_command(env: dict[str, str]) -> list[str]:
    """Find Compose without loading the user's Docker configuration."""
    commands = []
    standalone = shutil.which("docker-compose")
    if standalone:
        commands.append([standalone])
    # Resolve the user plugin executable before isolating HOME/DOCKER_CONFIG.
    # Only the executable is accessed; config.json is never read or copied.
    config = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
    plugin = config / "cli-plugins" / "docker-compose"
    if plugin.is_file() and os.access(plugin, os.X_OK):
        commands.append([str(plugin)])
    docker = shutil.which("docker")
    if docker:
        commands.append([docker, "compose"])
    for command in commands:
        probe = subprocess.run(
            [*command, "version", "--short"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
        )
        major = probe.stdout.strip().lstrip("v").split(".", 1)[0]
        if probe.returncode == 0 and major.isdecimal() and int(major) >= 2:
            return command
    message = "Docker Compose v2 or newer is unavailable in the isolated environment"
    if os.environ.get("CI"):
        raise AssertionError(message)
    raise unittest.SkipTest(message)


def render_compose(
    *overlays: str,
    env: dict[str, str] | None = None,
    env_file: Path | None = None,
) -> dict:
    """Return the configuration Compose actually resolves for these files."""
    files: list[str] = []
    for overlay in overlays:
        files += ["-f", overlay]
    with tempfile.TemporaryDirectory(prefix="moomoo-compose-config-") as docker_config:
        isolated_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(ROOT),
            "DOCKER_CONFIG": docker_config,
            **(env or {}),
        }
        command = compose_command(isolated_env)
        result = subprocess.run(
            # An empty env file and isolated HOME/DOCKER_CONFIG keep these
            # topology checks independent of private local configuration.
            [
                *command,
                "--env-file",
                str(env_file) if env_file is not None else os.devnull,
                *files,
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=isolated_env,
        )
    if result.returncode != 0:
        raise AssertionError(
            f"docker compose config failed for {files}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def production_wrapper(tmp_path: Path, services: dict):
    """Copy public scripts and record synthetic Docker calls without a daemon."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in (
        "compose-prod.sh",
        "deploy_verify.py",
        "tunnel_deployment.py",
        "deployment_manifest.py",
        "deployment_lock.py",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (tmp_path / "compose-model.json").write_text(json.dumps({"services": services}))
    docker = fake_bin / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

pathlib.Path('docker-call.json').write_text(
    json.dumps([sys.argv[1:], os.environ.get('CHATGPT_TUNNEL_IMAGE')])
)
if 'config' in sys.argv:
    pathlib.Path('config-seen').touch()
    print(pathlib.Path('compose-model.json').read_text())
else:
    pathlib.Path('started').touch()
""")
    docker.chmod(0o755)
    return scripts, {
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "MOOMOO_DEPLOY_LOCK_ROOT": str(tmp_path / "deployment-state"),
    }
