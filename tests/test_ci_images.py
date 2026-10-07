"""Execute image-path filtering and ECR tagging with disposable command stubs."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
BASELINE = "a" * 40
COMMIT = "b" * 40


def step(job, identifier):
    return next(s for s in WORKFLOW["jobs"][job]["steps"] if s.get("id") == identifier)


def run_step(
    tmp_path,
    script,
    *,
    changed="",
    prefix="",
    baseline_missing=False,
    event="push",
    ref="refs/heads/main",
    invalid_base=False,
    diff_failure=False,
):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    git = binaries / "git"
    git.write_text("""#!/usr/bin/env python3
import os,sys
if sys.argv[1] == 'diff':
    if os.environ['DIFF_FAILURE'] == '1': sys.exit(1)
    print(os.environ['CHANGED_PATHS'])
elif sys.argv[1] == 'rev-parse' and os.environ['INVALID_BASE'] == '1': sys.exit(1)
elif sys.argv[1] == 'tag': print('v0.1.0')
else: print(os.environ['BASELINE'])
""")
    aws = binaries / "aws"
    aws.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
with pathlib.Path(os.environ['AWS_LOG']).open('a') as log:
    log.write(json.dumps(args)+'\\n')
if 'put-image' not in args:
    if os.environ['BASELINE_MISSING'] == '1': print('None')
    elif 'images[0].imageManifest' in args: print('{}')
    else: print('sha256:'+'c'*64)
""")
    for binary in (git, aws):
        binary.chmod(0o755)
    output = tmp_path / "output"
    output.touch()
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "CHANGED_PATHS": changed,
        "BASELINE": BASELINE,
        "BASELINE_MISSING": "1" if baseline_missing else "0",
        "AWS_LOG": str(tmp_path / "aws.jsonl"),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_SHA": COMMIT,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "INVALID_BASE": "1" if invalid_base else "0",
        "DIFF_FAILURE": "1" if diff_failure else "0",
        "TAG_PREFIX": prefix,
        "ECR_REPOSITORY": "moomoo-api-mcp",
        "PUSH": "true",
    }
    for expression, value in {
        "github.event.before": BASELINE,
        "github.event.pull_request.base.sha": BASELINE,
        "vars.AWS_REGION": "us-east-1",
        "needs.changes.outputs.baseline": BASELINE,
        "steps.ecr.outputs.registry": "123456789012.dkr.ecr.us-east-1.amazonaws.com",
        "env.IMAGE": "moomoo-chatgpt-tunnel" if prefix else "moomoo-api-mcp",
    }.items():
        script = script.replace("${{ " + expression + " }}", value)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize(
    "path,mcp,tunnel,tests",
    [
        ("README.md", True, False, False),
        ("docs/deploy-vps.md", False, False, False),
        ("src/moomoo_mcp/server.py", True, False, True),
        (".github/workflows/ci.yml", True, True, True),
        ("docker-compose.chatgpt.yml", False, False, True),
        *[
            (path, False, True, True)
            for path in (
                "deploy/tunnel-client/container/Dockerfile",
                "deploy/tunnel-client/container/runtime.py",
                "deploy/tunnel-client/container/tunnel-client.yaml",
                "deploy/tunnel-client/install.py",
                "deploy/tunnel-client/release.json",
                "scripts/private_chatgpt_preflight.py",
                "scripts/build-tunnel-image.sh",
                "scripts/prepare-tunnel-build-context.sh",
            )
        ],
    ],
)
@pytest.mark.parametrize(
    "event,ref",
    [
        ("push", "refs/heads/main"),
        ("pull_request", "refs/pull/99/merge"),
        ("push", "refs/heads/feature/example"),
    ],
)
def test_image_filter_covers_build_inputs_on_pushes_and_prs(
    tmp_path, path, mcp, tunnel, tests, event, ref
):
    outputs = run_step(
        tmp_path, step("changes", "filter")["run"], changed=path, event=event, ref=ref
    )
    assert outputs["moomoo-api-mcp"] == str(mcp).lower()
    assert outputs["moomoo-chatgpt-tunnel"] == str(tunnel).lower()
    assert outputs["tests"] == str(tests).lower()


@pytest.mark.parametrize("prefix", ["", "tunnel-"])
@pytest.mark.parametrize("missing", [False, True])
def test_unchanged_image_retag_uses_its_own_namespace_or_builds(
    tmp_path, prefix, missing
):
    outputs = run_step(
        tmp_path,
        step("docker-build", "retag")["run"],
        prefix=prefix,
        baseline_missing=missing,
    )
    assert outputs["rebuild_needed"] == str(missing).lower()
    calls = [
        json.loads(line) for line in (tmp_path / "aws.jsonl").read_text().splitlines()
    ]
    lookups = [args for args in calls if "batch-get-image" in args]
    assert all(
        args[args.index("--repository-name") + 1] == "moomoo-api-mcp" for args in calls
    )
    assert all(
        args[args.index("--image-ids") + 1].startswith("imageTag=" + prefix)
        for args in lookups
    )
    tags = [
        args[args.index("--image-tag") + 1] for args in calls if "put-image" in args
    ]
    assert tags == (
        []
        if missing
        else [prefix + COMMIT[:7], prefix + "commit-" + COMMIT, prefix + "v0.1.0"]
    )


@pytest.mark.parametrize("prefix", ["", "tunnel-"])
def test_published_tags_are_disjoint_and_match_deploy_lookup(tmp_path, prefix):
    outputs = run_step(tmp_path, step("docker-build", "tags")["run"], prefix=prefix)
    tags = [tag.rsplit(":", 1)[1] for tag in outputs["tags"].split(",")]
    assert tags == [
        prefix + COMMIT,
        prefix + "latest",
        prefix + COMMIT[:7],
        prefix + "commit-" + COMMIT,
        prefix + "v0.1.0",
    ]


