"""Exercise frozen native releases using synthetic configuration only."""

import json
import os
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


def selection(env_file, mode="READ_ONLY", tunnel=False):
    return {
        "revision": "b" * 40,
        "image": IMAGE,
        "tunnel_image": TUNNEL,
        "project": "original-project",
        "env_file": str(env_file),
        "expected_mode": mode,
        "tunnel_enabled": tunnel,
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
        "config": {
            "User": "opend",
            "Env": ["MCP_AUTH_TOKEN=old-$synthetic", "MOOMOO_TRADING_MODE=READ_ONLY"],
        },
        "host": {},
        "image": "sha256:" + "d" * 64,
    }


def model(mode="READ_ONLY"):
    return {
        "services": {
            "moomoo-mcp": {
                "image": IMAGE,
                "environment": {
                    "MCP_AUTH_TOKEN": "synthetic",
                    "MOOMOO_TRADING_MODE": mode,
                },
            }
        }
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
        for filename in [
            "docker-compose.yml",
            "docker-compose.paper.yml",
            "docker-compose.real.yml",
            "docker-compose.controls.yml",
        ]:
            shutil.copy(ROOT / filename, self.root / filename)

    def prepare(
        self,
        value=None,
        *,
        container=None,
        base=None,
        final=None,
        old_tunnel=None,
        capture_predecessor=True,
    ):
        responses = [json.dumps(container or existing())]
        if capture_predecessor:
            responses.append("synthetic-tunnel\n" if old_tunnel else "")
            if old_tunnel:
                responses.append(json.dumps(old_tunnel))
            responses.append("")  # Compose validates the rescue document first.
        responses += [json.dumps(base or model()), json.dumps(final or base or model())]
        with patch.object(host, "execute", side_effect=responses) as execute:
            host.prepare(
                self.root,
                value or self.value,
                capture_predecessor=capture_predecessor,
            )
        return execute

    def test_prepare_preserves_state_and_freezes_actual_predecessor(self):
        original_read = Path.read_text

        def public_only(path, *args, **kwargs):
            self.assertNotEqual(path, self.env)
            return original_read(path, *args, **kwargs)

        with patch.object(Path, "read_text", public_only):
            self.prepare()
        saved = json.loads((self.root / "state-volumes.json").read_text())
        self.assertEqual(
            saved["opend-state"],
            {"external": True, "name": "original-project_opend-data"},
        )
        self.assertEqual(
            saved["paper-state"]["name"], "original-project_execution-data"
        )
        previous = json.loads((self.root / "previous-compose.json").read_text())
        self.assertEqual(
            previous["services"]["moomoo-mcp"]["environment"]["MCP_AUTH_TOKEN"],
            "old-$$synthetic",
        )
        for filename in [host.SNAPSHOT, "previous-compose.json"]:
            self.assertEqual((self.root / filename).stat().st_mode & 0o777, 0o600)
        self.env.write_text("MCP_AUTH_TOKEN=changed-after-prepare\n")
        frozen = host.validate_configuration(self.root, self.value)
        self.assertEqual(
            frozen["services"]["moomoo-mcp"]["environment"]["MCP_AUTH_TOKEN"],
            "synthetic",
        )
        with self.assertRaises(ValueError):
            self.prepare()

    def test_refuses_wrong_project_missing_state_and_bind_mount(self):
        for container in [
            existing() | {"labels": {}},
            existing() | {"mounts": []},
            existing()
            | {
                "mounts": [
                    {"Type": "bind", "Destination": "/home/opend/.com.moomoo.OpenD"}
                ]
            },
        ]:
            with (
                self.subTest(container=container),
                patch.object(host, "execute", return_value=json.dumps(container)),
                self.assertRaises(ValueError),
            ):
                host.prepare(self.root, self.value)

    def test_modes_and_controls_select_the_same_named_state_as_production(self):
        for mode in ["READ_ONLY", "SIMULATE", "REAL"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                selected = selection(self.env, mode)
                resolved = model(mode)
                resolved["services"]["moomoo-mcp"]["environment"].update(
                    {
                        "MOOMOO_SIMULATED_ACC_IDS": "123",
                        "MOOMOO_REQUIRE_TRADE_APPROVAL": "1",
                    }
                )
                with patch.object(
                    host,
                    "execute",
                    side_effect=[
                        json.dumps(existing()),
                        json.dumps(resolved),
                        json.dumps(resolved),
                    ],
                ):
                    host.prepare(root, selected)
                volumes = json.loads((root / "state-volumes.json").read_text())
                self.assertEqual(
                    volumes["paper-state"]["name"], "original-project_execution-data"
                )
                self.assertEqual(
                    volumes["controls-state"]["name"],
                    "original-project_trading-controls-data",
                )
                if mode == "REAL":
                    self.assertEqual(
                        volumes["real-state"]["name"],
                        "original-project_real-execution-data",
                    )
                commands = (root / "compose.ansible.yml").read_text()
                self.assertNotIn("MOOMOO_CREATE_JOURNAL", commands)
                self.assertNotIn("MOOMOO_CREATE_REAL_JOURNAL", commands)

    def test_read_only_preserves_existing_live_and_control_volumes(self):
        container = existing()
        for target, name in [
            ("/app/real-execution-data", "existing-live"),
            ("/var/lib/moomoo/trading-controls", "existing-controls"),
        ]:
            container["mounts"].append(
                {"Type": "volume", "Name": name, "Destination": target}
            )
        self.prepare(container=container)
        saved = json.loads((self.root / "state-volumes.json").read_text())
        self.assertEqual(saved["real-state"]["name"], "existing-live")
        self.assertEqual(saved["controls-state"]["name"], "existing-controls")

    def test_unused_retained_volumes_do_not_enable_execution_databases(self):
        container = existing()
        for target, name in [
            ("/app/real-execution-data", "existing-live"),
            ("/var/lib/moomoo/trading-controls", "existing-controls"),
        ]:
            container["mounts"].append(
                {"Type": "volume", "Name": name, "Destination": target}
            )
        for mode in ["READ_ONLY", "REAL"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as name:
                release = Path(name)
                resolved = model(mode)
                with patch.object(
                    host,
                    "execute",
                    side_effect=[
                        json.dumps(container),
                        json.dumps(resolved),
                        json.dumps(resolved),
                    ],
                ) as command:
                    host.prepare(release, selection(self.env, mode))
                files = command.call_args.args[0]
                self.assertNotIn(str(release / "docker-compose.paper.yml"), files)
                self.assertNotIn(str(release / "docker-compose.controls.yml"), files)
                self.assertEqual(
                    str(release / "docker-compose.real.yml") in files, mode == "REAL"
                )
                volumes = json.loads((release / "state-volumes.json").read_text())
                self.assertEqual(len(volumes), 4)

    def test_explicit_tunnel_disable_freezes_previous_tunnel_for_rollback(self):
        tunnel = existing()
        tunnel["host"] = {
            "Tmpfs": {"/run/moomoo-chatgpt-tunnel": "mode=0700,size=16m"},
            "ReadonlyRootfs": True,
        }
        commands = self.prepare(old_tunnel=tunnel)
        previous = json.loads((self.root / "previous-compose.json").read_text())
        self.assertIn("chatgpt-tunnel", previous["services"])
        self.assertEqual(
            previous["services"]["chatgpt-tunnel"]["tmpfs"],
            ["/run/moomoo-chatgpt-tunnel:mode=0700,size=16m"],
        )
        validation = next(
            call.args[0]
            for call in commands.call_args_list
            if "--quiet" in call.args[0]
        )
        self.assertIn(str(self.root / "previous-compose.json"), validation)
        self.assertNotIn(
            "chatgpt-tunnel",
            json.loads((self.root / host.SNAPSHOT).read_text())["services"],
        )
        with patch.object(host, "preflight"), patch.object(host, "execute") as execute:
            host.operate(
                self.root, self.value, "compose", ["up", "-d", "--remove-orphans"]
            )
        self.assertIn("--remove-orphans", execute.call_args.args[0])

    def test_managed_upgrade_does_not_reconstruct_legacy_configuration(self):
        commands = self.prepare(capture_predecessor=False)
        self.assertFalse((self.root / "previous-compose.json").exists())
        self.assertFalse(any("ps" in call.args[0] for call in commands.call_args_list))

    def test_invalid_legacy_snapshot_stops_before_candidate_resolution(self):
        with (
            patch.object(
                host,
                "execute",
                side_effect=[json.dumps(existing()), "", ValueError("invalid rescue")],
            ) as command,
            self.assertRaises(ValueError),
        ):
            host.prepare(self.root, self.value, capture_predecessor=True)
        self.assertEqual(command.call_count, 3)
        self.assertFalse((self.root / host.SNAPSHOT).exists())

    def test_saved_release_rollback_ignores_edited_settings_and_preserves_journals(
        self,
    ):
        for fails in [False, True]:
            with self.subTest(fails=fails), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                releases = root / "releases"
                old = releases / ("a" * 40 + "-" + "b" * 32)
                active = releases / ("c" * 40 + "-" + "d" * 32)
                for release, token in [(old, "saved-token"), (active, "active-token")]:
                    release.mkdir(parents=True)
                    (release / "deployment.json").write_text(json.dumps(self.value))
                    frozen = model()
                    frozen["services"]["moomoo-mcp"]["environment"].update(
                        MCP_AUTH_TOKEN=token
                    )
                    host.private_json(release / host.SNAPSHOT, frozen)
                current = root / "current"
                current.symlink_to(active, target_is_directory=True)
                journal = root / "journal.db"
                journal.write_bytes(b"subsequent journal evidence")
                self.env.write_text("MCP_AUTH_TOKEN=edited-token\n")
                old_snapshot = (old / host.SNAPSHOT).read_bytes()
                with (
                    patch.object(host, "preflight") as preflight,
                    patch.object(
                        host,
                        "verify",
                        side_effect=ValueError("failed") if fails else None,
                    ),
                    patch.object(host, "execute") as command,
                ):
                    with self.assertRaises(ValueError):
                        host.rollback(active, self.value, "../outside")
                    preflight.assert_not_called()
                    command.assert_not_called()
                    if fails:
                        with self.assertRaises(ValueError):
                            host.rollback(active, self.value, old.name)
                    else:
                        host.rollback(active, self.value, old.name)
                    preflight.assert_called_once_with(old, self.value)
                self.assertEqual(current.resolve(), active if fails else old)
                self.assertEqual((old / host.SNAPSHOT).read_bytes(), old_snapshot)
                self.assertEqual(journal.read_bytes(), b"subsequent journal evidence")
                self.assertEqual(command.call_count, 2 if fails else 1)
                self.assertIn(
                    str(old / host.SNAPSHOT), command.call_args_list[0].args[0]
                )
                for call in command.call_args_list:
                    self.assertNotIn("--env-file", call.args[0])
                    self.assertNotIn("--volumes", call.args[0])

    def test_tunnel_requires_independent_restricted_identity(self):
        selected = self.value | {"tunnel_enabled": True, "expected_mode": "REAL"}
        resolved = model("REAL")
        environment = resolved["services"]["moomoo-mcp"]["environment"]
        environment["MCP_RESTRICTED_AUTH_TOKEN"] = "synthetic-restricted"
        resolved["services"]["chatgpt-tunnel"] = {
            "environment": {
                "MCP_AUTH_TOKEN": "synthetic-restricted",
                "CHATGPT_TUNNEL_API_KEY": "synthetic-key",
                "CHATGPT_TUNNEL_ID": "tunnel_synthetic",
            }
        }
        host.validate_model(resolved, selected)
        resolved["services"]["chatgpt-tunnel"]["environment"]["MCP_AUTH_TOKEN"] = (
            environment["MCP_AUTH_TOKEN"]
        )
        with self.assertRaises(ValueError):
            host.validate_model(resolved, selected)

    def test_offline_preflight_uses_frozen_decoded_environment_and_readonly_storage(
        self,
    ):
        resolved = model("REAL")
        resolved["services"]["moomoo-mcp"]["environment"]["MCP_AUTH_TOKEN"] = (
            "synthetic-$$PASSWORD"
        )
        resolved["services"]["moomoo-mcp"]["volumes"] = [
            {"source": "real-state", "target": "/app/real-execution-data"}
        ]
        resolved["volumes"] = {
            "real-state": {"name": "original-project_real-execution-data"}
        }
        host.private_json(self.root / host.SNAPSHOT, resolved)
        with patch.object(host, "execute") as execute:
            host.preflight(self.root, self.value | {"expected_mode": "REAL"})
        self.assertEqual(execute.call_count, 2)
        first, storage = execute.call_args_list
        self.assertEqual(first.args[0][-1], "--validate")
        self.assertIn("--network", first.args[0])
        self.assertEqual(
            json.loads(first.kwargs["stdin"])["MCP_AUTH_TOKEN"], "synthetic-$PASSWORD"
        )
        self.assertEqual(storage.args[0][-1], "--validate-storage")
        self.assertIn(
            "type=volume,source=original-project_real-execution-data,target=/app/real-execution-data,readonly",
            storage.args[0],
        )

    def test_real_compose_renders_frozen_images_and_original_external_volumes(self):
        if not shutil.which("docker"):
            self.skipTest("Docker Compose is unavailable")
        execute = host.execute

        def commands(arguments, **kwargs):
            if arguments[1] == "inspect":
                container = existing()
                if arguments[2] != "moomoo-api-mcp":
                    container["host"] = {
                        "Tmpfs": {"/run/tunnel": "mode=0700,size=16m"}
                    }
                return json.dumps(container)
            if arguments[1] == "ps":
                return "synthetic-tunnel"
            return execute(arguments, **kwargs)

        with patch.object(host, "execute", side_effect=commands):
            host.prepare(self.root, self.value, capture_predecessor=True)
        predecessor = json.loads(
            execute(
                host.compose(self.root, self.value, sources=[])
                + [
                    "-f",
                    str(self.root / "previous-compose.json"),
                    "config",
                    "--format",
                    "json",
                ]
            )
        )
        self.assertEqual(
            predecessor["services"]["chatgpt-tunnel"]["tmpfs"],
            ["/run/tunnel:mode=0700,size=16m"],
        )
        resolved = json.loads(
            host.execute(
                host.compose(self.root, self.value) + ["config", "--format", "json"]
            )
        )
        self.assertEqual(resolved["services"]["moomoo-mcp"]["image"], IMAGE)
        self.assertNotIn("build", resolved["services"]["moomoo-mcp"])
        self.assertEqual(
            resolved["volumes"]["opend-state"]["name"], "original-project_opend-data"
        )
        host.validate_configuration(self.root, self.value)

    def test_mismatched_mode_and_unprotected_configuration_fail(self):
        with self.assertRaises(ValueError):
            host.validate_model(model("REAL"), self.value)
        self.env.chmod(0o644)
        with patch.object(host, "execute") as command, self.assertRaises(ValueError):
            host.prepare(self.root, self.value)
        command.assert_not_called()

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

    def test_real_helper_processes_preserve_state_across_modes_and_tunnel_disable(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        shutil.copy(
            ROOT / "deployments/moomoo/tests/docker-stub.py", binaries / "docker"
        )
        (binaries / "docker").chmod(0o755)
        state = self.root / "runtime.json"
        state.write_text(json.dumps({"image": IMAGE}))
        log = self.root / "commands.jsonl"
        environment = dict(os.environ)
        environment.pop("MOOMOO_DEPLOY_LOCK_TOKEN", None)
        environment.update(
            {
                "PATH": str(binaries) + os.pathsep + environment["PATH"],
                "MOOMOO_DEPLOY_LOCK_ROOT": str(self.root / "deployment-state"),
                "SYNTHETIC_DOCKER_STATE": str(state),
                "SYNTHETIC_DOCKER_LOG": str(log),
                "SYNTHETIC_LEGACY_RESTORED": str(self.root / "restored"),
            }
        )
        previous_mounts = None
        saved_releases = []
        for index, mode in enumerate(["SIMULATE", "REAL", "READ_ONLY"]):
            release = self.root / "releases" / (f"{index:040x}-" + "e" * 32)
            saved_releases.append(release)
            (release / "scripts").mkdir(parents=True)
            for filename in [
                "ansible_deployment.py",
                "deployment_lock.py",
                "deployment_manifest.py",
                "deployment_probe.py",
            ]:
                shutil.copy(ROOT / "scripts" / filename, release / "scripts" / filename)
            for filename in [
                "docker-compose.yml",
                "docker-compose.paper.yml",
                "docker-compose.real.yml",
                "docker-compose.controls.yml",
            ]:
                shutil.copy(ROOT / filename, release / filename)
            self.env.write_text(
                "MCP_AUTH_TOKEN=synthetic-primary\nMOOMOO_TRADING_MODE=" + mode + "\n"
                "MOOMOO_SIMULATED_ACC_IDS=123\nMOOMOO_CREATE_JOURNAL=1\n"
                "MOOMOO_REAL_ACC_IDS=456\nMOOMOO_CREATE_REAL_JOURNAL=1\n"
                "MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY=USD:100\n"
                "MOOMOO_CREATE_TRADING_CONTROLS=1\n"
                "MOOMOO_OPERATOR_AUTH_TOKEN=synthetic-operator-token\n"
                "MOOMOO_OPERATOR_ID=synthetic-operator\n"
                "MCP_RESTRICTED_AUTH_TOKEN=synthetic-restricted\n"
                "CHATGPT_TUNNEL_API_KEY=synthetic-key\nCHATGPT_TUNNEL_ID=tunnel_synthetic\n"
            )
            value = selection(self.env, mode, tunnel=index == 0)
            (release / "deployment.json").write_text(json.dumps(value))
            for arguments in [
                ["prepare"],
                ["compose", "pull"],
                ["compose", "up", "-d", "--no-build", "--remove-orphans"],
                ["verify"],
            ]:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(release / "scripts/ansible_deployment.py"),
                        *arguments,
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode, 0, completed.stdout + completed.stderr
                )
                self.assertNotIn(
                    "synthetic-primary", completed.stdout + completed.stderr
                )
            runtime = json.loads(state.read_text())
            mounts = {
                mount["Destination"]: mount["Name"] for mount in runtime["mounts"]
            }
            if previous_mounts is not None:
                self.assertTrue(previous_mounts.items() <= mounts.items())
            previous_mounts = mounts
            self.assertEqual(runtime["tunnel"], index == 0)
            self.assertIn("MOOMOO_TRADING_MODE=" + mode, runtime["environment"])
            self.assertIn(
                "MOOMOO_TRADING_CONTROLS_PATH="
                "/var/lib/moomoo/trading-controls/controls.sqlite3",
                runtime["environment"],
            )
            self.assertFalse((self.root / "deployment-state/.deployment-lock").exists())
        current = self.root / "current"
        current.symlink_to(saved_releases[-1], target_is_directory=True)
        self.env.write_text("MCP_AUTH_TOKEN=edited-after-release\n")
        result = subprocess.run(
            [
                sys.executable,
                str(current / "scripts/ansible_deployment.py"),
                "rollback",
                saved_releases[0].name,
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(current.resolve(), saved_releases[0])
        restored = json.loads(state.read_text())
        self.assertIn("MCP_AUTH_TOKEN=synthetic-primary", restored["environment"])
        self.assertIn("MOOMOO_TRADING_MODE=SIMULATE", restored["environment"])
        self.assertNotIn("edited-after-release", result.stdout + result.stderr)

