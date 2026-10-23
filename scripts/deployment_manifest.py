#!/usr/bin/env python3
"""Validate public CI release records used by production deployment."""

import argparse
import ipaddress
import json
import os
from pathlib import Path

REPOSITORY = "evil-sparkle/moomoo-api-mcp"
HEX = "0123456789abcdef"


def require_hex(value, length, name):
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(char not in HEX for char in value)
    ):
        raise ValueError(f"{name} must be {length} lowercase hexadecimal characters.")
    return value


def validate_image(value):
    if not isinstance(value, str) or value.count("@sha256:") != 1:
        raise ValueError("Deployment requires an immutable ECR image digest.")
    repository, digest = value.split("@sha256:")
    require_hex(digest, 64, "Image digest")
    if not repository.endswith("/moomoo-api-mcp"):
        raise ValueError("Unexpected image repository.")
    registry = repository.removesuffix("/moomoo-api-mcp").split(".")
    if (
        len(registry) != 6
        or registry[1:3] != ["dkr", "ecr"]
        or registry[4:] != ["amazonaws", "com"]
    ):
        raise ValueError("Expected a private AWS ECR registry.")
    if len(registry[0]) != 12 or any(char not in "0123456789" for char in registry[0]):
        raise ValueError("Invalid ECR account ID.")
    if not registry[3] or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in registry[3]
    ):
        raise ValueError("Invalid ECR region.")
    return value


def validate_project(value):
    """Require an unambiguous Compose project identity."""
    first = "abcdefghijklmnopqrstuvwxyz0123456789"
    if (
        not isinstance(value, str)
        or not value
        or value[0] not in first
        or any(character not in first + "_-" for character in value)
    ):
        raise ValueError("Invalid Compose project name.")
    return value


def deployment_branch():
    """Read the one trusted publishing branch from repository configuration."""
    branch = os.environ.get("DEPLOY_BRANCH") or "main"
    if branch == "main":
        return branch
    parts = branch.split("/")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if (
        parts[0] != "hotfix"
        or len(parts) < 2
        or any(
            not part or any(char not in allowed for char in part) for part in parts[1:]
        )
    ):
        raise ValueError("DEPLOY_BRANCH must be main or an explicit hotfix/ branch.")
    return branch


def validate_run(run):
    if not isinstance(run, dict):
        raise ValueError("Invalid CI run record.")
    repository = run.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != REPOSITORY:
        raise ValueError("CI run belongs to another repository.")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("Select a completed successful CI run.")
    if run.get("head_branch") != deployment_branch() or run.get("event") not in [
        "push",
        "workflow_dispatch",
    ]:
        raise ValueError("Select a published CI run from the active deployment branch.")
    if run.get("path") != ".github/workflows/ci.yml":
        raise ValueError("Select the CI & Docker Build workflow.")
    require_hex(run.get("head_sha"), 40, "Source revision")
    if type(run.get("id")) is not int or run["id"] < 1:
        raise ValueError("Invalid CI run ID.")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] < 1:
        raise ValueError("Invalid CI run attempt.")
    return "moomoo-deployment-" + str(run["run_attempt"])


def automatic_request(run, event, branch_revision):
    """Bind an automatic request to its exact successful CI webhook attempt."""
    validate_run(run)
    require_hex(branch_revision, 40, "Current deployment branch revision")
    if run["event"] != "push":
        raise ValueError("Automatic deployment requires an active-branch push CI run.")
    if not isinstance(event, dict) or event.get("action") != "completed":
        raise ValueError("Automatic deployment requires a completed workflow event.")
    repository = event.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != REPOSITORY:
        raise ValueError("Workflow event belongs to another repository.")
    expected = event.get("workflow_run")
    if not isinstance(expected, dict):
        raise ValueError("Invalid workflow event CI record.")
    head_repository = expected.get("head_repository")
    if (
        not isinstance(head_repository, dict)
        or head_repository.get("full_name") != REPOSITORY
    ):
        raise ValueError("Automatic deployment requires a source from this repository.")
    for name in ["id", "run_attempt"]:
        if type(expected.get(name)) is not int or expected[name] < 1:
            raise ValueError("Invalid workflow event CI " + name + ".")
    for name in [
        "id",
        "run_attempt",
        "head_sha",
        "head_branch",
        "event",
        "status",
        "conclusion",
    ]:
        if expected.get(name) != run[name]:
            raise ValueError(
                "Workflow event does not match the selected CI " + name + "."
            )
    return {
        "ci_run_id": str(run["id"]),
        "ci_run_attempt": str(run["run_attempt"]),
        "deploy": "true" if run["head_sha"] == branch_revision else "false",
    }


