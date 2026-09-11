"""Exercise deployment in disposable Git repos, with no AWS or Docker access."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com"


class DeployScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "scripts").mkdir()
        for name in ("deploy.sh", "compose-prod.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.repo / "scripts" / name)
        for name in (
            "docker-compose.yml",
            "docker-compose.prod.yml",
            ".gitignore",
            "pyproject.toml",
        ):
            shutil.copy2(ROOT / name, self.repo / name)
        (self.repo / ".env").write_text("MCP_AUTH_TOKEN=test-only\n")
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Deployment Test")
        self.git("config", "core.abbrev", "12")
        self.git("add", ".")
        self.git("commit", "-m", "test fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        # Tag the fixture with the version read from pyproject.toml so the
        # deploy script's release-tag path is exercised on every test
        # (and so "v<version>" queries against ECR land on the real digest).
        # Tests that need different ECR responses override AWS_TEST_MODE.
        fixture_version = subprocess.check_output(
            [
                "awk",
                '-F"',
                "/^version[[:space:]]*=/ { print $2; exit }",
                str(self.repo / "pyproject.toml"),
            ],
            text=True,
        ).strip()
        self.release_tag = f"v{fixture_version}"
        self.git("tag", "-a", self.release_tag, "-m", "test fixture release")
        self.git("remote", "add", "origin", str(self.repo))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Both stubs record arguments and selected inherited environment only.
        stub = """#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, sys.argv[1:], os.environ.get("IMAGE_TAG")]) + "\\n")
if name == "aws":
    mode = os.environ.get("AWS_TEST_MODE", "ok")
    if mode == "denied":
        print("AccessDeniedException: test denial", file=sys.stderr)
        sys.exit(254)
    if mode == "missing" and "moomoo-opend" in sys.argv:
        print("None")
    else:
        print("sha256:test-digest")
if name == "docker" and os.environ.get("DOCKER_TEST_FAIL") == "1":
    sys.exit(1)
"""
        for name in ("aws", "docker"):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.env = os.environ.copy()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            ECR_REGISTRY=REGISTRY,
            IMAGE_TAG="stale-shell-tag",
            CALL_LOG=str(self.log),
        )

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, text=True, stderr=subprocess.PIPE
        )

    def deploy(self, *args):
        return subprocess.run(
            [str(self.repo / "scripts/deploy.sh"), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_prepare_checks_both_images_and_pulls_without_starting(self):
        result = self.deploy("--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / ".deploy.env").read_text(),
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG={self.release_tag}\n",
        )
        calls = self.calls()
        aws = [args for name, args, _ in calls if name == "aws"]
        # Two release-tag checks (one per image) at most. After they pass,
        # we never query :latest, so there are no extra fallback probes.
        self.assertEqual(len(aws), 2)
        for args in aws:
            self.assertEqual(args[:2], ["ecr", "batch-get-image"])
            self.assertIn("imageTag=" + self.release_tag, args)
        docker = [(args, tag) for name, args, tag in calls if name == "docker"]
        self.assertEqual(len(docker), 1)
        args, tag = docker[0]
        self.assertEqual(args[:3], ["--context", "rootless", "compose"])
        self.assertIn(".env", args)
        self.assertIn(".deploy.env", args)
        self.assertEqual(args[-1], "pull")
        self.assertIsNone(tag)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)

    def test_explicit_commit_reuses_saved_registry_and_starts(self):
        (self.repo / ".deploy.env").write_text(
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=old\n"
        )
        self.env.pop("ECR_REGISTRY")
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        docker = [args for name, args, _ in self.calls() if name == "docker"]
        self.assertEqual(len(docker), 3)
        self.assertEqual(docker[1][-3:], ["up", "-d", "--remove-orphans"])

    def test_missing_second_image_does_not_checkout_or_write_settings(self):
        self.env["AWS_TEST_MODE"] = "missing"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        # The release path is tried first (:v<version>), then falls through
        # to :latest before declaring failure; the script therefore makes
        # 2 probes per image (release + latest), all returning 'None' for
        # moomoo-opend. The error message names moomoo-opend via the ECR
        # REGISTRY's host, not the image label, so check for a substring
        # unique to this failure mode.
        self.assertIn("moomoo-opend", result.stderr)
        self.assertTrue(
            "Aborting" in result.stderr or "aborting" in result.stderr,
            f"stderr didn't include abort marker: {result.stderr!r}",
        )
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertFalse((self.repo / ".deploy.env").exists())
        self.assertTrue(all(name == "aws" for name, _, _ in self.calls()))

    def test_permission_failure_remains_visible(self):
        self.env["AWS_TEST_MODE"] = "denied"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDeniedException", result.stderr)
        self.assertNotIn("ECR has no", result.stderr)
        self.assertFalse((self.repo / ".deploy.env").exists())

    def test_failed_pull_does_not_start_services(self):
        self.env["DOCKER_TEST_FAIL"] = "1"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        docker = [args for name, args, _ in self.calls() if name == "docker"]
        self.assertEqual(len(docker), 1)
        self.assertEqual(docker[0][-1], "pull")


if __name__ == "__main__":
    unittest.main()
