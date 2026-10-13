"""Execute the real Ansible tasks against disposable files and command stubs."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp@sha256:" + "a" * 64
SECOND = IMAGE.replace("a" * 64, "d" * 64)
TUNNEL = IMAGE.replace("a" * 64, "c" * 64)


@unittest.skipUnless(shutil.which("ansible-playbook"), "Ansible is unavailable")
class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in [
            "docker-compose.yml",
            "scripts/deployment_manifest.py",
            "scripts/ansible_deployment.py",
            "scripts/deployment_probe.py",
        ]:
            destination = self.source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, destination)
        self.run_command(["git", "init", "-q"], self.source)
        self.run_command(["git", "add", "docker-compose.yml", "scripts"], self.source)
        self.run_command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Synthetic Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "Synthetic public deployment",
            ],
            self.source,
        )
        self.revision = self.run_command(
            ["git", "rev-parse", "HEAD"], self.source
        ).stdout.strip()
        self.server = self.root / "server"
        self.legacy = self.server / "moomoo"
        (self.legacy / "scripts").mkdir(parents=True)
        self.env = self.legacy / ".env"
        self.env.write_text(
            "MCP_AUTH_TOKEN=synthetic-test-token\nMOOMOO_TRADING_MODE=READ_ONLY\n"
        )
        self.env.chmod(0o600)
        legacy = self.legacy / "scripts/compose-prod.sh"
        legacy.write_text(
            "#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n"
            "Path(os.environ['SYNTHETIC_LEGACY_RESTORED']).write_text('restored')\n"
        )
        legacy.chmod(0o755)
        self.binaries = self.root / "bin"
        self.binaries.mkdir()
        shutil.copy(
            ROOT / "deployments/moomoo/tests/docker-stub.py", self.binaries / "docker"
        )
        (self.binaries / "docker").chmod(0o755)
        (self.binaries / "uname").write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
        (self.binaries / "uname").chmod(0o755)
        self.state = self.root / "docker-state.json"
        self.state.write_text(json.dumps({"image": IMAGE}))
        self.log = self.root / "docker-log.jsonl"
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(
            json.dumps(
                {
                    "vps": {
                        "hosts": {
                            "vultr": {
                                "ansible_connection": "local",
                                "ansible_user": "synthetic-user",
                                "ansible_python_interpreter": sys.executable,
                            }
                        }
                    }
                }
            )
        )
        self.selection = {
            "moomoo_mcp_home": str(self.server),
            "moomoo_mcp_source_dir": str(self.source),
            "moomoo_mcp_revision": self.revision,
            "moomoo_mcp_project": "original-project",
            "moomoo_mcp_env_file": str(self.env),
            "moomoo_mcp_image": IMAGE,
            "moomoo_mcp_tunnel_image": TUNNEL,
        }
        self.environment = {
            **os.environ,
            "PATH": str(self.binaries) + os.pathsep + os.environ["PATH"],
            "ANSIBLE_LOCAL_TEMP": str(self.root / "controller-tmp"),
            "ANSIBLE_REMOTE_TEMP": str(self.root / "remote-tmp"),
            "SYNTHETIC_DOCKER_STATE": str(self.state),
            "SYNTHETIC_DOCKER_LOG": str(self.log),
            "SYNTHETIC_LEGACY_RESTORED": str(self.root / "legacy-restored"),
        }

    @staticmethod
    def run_command(arguments, directory):
        return subprocess.run(
            arguments, cwd=directory, check=True, capture_output=True, text=True
        )

    def deploy(self, *, fail_image="", selection=None):
        inputs = self.root / "selection.json"
        inputs.write_text(json.dumps(selection or self.selection))
        return subprocess.run(
            [
                "ansible-playbook",
                "deploy.yml",
                "--limit",
                "vultr",
                "--skip-tags",
                "startup",
                "-i",
                str(self.inventory),
                "-e",
                "@" + str(inputs),
            ],
            cwd=ROOT / "deployments/moomoo",
            env=self.environment | {"SYNTHETIC_FAIL_IMAGE": fail_image},
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )

    def test_migration_preserves_credentials_state_and_selects_verified_release(self):
        original = self.env.read_text()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        current = self.server / ".local/share/moomoo-api-mcp/current"
        self.assertTrue(current.is_symlink())
        metadata = json.loads((current / "state-volumes.json").read_text())
        self.assertEqual(metadata["opend-state"]["name"], "original-project_opend-data")
        self.assertEqual(
            metadata["paper-state"]["name"], "original-project_execution-data"
        )
        self.assertEqual(self.env.read_text(), original)
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
        self.assertFalse((current / ".env").exists())
        self.assertFalse((current.parent / ".deployment-lock").exists())
        self.assertNotIn("synthetic-test-token", result.stdout + result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertFalse(any("down" in call for call in calls))

    def test_failed_first_migration_restores_legacy_and_leaves_no_current_release(self):
        result = self.deploy(fail_image=IMAGE)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "legacy-restored").exists())
        self.assertFalse((self.server / ".local/share/moomoo-api-mcp/current").exists())
        self.assertNotIn(
            "synthetic-secret-must-not-escape", result.stdout + result.stderr
        )

    def test_failed_upgrade_restores_previous_digest_and_current_link(self):
        first = self.deploy()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        current = self.server / ".local/share/moomoo-api-mcp/current"
        previous = current.resolve()
        result = self.deploy(
            fail_image=SECOND, selection=self.selection | {"moomoo_mcp_image": SECOND}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(current.resolve(), previous)
        self.assertEqual(json.loads(self.state.read_text())["image"], IMAGE)
        self.assertNotIn(
            "synthetic-secret-must-not-escape", result.stdout + result.stderr
        )

    def test_existing_release_cannot_be_overwritten_with_different_public_settings(
        self,
    ):
        first = self.deploy()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        result = self.deploy(
            selection=self.selection
            | {"moomoo_mcp_env_file": str(self.root / "different-synthetic.env")}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different public settings", result.stdout)
