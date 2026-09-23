"""Exercise deployment in disposable Git repos, with no AWS or Docker access.

Docker and curl are stubs here, so these tests cover what deploy.sh decides:
ordering, rollback, re-execution.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com"
# deploy.sh as main ran it before scripts/deploy_verify.py existed: what a host
# still has checked out when it first deploys a commit carrying the helper.
PRE_HELPER_DEPLOY_SH = ROOT / "tests" / "fixtures" / "deploy_sh_before_verifier.sh"


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
        for name in ("deploy.sh", "compose-prod.sh", "deploy_verify.py"):
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
        # A developer's global core.hooksPath applies to every repository on the
        # machine, this disposable fixture included. A commit-msg hook enforcing
        # Conventional Commits then rejects "test fixture" and every test here
        # fails in setUp; a post-checkout hook would fire on deploy.sh's
        # `git checkout --detach`. CI has no global hooks, so this only ever
        # breaks locally. Point the fixture at a directory that holds no hooks.
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
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
        # The stubs record arguments and selected inherited environment only.
        # docker answers `config` with the resolved model Compose would print,
        # carrying DOCKER_TEST_TOKEN as the service's MCP_AUTH_TOKEN. curl
        # mirrors the probe's wire format — the body, then the status and the
        # content type on lines of their own from --write-out — and logs any
        # header it was handed on stdin as a separate "curl-header" entry.
        stub = f"""#!{sys.executable}
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
VALID_INIT_RESULT = (
    '{{"jsonrpc":"2.0","id":"deploy-verify","result":{{"protocolVersion":"2025-06-18",'
    '"capabilities":{{"tools":{{}}}},"serverInfo":{{"name":"moomoo-api-mcp","version":"0"}}}}}}'
)
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, args, os.environ.get("IMAGE_TAG")]) + "\\n")
    if name == "curl" and "@-" in args:
        log.write(json.dumps(["curl-header", [sys.stdin.read()], None]) + "\\n")
if name == "aws":
    mode = os.environ.get("AWS_TEST_MODE", "ok")
    if mode == "denied":
        print("AccessDeniedException: test denial", file=sys.stderr)
        sys.exit(254)
    if mode == "missing" and "moomoo-api-mcp" in sys.argv:
        print("None")
    elif mode == "only_latest":
        if "imageTag=latest" in sys.argv:
            print("sha256:latest-digest")
        else:
            print("None")
    else:
        print("sha256:test-digest")
if name == "docker":
    if args[2:4] == ["container", "inspect"]:
        if os.environ.get("DOCKER_TEST_FAIL_INSPECT") == "1":
            sys.exit(1)
        calls = pathlib.Path(os.environ["CALL_LOG"]).read_text().splitlines()
        inspections = sum("container" in json.loads(line)[1] for line in calls)
        if (inspections > 1
                and os.environ.get("DOCKER_TEST_FAIL_CURRENT_INSPECT") == "1"):
            sys.exit(1)
        key = (
            "DOCKER_TEST_PREVIOUS_IMAGE" if inspections == 1
            else "DOCKER_TEST_CURRENT_IMAGE"
        )
        default_image = "sha256:previous" if inspections == 1 else "sha256:current"
        print(os.environ.get(key, default_image))
    if args[2:4] == ["image", "ls"]:
        if os.environ.get("DOCKER_TEST_FAIL_LIST") == "1":
            sys.exit(1)
        print(os.environ.get("DOCKER_TEST_IMAGES", ""))
    if args[2:4] == ["image", "rm"] and os.environ.get("DOCKER_TEST_FAIL_RM") == "1":
        sys.exit(1)
    if os.environ.get("DOCKER_TEST_FAIL") == "1":
        sys.exit(1)
    if "config" in args:
        if os.environ.get("DOCKER_TEST_FAIL_CONFIG") == "1":
            print("invalid env file", file=sys.stderr)
            sys.exit(15)
        token = os.environ.get("DOCKER_TEST_TOKEN", "test-only")
        service = {{"environment": {{"MCP_AUTH_TOKEN": token}}}}
        print(json.dumps({{"services": {{"moomoo-mcp": service}}}}))
    if os.environ.get("DOCKER_TEST_FAIL_LOGS") == "1" and "logs" in sys.argv:
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
if name == "curl":
    if os.environ.get("CURL_TEST_FAIL") == "1":
        # Connection refused: curl prints only --write-out, with status 000.
        sys.stdout.write("\\n000\\n")
        sys.exit(7)
    else:
        body = os.environ.get("CURL_TEST_BODY", VALID_INIT_RESULT)
        status = os.environ.get("CURL_TEST_STATUS", "200")
        sys.stdout.write(body + "\\n" + status + "\\napplication/json")