def validate_manifest(manifest, run):
    validate_run(run)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema")) is not int
        or manifest["schema"] != 1
    ):
        raise ValueError("Unsupported deployment manifest.")
    revision = run["head_sha"]
    if manifest.get("repository") != REPOSITORY or manifest.get("revision") != revision:
        raise ValueError("Manifest does not match the successful CI source.")
    if type(manifest.get("run_id")) is not int or manifest["run_id"] != run.get("id"):
        raise ValueError("Manifest does not match the selected CI run.")
    if type(manifest.get("run_attempt")) is not int or manifest[
        "run_attempt"
    ] != run.get("run_attempt"):
        raise ValueError("Manifest does not match the successful CI run attempt.")
    image = validate_image(manifest.get("image"))
    tunnel_image = validate_image(manifest.get("tunnel_image"))
    if image.split("@")[0] != tunnel_image.split("@")[0]:
        raise ValueError("Application and tunnel must use the same ECR repository.")
    return {"revision": revision, "image": image, "tunnel_image": tunnel_image}


def select_release(manifest, run, automatic=False, branch_revision=None):
    selected = validate_manifest(manifest, run)
    if automatic:
        require_hex(branch_revision, 40, "Current deployment branch revision")
        if run["event"] != "push":
            raise ValueError(
                "Automatic deployment requires an active-branch push CI run."
            )
    selected["deploy"] = (
        "true" if not automatic or selected["revision"] == branch_revision else "false"
    )
    return selected


