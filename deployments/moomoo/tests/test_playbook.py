"""Execute the real Ansible tasks against disposable files and command stubs."""

import json
import os
import shutil
import signal
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
            "docker-compose.paper.yml",
            "docker-compose.real.yml",
            "docker-compose.controls.yml",
            "scripts/deployment_lock.py",
            "scripts/deployment_manifest.py",
            "scripts/ansible_deployment.py",
            "scripts/deployment_probe.py",
        ]:
            destination = self.source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, destination)
        self.run_command(["git", "init", "-q"], self.source)
        self.run_command(["git", "add", "."], self.source)
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
            "ANSIBLE_PIPELINING": "true",
            "ANSIBLE_FORKS": "1",
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
        with subprocess.Popen(
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                output, error = process.communicate(timeout=240)
            except subprocess.TimeoutExpired:
                # Stop module workers before their disposable command stubs disappear.
                os.killpg(process.pid, signal.SIGKILL)
                output, error = process.communicate()
                raise subprocess.TimeoutExpired(
                    process.args, 240, output=output, stderr=error
                ) from None
            return subprocess.CompletedProcess(
                process.args, process.returncode, output, error
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

    def test_configuration_changes_create_a_new_snapshot_without_rewriting_rollback(
        self,
    ):
        first = self.deploy()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        current = self.server / ".local/share/moomoo-api-mcp/current"
        previous = current.resolve()
        frozen = (previous / "resolved-compose.json").read_text()
        self.env.write_text(
            "MCP_AUTH_TOKEN=new-synthetic-token\nMOOMOO_TRADING_MODE=READ_ONLY\n"
        )
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(current.resolve(), previous)
        self.assertEqual((previous / "resolved-compose.json").read_text(), frozen)
        self.assertIn(
            "new-synthetic-token", (current / "resolved-compose.json").read_text()
        )

    def test_mode_transitions_preserve_journals_and_disable_the_selected_tunnel(self):
        self.env.write_text(
            "MCP_AUTH_TOKEN=synthetic-test-token\nMOOMOO_TRADING_MODE=SIMULATE\n"
            "MOOMOO_SIMULATED_ACC_IDS=123\nMOOMOO_CREATE_JOURNAL=1\n"
            "MCP_RESTRICTED_AUTH_TOKEN=synthetic-restricted\n"
            "CHATGPT_TUNNEL_API_KEY=synthetic-key\nCHATGPT_TUNNEL_ID=tunnel_synthetic\n"
        )
        first = self.deploy(
            selection=self.selection
            | {
                "moomoo_mcp_expected_mode": "SIMULATE",
                "moomoo_mcp_tunnel_enabled": True,
            }
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue(json.loads(self.state.read_text())["tunnel"])
        with self.env.open("a") as stream:
            stream.write(
                "MOOMOO_TRADING_MODE=REAL\nMOOMOO_REAL_ACC_IDS=456\nMOOMOO_CREATE_REAL_JOURNAL=1\nMOOMOO_REQUIRE_TRADE_APPROVAL=1\nMOOMOO_CREATE_TRADING_CONTROLS=1\n"
            )
        live = self.deploy(
            selection=self.selection | {"moomoo_mcp_expected_mode": "REAL"}
        )
        self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
        state = json.loads(self.state.read_text())
        self.assertFalse(state["tunnel"])
        expected = {mount["Destination"]: mount["Name"] for mount in state["mounts"]}
        self.assertEqual(
            expected["/var/lib/moomoo-mcp/data"], "original-project_execution-data"
        )
        self.assertEqual(
            expected["/app/real-execution-data"], "original-project_real-execution-data"
        )
        self.assertEqual(
            expected["/var/lib/moomoo/trading-controls"],
            "original-project_trading-controls-data",
        )
        with self.env.open("a") as stream:
            stream.write("MOOMOO_TRADING_MODE=READ_ONLY\n")
        readonly = self.deploy()
        self.assertEqual(readonly.returncode, 0, readonly.stdout + readonly.stderr)
        actual = {
            mount["Destination"]: mount["Name"]
            for mount in json.loads(self.state.read_text())["mounts"]
        }
        self.assertEqual(actual, expected)

    def test_invalid_candidate_is_refused_before_runtime_mutation(self):
        with self.env.open("a") as stream:
            stream.write("SYNTHETIC_INVALID_SETTINGS=1\n")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertFalse(any("up" in call for call in calls))
        self.assertNotIn(
            "synthetic-secret-must-not-escape", result.stdout + result.stderr
        )

    def test_runtime_lifecycle_cannot_interleave_with_a_release_owner(self):
        first = self.deploy()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        root = self.server / ".local/share/moomoo-api-mcp"
        lock = root / ".deployment-lock"
        lock.mkdir()
        (lock / "owner.json").write_text(json.dumps({"token": "synthetic-other-owner"}))
        original = self.log.read_text()
        for arguments in [
            ["compose", "stop"],
            ["compose", "restart"],
            ["compose", "up", "-d"],
        ]:
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "current/scripts/ansible_deployment.py"),
                    *arguments,
                ],
                env=self.environment | {"MOOMOO_DEPLOY_LOCK_ROOT": str(root)},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
        blocked = self.deploy()
        self.assertNotEqual(blocked.returncode, 0)
        calls = [
            json.loads(line)
            for line in self.log.read_text().removeprefix(original).splitlines()
        ]
        self.assertFalse(
            any("up" in call or "stop" in call or "restart" in call for call in calls)
        )
        self.assertEqual(
            json.loads((lock / "owner.json").read_text())["token"],
            "synthetic-other-owner",
        )