"""
        for name in ("aws", "docker", "curl"):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)
        # Empty, so a file the deploy leaves behind (a secret, say) shows up.
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = _env_without_git_vars()
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            ECR_REGISTRY=REGISTRY,
            IMAGE_TAG="stale-shell-tag",
            CALL_LOG=str(self.log),
            DEPLOY_VERIFY_TIMEOUT="0",
            TMPDIR=str(self.tmpdir),
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

    def compose_commands(self):
        """The Compose subcommand of each docker call, in order."""
        return [
            args[args.index("docker-compose.prod.yml") + 1]
            for name, args, _ in self.calls()
            if name == "docker" and "compose" in args
        ]

    def save_previous_deploy(self):
        """A prior deployment's settings, for a rollback to restore."""
        prev_env = f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous-tag\n"
        (self.repo / ".deploy.env").write_text(prev_env)
        self.env.pop("ECR_REGISTRY")
        return prev_env

    def add_newer_commit(self):
        """A second commit to deploy, leaving the checkout on the first."""
        (self.repo / "extra.txt").write_text("newer commit\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "newer commit")
        newer_commit = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "--quiet", "--detach", self.commit)
        return newer_commit

    def image_removals(self):
        return [
            args[-1]
            for name, args, _ in self.calls()
            if name == "docker" and args[2:4] == ["image", "rm"]
        ]

    def seed_images(self):
        repo = f"{REGISTRY}/moomoo-api-mcp"
        self.env["DOCKER_TEST_IMAGES"] = "\n".join(
            [
                f"{repo} current sha256:current",
                f"{repo} current-alias sha256:current",
                f"{repo} previous sha256:previous",
                f"{repo} previous-alias sha256:previous",
                f"{repo} old sha256:old",
                f"{repo} older sha256:older",
                f"{repo}-other old sha256:other",
                "other-registry/moomoo-api-mcp old sha256:other",
                "<none> <none> sha256:dangling",
            ]
        )
        return repo

    def test_cleanup_keeps_both_images_and_their_aliases(self):
        repo = self.seed_images()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.image_removals(), [f"{repo}:old", f"{repo}:older"])
        calls = self.calls()
        probe = next(i for i, call in enumerate(calls) if call[0] == "curl")
        for i, (name, args, _) in enumerate(calls):
            if name == "docker" and args[2:4] == ["image", "rm"]:
                self.assertGreater(i, probe)
                self.assertEqual(args[:2], ["--context", "rootless"])
                self.assertNotIn("--force", args)

    def test_cleanup_skipped_on_prepare_and_failed_deployments(self):
        self.seed_images()
        for mode in ("prepare", "CONFIG", "PULL", "UP_ALWAYS", "verify"):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                overrides = {}
                if mode == "verify":
                    overrides["CURL_TEST_FAIL"] = "1"
                elif mode != "prepare":
                    overrides[f"DOCKER_TEST_FAIL_{mode}"] = "1"
                with mock.patch.dict(self.env, overrides):
                    result = self.deploy(*(["--prepare"] if mode == "prepare" else []))
                self.assertEqual(result.returncode == 0, mode == "prepare")
                self.assertEqual(self.image_removals(), [])
                self.assertFalse(
                    any(
                        args[2:4] == ["image", "ls"]
                        for name, args, _ in self.calls()
                        if name == "docker"
                    )
                )

    def test_cleanup_failures_do_not_fail_verified_deployment(self):
        repo = self.seed_images()
        for failure in ("INSPECT", "CURRENT_INSPECT", "LIST", "RM"):
            with self.subTest(failure=failure):
                self.log.unlink(missing_ok=True)
                with mock.patch.dict(self.env, {f"DOCKER_TEST_FAIL_{failure}": "1"}):
                    result = self.deploy()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Deploy verified", result.stderr)
                if failure == "RM":
                    self.assertEqual(
                        self.image_removals(), [f"{repo}:old", f"{repo}:older"]
                    )
                    self.assertIn("Could not remove old app image", result.stderr)
                else:
                    self.assertEqual(self.image_removals(), [])
                    self.assertIn("Skipping image cleanup", result.stderr)

    def test_same_image_redeploy_preserves_older_rollback_image(self):
        self.seed_images()
        self.env["DOCKER_TEST_CURRENT_IMAGE"] = "sha256:previous"
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.image_removals(), [])

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

    def test_prepare_checks_the_image_and_pulls_without_starting(self):
        result = self.deploy("--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / ".deploy.env").read_text(),
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG={self.commit[:7]}\n",
        )
        calls = self.calls()
        aws = [args for name, args, _ in calls if name == "aws"]
        # One short commit check: a single image now carries both programs.
        # After it passes we never query :latest, so there are no extra
        # fallback probes.
        self.assertEqual(len(aws), 1)
        for args in aws:
            self.assertEqual(args[:2], ["ecr", "batch-get-image"])
            self.assertIn("imageTag=" + self.commit[:7], args)
        docker = [args for name, args, _ in calls if name == "docker"]
        for args in docker:
            self.assertEqual(args[:3], ["--context", "rootless", "compose"])
            self.assertIn(".env", args)
            self.assertIn(".deploy.env", args)
        # The configuration is checked, then the image pulled. Nothing starts
        # and nothing is probed.
        self.assertEqual(self.compose_commands(), ["config", "pull"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertFalse(any(name == "curl" for name, _, _ in calls))

    def test_prepare_stops_on_a_configuration_error_before_pulling(self):
        self.env["DOCKER_TEST_FAIL_CONFIG"] = "1"
        result = self.deploy("--prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configuration error", result.stderr)
        self.assertEqual(self.compose_commands(), ["config"])

    def test_explicit_commit_reuses_saved_registry_and_starts(self):
        (self.repo / ".deploy.env").write_text(
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=old\n"
        )
        self.env.pop("ECR_REGISTRY")
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Deploy verified", result.stderr)
        # Configuration is resolved before the pull, and again by verify,
        # which reads it from nowhere else.
        self.assertEqual(
            self.compose_commands(), ["config", "pull", "up", "config", "logs"]
        )
        docker = [
            args
            for name, args, _ in self.calls()
            if name == "docker" and "compose" in args
        ]
        self.assertEqual(docker[2][-3:], ["up", "-d", "--remove-orphans"])
        self.assertIn("moomoo-mcp", docker[4])

    def test_verification_probes_as_an_authenticated_mcp_client(self):
        """The probe sends Compose's resolved token and an MCP initialize.

        A bare GET answers 401 under bearer auth, which was the last log line
        after every successful deploy and read like a failure. Probing as a
        client reads honestly in the access log (POST /mcp 200) and lets a
        token mismatch fail the deploy instead of decorating a success.
        """
        self.env["DOCKER_TEST_TOKEN"] = "resolved by compose"
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        curls = [args for name, args, _ in self.calls() if name == "curl"]
        self.assertEqual(len(curls), 1)
        args = curls[0]
        self.assertIn("Content-Type: application/json", args)
        self.assertIn("Accept: application/json, text/event-stream", args)
        bodies = [args[i + 1] for i, a in enumerate(args) if a == "--data-binary"]
        self.assertEqual(len(bodies), 1)
        self.assertIn('"method":"initialize"', bodies[0])
        # The token travels on curl's stdin: no command line carries it, the
        # output does not show it, and no file is created to hold it. (The
        # stub's curl-header entry is its record of stdin, not a command line.)
        commands = [
            [name, args] for name, args, _ in self.calls() if name != "curl-header"
        ]
        self.assertNotIn("resolved by compose", json.dumps(commands))
        self.assertNotIn("resolved by compose", result.stdout + result.stderr)
        headers = [c[1] for c in self.calls() if c[0] == "curl-header"]
        self.assertEqual(headers, [["Authorization: Bearer resolved by compose\n"]])
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_empty_resolved_token_probes_without_authorization(self):
        self.env["DOCKER_TEST_TOKEN"] = ""
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c for c in self.calls() if c[0] == "curl-header"], [])

    def test_configuration_error_restores_state_without_restarting(self):
        """Nothing has started yet, so the rollback touches files, not services."""
        prev_env = self.save_previous_deploy()
        newer_commit = self.add_newer_commit()
        self.env["DOCKER_TEST_FAIL_CONFIG"] = "1"
        result = self.deploy(newer_commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configuration error", result.stderr)
        self.assertIn(f"Rolled back to {self.commit[:7]}", result.stderr)
        self.assertEqual(self.compose_commands(), ["config"])
        self.assertFalse(any(name == "curl" for name, _, _ in self.calls()))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

    def test_200_that_is_not_an_mcp_initialize_result_fails(self):
        """HTTP 200 alone is not verification.

        smoke-test.sh once caught a server answering 200 while serving an
        empty tool list; here, a 200 whose body is not a JSON-RPC initialize
        success must fail the deploy, not pass it.
        """
        for body in (
            '{"jsonrpc":"2.0","id":"deploy-verify","error":{"code":-32600}}',
            '{"ok": true}',
            '{"result":{"protocolVersion":"2025-06-18"}}',
        ):
            with self.subTest(body=body):
                self.env["CURL_TEST_BODY"] = body
                self.log.unlink(missing_ok=True)
                result = self.deploy(self.commit)
                self.assertNotEqual(result.returncode, 0, body)
                self.assertIn("Deploy verification failed", result.stderr)
                self.assertIn("initialize", result.stderr)
                self.assertNotIn("Deploy verified", result.stderr)

    def test_auth_refusal_fails_the_deploy_instead_of_passing(self):
        """A 401 from the endpoint must not print "Deploy verified".

        The server is up but refusing the token clients will send; the deploy
        names MCP_AUTH_TOKEN as the thing to check and rolls back.
        """
        self.env["CURL_TEST_STATUS"] = "401"
        result = self.deploy(self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Deploy verification failed", result.stderr)
        self.assertIn("Authentication refused", result.stderr)
        self.assertIn("MCP_AUTH_TOKEN", result.stderr)
        self.assertNotIn("Deploy verified", result.stderr)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_failed_log_collection_does_not_prevent_rollback(self):
        prev_env = self.save_previous_deploy()
        newer_commit = self.add_newer_commit()
        self.env["CURL_TEST_STATUS"] = "401"
        self.env["DOCKER_TEST_FAIL_LOGS"] = "1"
        result = self.deploy(newer_commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not collect the moomoo-mcp logs", result.stderr)
        self.assertIn(f"Rolled back to {self.commit[:7]}", result.stderr)
        self.assertEqual(self.compose_commands().count("up"), 2)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.commit)
        self.assertEqual((self.repo / ".deploy.env").read_text(), prev_env)

    def test_too_old_host_python_fails_before_changing_anything(self):
        fake = self.bin / "python3"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        result = self.deploy(self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10 or newer", result.stderr)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD").strip(), "main")
        self.assertFalse((self.repo / ".deploy.env").exists())
        self.assertEqual(self.calls(), [])

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

    def test_missing_image_does_not_checkout_or_write_settings(self):
        self.env["AWS_TEST_MODE"] = "missing"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        # The script checks the short commit tag before declaring failure;
        # it does not fall through to :latest. The error message names the image.
        self.assertIn("moomoo-api-mcp", result.stderr)
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
        self.assertEqual(self.compose_commands(), ["config", "pull"])

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

        docker = [
            args
            for name, args, _ in self.calls()
            if name == "docker" and "compose" in args
        ]
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

        docker = [
            args
            for name, args, _ in self.calls()
            if name == "docker" and "compose" in args
        ]
        up_calls = [
            args for args in docker if args[-3:] == ["up", "-d", "--remove-orphans"]
        ]
        self.assertEqual(len(up_calls), 2)

    def test_failed_verification_without_previous_state(self):
        self.env["CURL_TEST_FAIL"] = "1"
        result = self.deploy(self.commit)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Deploy verified", result.stderr)

    def test_reexec_when_deploy_script_differs_in_target_commit(self):
        """Re-execute target deploy.sh when it differs in the target commit."""
        deploy_script = self.repo / "scripts/deploy.sh"
        original_content = deploy_script.read_text()
        marker = 'echo "REEXEC_MARKER_TEST_OK" >&2\n'
        deploy_script.write_text(marker + original_content)
        self.git("add", "scripts/deploy.sh")
        self.git("commit", "-m", "update deploy script with marker")
        newer_commit = self.git("rev-parse", "HEAD").strip()

        # Detach working tree back to original commit, simulating an older host checkout
        self.git("checkout", "--quiet", "--detach", self.commit)
        self.assertNotIn("REEXEC_MARKER_TEST_OK", deploy_script.read_text())

        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-executing latest deploy script", result.stderr)
        self.assertIn("REEXEC_MARKER_TEST_OK", result.stderr)
        self.assertIn(f"Deploy verified: {newer_commit[:7]}", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), newer_commit)
        reexec_leftovers = list((self.repo / "scripts").glob(".deploy.reexec*"))
        self.assertEqual(reexec_leftovers, [])

    def test_reexec_preserves_prepare_flag(self):
        """Self-reexec preserves command line flags like --prepare."""
        deploy_script = self.repo / "scripts/deploy.sh"
        original_content = deploy_script.read_text()
        marker = 'echo "REEXEC_PREPARE_MARKER" >&2\n'
        deploy_script.write_text(marker + original_content)
        self.git("add", "scripts/deploy.sh")
        self.git("commit", "-m", "update deploy script for prepare test")
        newer_commit = self.git("rev-parse", "HEAD").strip()

        self.git("checkout", "--quiet", "--detach", self.commit)

        result = self.deploy("--prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-executing latest deploy script", result.stderr)
        self.assertIn("REEXEC_PREPARE_MARKER", result.stderr)
        self.assertEqual(
            (self.repo / ".deploy.env").read_text(),
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG={newer_commit[:7]}\n",
        )
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), newer_commit)
        self.assertEqual(self.compose_commands(), ["config", "pull"])
        self.assertFalse(any(name == "curl" for name, _, _ in self.calls()))

    def commit_host_checkout(self, deploy_sh, helper):
        """Commit an older host checkout, then restore the current scripts on
        top of it as the target. Leaves the checkout on the older commit."""
        scripts = self.repo / "scripts"
        (scripts / "deploy.sh").write_text(deploy_sh)
        if helper is None:
            self.git("rm", "--quiet", "scripts/deploy_verify.py")
        else:
            (scripts / "deploy_verify.py").write_text(helper)
        self.git("add", "scripts")
        self.git("commit", "-m", "older host checkout")
        old_commit = self.git("rev-parse", "HEAD").strip()
        for name in ("deploy.sh", "deploy_verify.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        self.git("add", "scripts")
        self.git("commit", "-m", "target with the verifier")
        target = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "--quiet", "--detach", old_commit)
        return target

    def test_pre_helper_script_hands_over_to_the_target_helper(self):
        """The first deploy of the helper starts from a script that has none.

        The host runs the deploy.sh it has checked out — main's, from before
        the helper existed. It re-executes the target's script from a
        temporary file, and that script must find the helper in the target
        checkout: next to the temporary file there is nothing to find.
        """
        target = self.commit_host_checkout(PRE_HELPER_DEPLOY_SH.read_text(), None)
        self.assertFalse((self.repo / "scripts/deploy_verify.py").exists())

        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-executing latest deploy script", result.stderr)
        self.assertIn(f"Deploy verified: {target[:7]}", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), target)
        self.assertEqual(
            self.compose_commands(), ["config", "pull", "up", "config", "logs"]
        )
        headers = [c[1] for c in self.calls() if c[0] == "curl-header"]
        self.assertEqual(headers, [["Authorization: Bearer test-only\n"]])

    def test_reexec_runs_the_target_helper_not_the_old_one(self):
        """A helper already on the host is the old commit's, and stays unused."""
        stale = "import sys\nprint('STALE HELPER', file=sys.stderr)\nsys.exit(1)\n"
        shebang, rest = (ROOT / "scripts/deploy.sh").read_text().split("\n", 1)
        old_script = f'{shebang}\necho "OLD DEPLOY SCRIPT" >&2\n{rest}'
        target = self.commit_host_checkout(old_script, stale)

        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("re-executing latest deploy script", result.stderr)
        self.assertNotIn("STALE HELPER", result.stderr)
        self.assertIn(f"Deploy verified: {target[:7]}", result.stderr)

    def test_no_reexec_when_deploy_script_identical(self):
        """When deploy.sh is identical, deployment proceeds without re-execution."""
        result = self.deploy(self.commit)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("re-executing latest deploy script", result.stderr)


if __name__ == "__main__":
    unittest.main()