def deployment_settings(selected, environment):
    host = str(ipaddress.IPv4Address(environment["VPS_HOST"]))
    if ipaddress.IPv4Address(host) not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise ValueError("VPS_HOST must be a Tailscale IPv4 address.")
    user = environment["VPS_USER"]
    allowed_user = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not user or user == "root" or any(char not in allowed_user for char in user):
        raise ValueError("VPS_USER must be an explicit non-root application user.")
    port_text = environment["VPS_PORT"]
    if (
        not port_text
        or any(char not in "0123456789" for char in port_text)
        or not 1 <= int(port_text) <= 65535
    ):
        raise ValueError("Invalid VPS SSH port.")
    project = environment["MOOMOO_COMPOSE_PROJECT"]
    if (
        not project
        or project[0] not in "abcdefghijklmnopqrstuvwxyz0123456789"
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in project)
    ):
        raise ValueError("Select the existing lowercase Compose project name.")
    credentials = environment["MOOMOO_ENV_FILE"]
    if (
        not credentials.startswith("/")
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_.-"
            for char in credentials
        )
        or ".." in Path(credentials).parts
    ):
        raise ValueError("MOOMOO_ENV_FILE must be an absolute server-side path.")
    tunnel = environment.get("MOOMOO_TUNNEL_ENABLED", "false")
    if tunnel not in ["true", "false"]:
        raise ValueError("MOOMOO_TUNNEL_ENABLED must be true or false.")
    expected_mode = environment.get("MOOMOO_EXPECTED_MODE", "READ_ONLY")
    if expected_mode not in {"READ_ONLY", "SIMULATE", "REAL"}:
        raise ValueError("MOOMOO_EXPECTED_MODE must be READ_ONLY, SIMULATE or REAL.")
    inventory = {
        "vps": {
            "hosts": {
                "vultr": {
                    "ansible_host": host,
                    "ansible_port": int(port_text),
                    "ansible_user": user,
                    "ansible_python_interpreter": "/usr/bin/python3",
                }
            }
        }
    }
    settings = {
        "moomoo_mcp_revision": require_hex(selected["revision"], 40, "Source revision"),
        "moomoo_mcp_image": validate_image(selected["image"]),
        "moomoo_mcp_source_dir": str(Path("moomoo-api-mcp").resolve()),
        "moomoo_mcp_tunnel_image": validate_image(selected["tunnel_image"]),
        "moomoo_mcp_project": project,
        "moomoo_mcp_env_file": credentials,
        "moomoo_mcp_tunnel_enabled": tunnel == "true",
        "moomoo_mcp_expected_mode": expected_mode,
    }
    return inventory, settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("branch")
    publish = sub.add_parser("publish")
    publish.add_argument("--image", required=True)
    publish.add_argument("--tunnel-image", required=True)
    publish.add_argument("--output", required=True, type=Path)
    inspect_run = sub.add_parser("run")
    inspect_run.add_argument("--run", required=True, type=Path)
    inspect_run.add_argument("--output", required=True, type=Path)
    automatic = sub.add_parser("automatic")
    automatic.add_argument("--run", required=True, type=Path)
    automatic.add_argument("--event", required=True, type=Path)
    automatic.add_argument("--branch-revision", "--main-revision", required=True)
    automatic.add_argument("--output", required=True, type=Path)
    select = sub.add_parser("select")
    select.add_argument("--run", required=True, type=Path)
    select.add_argument("--manifest", required=True, type=Path)
    select.add_argument("--output", required=True, type=Path)
    select.add_argument("--automatic", action="store_true")
    select.add_argument("--branch-revision", "--main-revision")
    configure = sub.add_parser("configure")
    configure.add_argument("--revision", required=True)
    configure.add_argument("--image", required=True)
    configure.add_argument("--tunnel-image", required=True)
    configure.add_argument("--inventory", required=True, type=Path)
    configure.add_argument("--settings", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "branch":
            print(deployment_branch())
        elif args.mode == "publish":
            manifest = {
                "schema": 1,
                "repository": REPOSITORY,
                "revision": require_hex(
                    os.environ["GITHUB_SHA"], 40, "Source revision"
                ),
                "image": validate_image(args.image),
                "tunnel_image": validate_image(args.tunnel_image),
                "run_id": int(os.environ["GITHUB_RUN_ID"]),
                "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            }
            validate_manifest(
                manifest,
                {
                    "repository": {"full_name": REPOSITORY},
                    "head_sha": manifest["revision"],
                    "id": manifest["run_id"],
                    "run_attempt": manifest["run_attempt"],
                    "path": ".github/workflows/ci.yml",
                    "head_branch": os.environ["GITHUB_REF_NAME"],
                    "event": os.environ["GITHUB_EVENT_NAME"],
                    "status": "completed",
                    "conclusion": "success",
                },
            )
            args.output.write_text(json.dumps(manifest) + "\n")
        elif args.mode == "run":
            args.output.write_text(
                validate_run(json.loads(args.run.read_text())) + "\n"
            )
        elif args.mode == "automatic":
            requested = automatic_request(
                json.loads(args.run.read_text()),
                json.loads(args.event.read_text()),
                args.branch_revision,
            )
            args.output.write_text(
                "".join(key + "=" + value + "\n" for key, value in requested.items())
            )
        elif args.mode == "select":
            selected = select_release(
                json.loads(args.manifest.read_text()),
                json.loads(args.run.read_text()),
                args.automatic,
                args.branch_revision,
            )
            args.output.write_text(
                "".join(key + "=" + value + "\n" for key, value in selected.items())
            )
        else:
            inventory, settings = deployment_settings(
                {
                    "revision": args.revision,
                    "image": args.image,
                    "tunnel_image": args.tunnel_image,
                },
                os.environ,
            )
            args.inventory.write_text(json.dumps(inventory) + "\n")
            args.settings.write_text(json.dumps(settings) + "\n")
    except (ValueError, KeyError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
