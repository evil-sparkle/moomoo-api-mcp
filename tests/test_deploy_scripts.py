"""Exercise deployment in disposable Git repos, with no AWS or Docker access."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com"


def _env_without_git_vars():
    """Copy the environment with Git's per-invocation variables removed.

    Git exports GIT_DIR, GIT_INDEX_FILE and friends to every process it starts,
    hooks included. These tests shell out to git, and to deploy.sh which runs
    git itself, so inheriting those variables aims both at whatever repository
    git is currently working on instead of the disposable fixture. Running the
    suite from a pre-commit hook then rewrote the developer's own checkout:
    `git init` re-initialised it, `git config user.name` replaced their
    identity, and deploy.sh's `git checkout --detach` moved their HEAD.
    """
    return {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }


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
        fixture_version = ""
        with open(self.repo / "pyproject.toml") as f:
            for line in f:
                if (
                    line.startswith("version")
                    and line.split("=")[0].strip() == "version"
                ):
                    fixture_version = line.split('"')[1]
                    break
        if not fixture_version:
            self.fail("pyproject.toml has no version line")
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
    elif mode == "only_latest":
        if "imageTag=latest" in sys.argv:
            print("sha256:latest-digest")
        else:
            print("None")
    else:
        print("sha256:test-digest")
if name == "docker":
    if os.environ.get("DOCKER_TEST_FAIL") == "1":
        sys.exit(1)
    if os.environ.get("DOCKER_TEST_FAIL_UP_ALWAYS") == "1" and "up" in sys.argv:
        sys.exit(1)
    if os.environ.get("DOCKER_TEST_FAIL_UP") == "1" and "up" in sys.argv:
        marker = pathlib.Path(os.environ["CALL_LOG"]).with_name("up_marker")
        if not marker.exists():
            marker.write_text("")
            sys.exit(1)
    if os.environ.get("DOCKER_TEST_FAIL_PULL") == "1" and "pull" in sys.argv:
        sys.exit(1)
    if "ps" in sys.argv:
        if os.environ.get("DOCKER_PS_MISSING") == "1":
            print("opend\\n")
        else:
            print("opend\\nmoomoo-mcp\\n")
if name == "curl":
    if os.environ.get("CURL_TEST_FAIL") == "1":
        print("000")
        sys.exit(7)
    else:
        print("200")
"""
        for name in ("aws", "docker", "curl"):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.env = _env_without_git_vars()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            ECR_REGISTRY=REGISTRY,
            IMAGE_TAG="stale-shell-tag",
            CALL_LOG=str(self.log),
            DEPLOY_VERIFY_TIMEOUT="0",
        )

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=self.repo,
            text=True,
            stderr=subprocess.PIPE,
            env=_env_without_git_vars(),
        )

    def deploy(self, *args):
        return subprocess.run(
            [str(self.repo / "scripts/deploy.sh"), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_git_helper_ignores_inherited_git_environment(self):
        """Inherited GIT_* variables must not redirect the fixture's git calls.

        Run from a pre-commit hook, these tests used to operate on the
        repository being committed: setUp's `git config user.name` overwrote
        the developer's identity and `git add` rewrote their index.
        """
        hijacked = self.root / "hijacked"
        hijacked.mkdir()
        subprocess.check_output(
            ["git", "init", "-b", "main"],
            cwd=hijacked,
            text=True,
            stderr=subprocess.PIPE,
            env=_env_without_git_vars(),
        )

        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(hijacked / ".git"),
                "GIT_WORK_TREE": str(hijacked),
            },
        ):
            self.git("config", "user.name", "Leak Check")

        self.assertEqual(
            self.git("config", "--local", "user.name").strip(), "Leak Check"
        )
        untouched = subprocess.run(
            ["git", "config", "--local", "user.name"],
            cwd=hijacked,
            capture_output=True,
            text=True,
            env=_env_without_git_vars(),
        )
        self.assertEqual(untouched.stdout.strip(), "")

    def test_deploy_environment_carries_no_git_variables(self):
        """deploy.sh runs git itself, including `git checkout --detach`.

        setUp is driven directly under a hijacked environment: asserting on
        this instance's env would only catch the leak when the developer
        running the suite happens to have GIT_* set, which is precisely the
        case that used to slip through.
        """
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.root / "nope")}):
            case = DeployScriptsTest("test_deploy_environment_carries_no_git_variables")
            case.setUp()
            self.addCleanup(case.temp.cleanup)

        self.assertNotIn("GIT_DIR", case.env)

    def test_prepare_checks_both_images_and_pulls_without_starting(self):
        result = self.deploy("--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / ".deploy.env").read_text(),
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG={self.commit[:7]}\n",
        )
        calls = self.calls()
        aws = [args for name, args, _ in calls if name == "aws"]
        # Two short commit checks (one per image). After they pass,
        # we never query :latest, so there are no extra fallback probes.
        self.assertEqual(len(aws), 2)
        for args in aws:
            self.assertEqual(args[:2], ["ecr", "batch-get-image"])
            self.assertIn("imageTag=" + self.commit[:7], args)
        docker = [args for name, args, _ in calls if name == "docker"]
        self.assertEqual(len(docker), 1)
        args = docker[0]
        self.assertEqual(args[:3], ["--context", "rootless", "compose"])
        self.assertIn(".env", args)
        self.assertIn(".deploy.env", args)
        self.assertEqual(args[-1], "pull")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertFalse(
            any(
                name == "curl" or (name == "docker" and "ps" in args)
                for name, args, _ in calls
            )
        )

    def test_explicit_commit_reuses_saved_registry_and_starts(self):
        (self.repo / ".deploy.env").write_text(
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=old\n"
        )
        self.env.pop("ECR_REGISTRY")
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Deploy verified", result.stderr)
        docker = [args for name, args, _ in self.calls() if name == "docker"]
        self.assertEqual(len(docker), 4)
        self.assertEqual(docker[0][-1], "pull")
        self.assertEqual(docker[1][-3:], ["up", "-d", "--remove-orphans"])
        self.assertIn("ps", docker[2])
        self.assertIn("--status", docker[2])
        self.assertIn("running", docker[2])
        self.assertIn("--services", docker[2])
        self.assertIn("logs", docker[3])

    def test_release_tagged_commit_still_deploys_commit_tag(self):
        """A commit carrying a v* git tag still deploys under its short commit."""
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"as {self.commit[:7]}", result.stderr)
        calls = self.calls()
        for name, args, _ in calls:
            if name == "aws":
                for arg in args:
                    self.assertFalse(
                        arg.startswith("imageTag=v"),
                        f"Unexpected AWS call with release tag: {args}",
                    )

        # An untagged commit past the release deploys as its own short commit too.
        (self.repo / "extra.txt").write_text("work after the release\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "past the release")
        newer_commit = self.git("rev-parse", "HEAD").strip()

        # Reset deploy log so we can cleanly check again
        self.log.unlink()

        result_newer = self.deploy(newer_commit)
        self.assertEqual(result_newer.returncode, 0, result_newer.stderr)
        self.assertIn(f"as {newer_commit[:7]}", result_newer.stderr)

    def test_missing_second_image_does_not_checkout_or_write_settings(self):
        self.env["AWS_TEST_MODE"] = "missing"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        # The script checks the short commit tag before declaring failure;
        # it does not fall through to :latest. The error message names moomoo-opend.
        self.assertIn("moomoo-opend", result.stderr)
        self.assertTrue(
            "Aborting" in result.stderr or "aborting" in result.stderr,
            f"stderr didn't include abort marker: {result.stderr!r}",
        )
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertFalse((self.repo / ".deploy.env").exists())
        calls = self.calls()
        self.assertTrue(all(name == "aws" for name, _, _ in calls))
        for _, args, _ in calls:
            self.assertNotIn("imageTag=latest", args)

    def test_permission_failure_remains_visible(self):
        self.env["AWS_TEST_MODE"] = "denied"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AccessDeniedException", result.stderr)
        self.assertNotIn("ECR has no", result.stderr)
        self.assertFalse((self.repo / ".deploy.env").exists())

    def test_missing_commit_tag_aborts_without_latest_probe(self):
        self.env["AWS_TEST_MODE"] = "only_latest"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertFalse((self.repo / ".deploy.env").exists())
        calls = self.calls()
        self.assertTrue(all(name == "aws" for name, _, _ in calls))
        for _, args, _ in calls:
            self.assertNotIn("imageTag=latest", args)

    def test_failed_pull_does_not_start_services(self):
        prev_env = f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous-tag\n"
        (self.repo / ".deploy.env").write_text(prev_env)
        self.env.pop("ECR_REGISTRY")

        (self.repo / "extra.txt").write_text("newer commit\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "newer commit")
        newer_commit = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", self.commit)

        self.env["DOCKER_TEST_FAIL_PULL"] = "1"
        result = self.deploy(newer_commit)

        self.assertNotEqual(result.returncode, 0)
        docker = [args for name, args, _ in self.calls() if name == "docker"]
        self.assertEqual(len(docker), 1)
        self.assertEqual(docker[0][-1], "pull")

        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

    def test_failed_up_rolls_back_to_previous_state(self):
        prev_env = f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous-tag\n"
        (self.repo / ".deploy.env").write_text(prev_env)
        self.env.pop("ECR_REGISTRY")

        (self.repo / "extra.txt").write_text("newer commit\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "newer commit")
        newer_commit = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", self.commit)

        self.env["DOCKER_TEST_FAIL_UP"] = "1"
        result = self.deploy(newer_commit)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Rolled back to {self.commit[:7]}", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

        docker = [args for name, args, _ in self.calls() if name == "docker"]
        up_calls = [
            args for args in docker if args[-3:] == ["up", "-d", "--remove-orphans"]
        ]
        self.assertEqual(len(up_calls), 2)

    def test_failed_up_always_exits_and_tells_user(self):
        prev_env = f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous-tag\n"
        (self.repo / ".deploy.env").write_text(prev_env)
        self.env.pop("ECR_REGISTRY")

        (self.repo / "extra.txt").write_text("newer commit\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "newer commit")
        newer_commit = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", self.commit)

        self.env["DOCKER_TEST_FAIL_UP_ALWAYS"] = "1"
        result = self.deploy(newer_commit)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Stack needs manual attention", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

    def test_deploy_timeout_validation(self):
        self.env["DEPLOY_VERIFY_TIMEOUT"] = "abc"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a numeric value", result.stderr)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertFalse((self.repo / ".deploy.env").exists())
        self.assertEqual(self.calls(), [])

    def test_failed_verification_rolls_back_to_previous_state(self):
        prev_env = f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous-tag\n"
        (self.repo / ".deploy.env").write_text(prev_env)
        self.env.pop("ECR_REGISTRY")

        (self.repo / "extra.txt").write_text("newer commit\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "newer commit")
        newer_commit = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", self.commit)

        self.env["CURL_TEST_FAIL"] = "1"
        result = self.deploy(newer_commit)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Rolled back to {self.commit[:7]}", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

        docker = [args for name, args, _ in self.calls() if name == "docker"]
        up_calls = [
            args for args in docker if args[-3:] == ["up", "-d", "--remove-orphans"]
        ]
        self.assertEqual(len(up_calls), 2)

    def test_failed_verification_without_previous_state(self):
        self.env["CURL_TEST_FAIL"] = "1"
        result = self.deploy(self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Deploy verified", result.stderr)


if __name__ == "__main__":
    unittest.main()
