"""Validate release selection using synthetic CI records and public metadata."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from deployment_manifest import (  # noqa: E402
    REPOSITORY,
    automatic_request,
    deployment_branch,
    deployment_settings,
    select_release,
    validate_image,
    validate_manifest,
    validate_run,
)

IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/moomoo-api-mcp@sha256:" + "a" * 64
TUNNEL = IMAGE.replace("a" * 64, "c" * 64)


def run_record():
    return {
        "id": 123,
        "run_attempt": 2,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": REPOSITORY},
    }


def manifest():
    return {
        "schema": 1,
        "repository": REPOSITORY,
        "revision": "b" * 40,
        "run_id": 123,
        "run_attempt": 2,
        "image": IMAGE,
        "tunnel_image": TUNNEL,
    }


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DEPLOY_BRANCH": ""}))

    def test_deployment_branch_defaults_and_rejects_unsafe_configuration(self):
        self.assertEqual(deployment_branch(), "main")
        for branch in [
            "main",
            "hotfix/security-emudhra-20261023",
            "hotfix/nested/Fix_1",
        ]:
            with (
                self.subTest(branch=branch),
                patch.dict(os.environ, {"DEPLOY_BRANCH": branch}),
            ):
                self.assertEqual(deployment_branch(), branch)
        for branch in [
            "feature/other",
            "refs/heads/main",
            "hotfix/",
            "hotfix//other",
            "hotfix/../main",
            "hotfix/a.lock",
            "hotfix/a b",
            "hotfix/a\nmain",
            "hotfix/a?b",
            "hotfix/a@{b}",
            "hotfix/café",
        ]:
            with (
                self.subTest(branch=branch),
                patch.dict(os.environ, {"DEPLOY_BRANCH": branch}),
                self.assertRaises(ValueError),
            ):
                deployment_branch()

    def test_only_the_configured_branch_can_supply_a_release(self):
        branch = "hotfix/security-emudhra-20261023"
        with self.assertRaises(ValueError):
            validate_run(run_record() | {"head_branch": branch})
        with patch.dict(os.environ, {"DEPLOY_BRANCH": branch}):
            for event in ["push", "workflow_dispatch"]:
                run = run_record() | {"head_branch": branch, "event": event}
                self.assertEqual(validate_run(run), "moomoo-deployment-2")
                self.assertEqual(select_release(manifest(), run)["deploy"], "true")
            for other in ["main", "hotfix/other", "feature/other"]:
                with self.subTest(branch=other), self.assertRaises(ValueError):
                    validate_run(run_record() | {"head_branch": other})
            with self.assertRaises(ValueError):
                select_release(manifest(), run, True, "b" * 40)

    def test_hotfix_automatic_requests_keep_exact_attempt_and_freshness_checks(self):
        with patch.dict(
            os.environ, {"DEPLOY_BRANCH": "hotfix/security-emudhra-20261023"}
        ):
            run = run_record() | {"head_branch": deployment_branch()}
            event = {
                "action": "completed",
                "repository": {"full_name": REPOSITORY},
                "workflow_run": run | {"head_repository": {"full_name": REPOSITORY}},
            }
            self.assertEqual(automatic_request(run, event, "b" * 40)["deploy"], "true")
            self.assertEqual(automatic_request(run, event, "d" * 40)["deploy"], "false")
            self.assertEqual(
                select_release(manifest(), run, True, "d" * 40)["deploy"], "false"
            )
            self.assertEqual(select_release(manifest(), run)["deploy"], "true")
            for changes in [
                {"run_attempt": 3},
                {"head_branch": "main"},
                {"head_repository": {"full_name": "fork/repo"}},
            ]:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    automatic_request(
                        run,
                        event | {"workflow_run": event["workflow_run"] | changes},
                        "b" * 40,
                    )

    def test_release_binds_source_run_attempt_and_both_digests(self):
        self.assertEqual(
            validate_manifest(manifest(), run_record()),
            {
                "revision": "b" * 40,
                "image": IMAGE,
                "tunnel_image": TUNNEL,
            },
        )
        changes = {
            "conclusion": "failure",
            "status": "in_progress",
            "event": "pull_request",
            "head_branch": "feature",
            "head_sha": "d" * 40,
            "id": 124,
            "run_attempt": 1,
            "repository": {"full_name": "other/repo"},
            "path": ".github/workflows/deploy.yml",
        }
        for name, value in changes.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_manifest(manifest(), run_record() | {name: value})
        self.assertEqual(validate_run(run_record()), "moomoo-deployment-2")
        without_tunnel = manifest()
        del without_tunnel["tunnel_image"]
        with self.assertRaises(ValueError):
            validate_manifest(without_tunnel, run_record())
        for key in ["image", "tunnel_image"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_manifest(
                    manifest() | {key: IMAGE.split("@")[0] + ":latest"}, run_record()
                )

    def test_rejects_injected_and_foreign_images(self):
        for value in [
            IMAGE + "\nextra=value",
            IMAGE.replace("/moomoo-api-mcp", "/other"),
            IMAGE.replace("a" * 64, "A" * 64),
            IMAGE.replace(
                "123456789012.dkr.ecr.us-east-1.amazonaws.com", "example.invalid"
            ),
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_image(value)
        with self.assertRaises(ValueError):
            validate_manifest(
                manifest()
                | {"tunnel_image": TUNNEL.replace("123456789012", "999999999999")},
                run_record(),
            )

    def test_automatic_event_is_exact_and_obsolete_runs_skip(self):
        run = run_record()
        event = {
            "action": "completed",
            "repository": {"full_name": REPOSITORY},
            "workflow_run": run | {"head_repository": {"full_name": REPOSITORY}},
        }
        self.assertEqual(automatic_request(run, event, "b" * 40)["deploy"], "true")
        self.assertEqual(automatic_request(run, event, "d" * 40)["deploy"], "false")
        for name, value in {
            "id": 456,
            "run_attempt": 3,
            "head_sha": "d" * 40,
            "head_repository": {"full_name": "fork/repo"},
        }.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                automatic_request(
                    run,
                    event | {"workflow_run": event["workflow_run"] | {name: value}},
                    "b" * 40,
                )
        self.assertEqual(
            select_release(manifest(), run, True, "d" * 40)["deploy"], "false"
        )
        self.assertEqual(
            select_release(manifest(), run, False, "d" * 40)["deploy"], "true"
        )
        for name in ["id", "run_attempt"]:
            for value in [True, 0, -1, "123"]:
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(ValueError),
                ):
                    validate_run(run | {name: value})

    def test_settings_require_explicit_state_identity_and_server_credentials(self):
        environment = {
            "VPS_HOST": "100.64.0.1",
            "VPS_USER": "synthetic-user",
            "VPS_PORT": "23218",
            "MOOMOO_COMPOSE_PROJECT": "existing-moomoo",
            "MOOMOO_ENV_FILE": "/home/synthetic-user/moomoo/.env",
            "MOOMOO_TUNNEL_ENABLED": "true",
        }
        inventory, value = deployment_settings(
            validate_manifest(manifest(), run_record()), environment
        )
        self.assertEqual(inventory["vps"]["hosts"]["vultr"]["ansible_port"], 23218)
        self.assertEqual(value["moomoo_mcp_project"], "existing-moomoo")
        self.assertEqual(value["moomoo_mcp_expected_mode"], "READ_ONLY")
        self.assertTrue(value["moomoo_mcp_tunnel_enabled"])
        for mode in ["READ_ONLY", "SIMULATE", "REAL"]:
            _, configured = deployment_settings(
                validate_manifest(manifest(), run_record()),
                environment | {"MOOMOO_EXPECTED_MODE": mode},
            )
            self.assertEqual(configured["moomoo_mcp_expected_mode"], mode)
        self.assertFalse(
            any("key" in key or "token" in key or "password" in key for key in value)
        )
        for name, setting in {
            "VPS_HOST": "203.0.113.1",
            "VPS_USER": "root",
            "VPS_PORT": "0",
            "MOOMOO_COMPOSE_PROJECT": "../other",
            "MOOMOO_ENV_FILE": "relative/.env",
            "MOOMOO_TUNNEL_ENABLED": "yes",
            "MOOMOO_EXPECTED_MODE": "unknown",
        }.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                deployment_settings(
                    validate_manifest(manifest(), run_record()),
                    environment | {name: setting},
                )

    def test_cli_does_not_emit_selection_after_manifest_mismatch(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "run.json").write_text(json.dumps(run_record()))
            (root / "release.json").write_text(
                json.dumps(manifest() | {"run_attempt": 1})
            )
            output = root / "output"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/deployment_manifest.py"),
                    "select",
                    "--run",
                    str(root / "run.json"),
                    "--manifest",
                    str(root / "release.json"),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
