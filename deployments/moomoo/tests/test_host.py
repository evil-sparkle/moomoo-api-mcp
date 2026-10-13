"""Exercise native deployment boundaries with synthetic configuration only."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import ansible_deployment as host  # noqa: E402

IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp@sha256:" + "a" * 64
TUNNEL = IMAGE.replace("a" * 64, "c" * 64)


def selection(env_file):
    return {
        "revision": "b" * 40,
        "image": IMAGE,
        "tunnel_image": TUNNEL,
        "project": "original-project",
        "env_file": str(env_file),
        "expected_mode": "READ_ONLY",
        "tunnel_enabled": False,
    }


def existing():
    return {
        "labels": {
            "com.docker.compose.project": "original-project",
            "com.docker.compose.service": "moomoo-mcp",
        },
        "mounts": [
            {
                "Type": "volume",
                "Name": "original-project_opend-data",
                "Destination": "/home/opend/.com.moomoo.OpenD",
            },
            {
                "Type": "volume",
                "Name": "original-project_execution-data",
                "Destination": "/var/lib/moomoo-mcp/data",
            },
        ],
    }


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = self.root / "synthetic.env"
        self.env.write_text(
            "MCP_AUTH_TOKEN=synthetic-test-token\nMOOMOO_TRADING_MODE=READ_ONLY\n"
        )
        self.env.chmod(0o600)
        self.value = selection(self.env)
        (self.root / "deployment.json").write_text(json.dumps(self.value))
        shutil.copy(ROOT / "docker-compose.yml", self.root / "docker-compose.yml")

    def test_prepare_preserves_named_state_and_never_reads_credential_file(self):
        original_read = Path.read_text

        def public_only(path, *args, **kwargs):
            self.assertNotEqual(path, self.env)
            return original_read(path, *args, **kwargs)

        with (
            patch.object(host, "execute", side_effect=[json.dumps(existing()), ""]),
            patch.object(host, "validate_configuration"),
            patch.object(Path, "read_text", public_only),
        ):
            host.prepare(self.root, self.value)
        saved = json.loads((self.root / "state-volumes.json").read_text())
        self.assertEqual(
            saved["opend-state"],
            {"external": True, "name": "original-project_opend-data"},
        )
        self.assertEqual(
            saved["paper-state"]["name"], "original-project_execution-data"
        )
        self.assertEqual(
            self.env.read_text(),
            "MCP_AUTH_TOKEN=synthetic-test-token\nMOOMOO_TRADING_MODE=READ_ONLY\n",
        )

    def test_refuses_wrong_project_missing_state_bind_mount_and_lost_tunnel(self):
        cases = [
            existing() | {"labels": {}},
            existing() | {"mounts": []},
            existing()
            | {
                "mounts": [
                    {"Type": "bind", "Destination": "/home/opend/.com.moomoo.OpenD"}
                ]
            },
        ]
        for container in cases:
            with (
                self.subTest(container=container),
                patch.object(host, "execute", side_effect=[json.dumps(container), ""]),
                self.assertRaises(ValueError),
            ):
                host.prepare(self.root, self.value)
        with (
            patch.object(
                host,
                "execute",
                side_effect=[json.dumps(existing()), "tunnel-container\n"],
            ),
            self.assertRaises(ValueError),
        ):
            host.prepare(self.root, self.value)

    def test_real_compose_renders_pinned_images_and_original_external_volumes(self):
        if not shutil.which("docker"):
            self.skipTest("Docker Compose is unavailable")
        with (
            patch.object(host, "execute", side_effect=[json.dumps(existing()), ""]),
            patch.object(host, "validate_configuration"),
        ):
            host.prepare(self.root, self.value)
        model = json.loads(
            host.execute(
                host.compose(self.root, self.value) + ["config", "--format", "json"]
            )
        )
        service = model["services"]["moomoo-mcp"]
        self.assertEqual(service["image"], IMAGE)
        self.assertNotIn("build", service)
        self.assertEqual(
            model["volumes"]["opend-state"]["name"], "original-project_opend-data"
        )
        self.assertTrue(model["volumes"]["opend-state"]["external"])
        self.assertEqual(service["ports"][0]["host_ip"], "127.0.0.1")
        host.validate_configuration(self.root, self.value)

    def test_refuses_writable_mode_and_unprotected_credentials_without_changing_file(
        self,
    ):
        model = {
            "services": {
                "moomoo-mcp": {
                    "environment": {
                        "MCP_AUTH_TOKEN": "synthetic",
                        "MOOMOO_TRADING_MODE": "REAL",
                    }
                }
            }
        }
        with (
            patch.object(host, "execute", return_value=json.dumps(model)),
            self.assertRaises(ValueError),
        ):
            host.validate_configuration(self.root, self.value)
        self.env.chmod(0o644)
        with patch.object(host, "execute") as command, self.assertRaises(ValueError):
            host.validate_configuration(self.root, self.value)
        command.assert_not_called()
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o644)

    def test_command_failure_does_not_relay_secret_text(self):
        result = subprocess.CompletedProcess(
            [], 1, "synthetic-secret", "synthetic-secret"
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(ValueError) as failure,
        ):
            host.execute(["synthetic-command"])
        self.assertNotIn("synthetic-secret", str(failure.exception))
