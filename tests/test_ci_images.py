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


def run_step(tmp_path, script, *, changed="", prefix="", baseline_missing=False):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    git = binaries / "git"
    git.write_text("""#!/usr/bin/env python3
import os,sys
if sys.argv[1] == 'diff': print(os.environ['CHANGED_PATHS'])
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
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
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
    "path,mcp,tunnel,docs_only",
    [
        ("README.md", True, False, True),
        ("docs/private-chatgpt-mcp.md", False, False, True),
        ("src/moomoo_mcp/server.py", True, False, False),
        (".github/workflows/ci.yml", True, True, False),
        ("docker-compose.chatgpt.yml", False, True, False),
        *[
            (path, False, True, False)
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
def test_main_image_filter_covers_all_build_inputs(
    tmp_path, path, mcp, tunnel, docs_only
):
    outputs = run_step(tmp_path, step("changes", "filter")["run"], changed=path)
    assert outputs["moomoo-api-mcp"] == str(mcp).lower()
    assert outputs["moomoo-chatgpt-tunnel"] == str(tunnel).lower()
    assert outputs["docs_only"] == str(docs_only).lower()


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