@pytest.mark.parametrize(
    "changed,tests,smoke,mcp,tunnel",
    [
        ("docs/reports/investigation.md", False, False, False, False),
        (
            "openspec/profile.json\nscripts/update-openspec.sh\n"
            "scripts/validate-openspec.sh\n.agents/skills/example/SKILL.md",
            False,
            False,
            False,
            False,
        ),
        (
            "scripts/deploy.sh\nscripts/deploy_verify.py\n"
            "tests/test_deploy_verify.py\n.env.example",
            True,
            False,
            False,
            False,
        ),
        ("tests/test_tools/test_accounting_guidance.py", True, False, False, False),
        ("docs/guide.md\nsrc/moomoo_mcp/tools/account.py", True, True, True, False),
        ("docker-compose.smoke.yml", True, True, False, False),
        ("docker-compose.paper.yml", True, True, False, False),
        ("scripts/test-paper-container.sh", True, True, False, False),
        ("tests/fixtures/paper_container_checks.py", True, True, False, False),
        ("tests/fixtures/opend_stub.py", True, True, False, False),
        (".dockerignore", True, True, True, False),
        ("pyproject.toml", True, True, True, False),
        ("uv.lock", True, True, True, False),
        ("unknown-config.toml", True, False, False, False),
    ],
)
def test_check_scopes_for_tooling_deployment_and_mixed_changes(
    tmp_path, changed, tests, smoke, mcp, tunnel
):
    outputs = run_step(
        tmp_path,
        step("changes", "filter")["run"],
        changed=changed,
        event="pull_request",
        ref="refs/pull/99/merge",
    )
    for key, expected in {
        "tests": tests,
        "smoke": smoke,
        "moomoo-api-mcp": mcp,
        "moomoo-chatgpt-tunnel": tunnel,
    }.items():
        assert outputs[key] == str(expected).lower(), key


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("failure", ["invalid_base", "diff_failure", "empty"])
def test_unreliable_comparison_runs_all_checks_and_builds(tmp_path, event, failure):
    outputs = run_step(
        tmp_path,
        step("changes", "filter")["run"],
        event=event,
        changed="" if failure == "empty" else "docs/guide.md",
        invalid_base=failure == "invalid_base",
        diff_failure=failure == "diff_failure",
    )
    for key in ("tests", "smoke", "moomoo-api-mcp", "moomoo-chatgpt-tunnel"):
        assert outputs[key] == "true", key
    assert outputs["baseline"] == ""


def test_jobs_use_independent_check_outputs_and_keep_main_retagging():
    jobs = WORKFLOW["jobs"]
    assert jobs["test"]["if"] == "needs.changes.outputs.tests == 'true'"
    assert jobs["smoke"]["needs"] == ["test", "changes"]
    assert jobs["smoke"]["if"] == "needs.changes.outputs.smoke == 'true'"
    assert "needs.test.result == 'skipped'" in jobs["docker-build"]["if"]
    assert (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        in jobs["docker-build"]["if"]
    )
    assert step("docker-build", "retag")["if"] == (
        "env.PUSH == 'true' && needs.changes.outputs[matrix.image] != 'true'"
    )


def tunnel_workflow_matches(path):
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/tunnel-compatibility.yml").read_text()
    )
    # PyYAML's YAML 1.1 loader treats the unquoted GitHub key `on` as True.
    triggers = workflow[True]["pull_request"]["paths"]
    for pattern in triggers:
        if "*" not in pattern:
            if path == pattern:
                return True
        else:
            prefix, suffix = pattern.split("*", 1)
            suffix = suffix.lstrip("*")
            if path.startswith(prefix) and path.endswith(suffix):
                return True
    return False


@pytest.mark.parametrize(
    "path,expected",
    [
        ("scripts/update-openspec.sh", False),
        ("scripts/validate-openspec.sh", False),
        ("scripts/deploy.sh", False),
        ("scripts/deploy_verify.py", False),
        ("tests/test_deploy_scripts.py", False),
        ("tests/test_tools/test_accounting_guidance.py", False),
        ("src/moomoo_mcp/tools/account.py", False),
        ("src/moomoo_mcp/tools/trading.py", False),
        ("src/moomoo_mcp/server.py", True),
        ("src/moomoo_mcp/settings.py", True),
        ("src/moomoo_mcp/services/health.py", True),
        ("src/moomoo_mcp/services/execution_store.py", True),
        ("src/moomoo_mcp/tools/offload.py", True),
        ("Dockerfile", True),
        (".dockerignore", True),
        ("pyproject.toml", True),
        ("uv.lock", True),
        ("docker-compose.chatgpt.yml", True),
        ("scripts/private_chatgpt_preflight.py", True),
        ("scripts/prepare-tunnel-build-context.sh", True),
        ("scripts/test-tunnel-container.sh", True),
        ("deploy/tunnel-client/container/runtime.py", True),
        ("tests/fixtures/tunnel_container_checks.py", True),
        ("tests/fixtures/opend_stub.py", True),
        ("tests/test_tunnel_runtime.py", True),
        ("tests/conftest.py", True),
        (".github/workflows/ci.yml", True),
        (".github/workflows/tunnel-compatibility.yml", True),
    ],
)
def test_tunnel_workflow_only_runs_for_relevant_inputs(path, expected):
    assert tunnel_workflow_matches(path) is expected
