"""Race real lifecycle subprocesses against the shared deployment lock."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import deployment_lock as locking  # noqa: E402


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = dict(os.environ)
        self.environment.pop(locking.TOKEN_ENV, None)
        self.environment[locking.ROOT_ENV] = str(self.root)

    def command(self, *arguments, environment=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/deployment_lock.py"), *arguments],
            env=environment or self.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_concurrent_lifecycle_is_refused_while_owner_remains_unchanged(self):
        started = self.root / "started"
        finish = self.root / "finish"
        child = (
            "import pathlib,time; root=pathlib.Path(" + repr(str(self.root)) + "); "
            "(root/'started').touch(); "
            "exec(\"while not (root/'finish').exists():\\n time.sleep(0.01)\")"
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/deployment_lock.py"),
                "run",
                "--",
                sys.executable,
                "-c",
                child,
            ],
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while (
                not started.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertTrue(started.exists())
            owner = (self.root / ".deployment-lock/owner.json").read_text()
            result = self.command(
                "run", "--", sys.executable, "-c", "raise SystemExit(0)"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                (self.root / ".deployment-lock/owner.json").read_text(), owner
            )
        finally:
            finish.touch()
            output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output + error)
        self.assertFalse((self.root / ".deployment-lock").exists())

    def test_delegation_requires_exact_private_token_and_never_releases_parent(self):
        result = self.command("acquire")
        self.assertEqual(result.returncode, 0, result.stderr)
        token = result.stdout.strip()
        delegated = self.environment | {locking.TOKEN_ENV: token}
        owner = self.root / ".deployment-lock/owner.json"
        self.assertEqual(owner.stat().st_mode & 0o777, 0o600)
        bad = self.command(
            "run",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
            environment=self.environment | {locking.TOKEN_ENV: "wrong-owner"},
        )
        self.assertNotEqual(bad.returncode, 0)
        child = self.command(
            "run",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
            environment=delegated,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(owner.read_text())["token"], token)
        wrong_release = self.command(
            "release", environment=self.environment | {locking.TOKEN_ENV: "wrong-owner"}
        )
        self.assertNotEqual(wrong_release.returncode, 0)
        self.assertTrue(owner.exists())
        released = self.command("release", environment=delegated)
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertFalse(owner.parent.exists())

    def test_failure_releases_only_the_current_callers_lock(self):
        result = self.command("run", "--", sys.executable, "-c", "raise SystemExit(23)")
        self.assertEqual(result.returncode, 23)
        self.assertFalse((self.root / ".deployment-lock").exists())
        with (
            patch.dict(os.environ, self.environment, clear=True),
            self.assertRaises(RuntimeError),
            locking.deployment_lock(),
        ):
            raise RuntimeError("synthetic failure")
        self.assertFalse((self.root / ".deployment-lock").exists())

    def test_legacy_lifecycle_obeys_lock_and_delegation(self):
        checkout = self.root / "legacy"
        (checkout / "scripts").mkdir(parents=True)
        for filename in ["compose-prod.sh", "deployment_lock.py"]:
            shutil.copy(ROOT / "scripts" / filename, checkout / "scripts" / filename)
        snapshot = checkout / ".deploy-config.json"
        snapshot.write_text('{"services":{}}')
        binaries = self.root / "bin"
        binaries.mkdir()
        marker = self.root / "docker-called"
        docker = binaries / "docker"
        docker.write_text(
            "#!"
            + sys.executable
            + "\nfrom pathlib import Path\nPath("
            + repr(str(marker))
            + ").touch()\n"
        )
        docker.chmod(0o755)
        environment = self.environment | {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        }
        token = self.command("acquire").stdout.strip()
        try:
            for action in ["up", "stop", "restart", "down", "rm", "run"]:
                with self.subTest(action=action):
                    result = subprocess.run(
                        ["bash", str(checkout / "scripts/compose-prod.sh"), action],
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(marker.exists())
            allowed = subprocess.run(
                ["bash", str(checkout / "scripts/compose-prod.sh"), "stop"],
                env=environment | {locking.TOKEN_ENV: token},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue(marker.exists())
            self.assertTrue((self.root / ".deployment-lock").exists())
        finally:
            self.command(
                "release", environment=environment | {locking.TOKEN_ENV: token}
            )
