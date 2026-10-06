#!/usr/bin/env python3
"""Non-secret optional Compose selection and managed startup checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / ".chatgpt-deploy.json"


def validate(selection: dict) -> None:
    if (
        set(selection) != {"version", "image", "secret_directory", "project"}
        or selection["version"] != 1
    ):
        raise ValueError("unsupported tunnel selection schema")
    if not re.fullmatch(
        r"(?:[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/"
        r"moomoo-api-mcp@)?sha256:[0-9a-f]{64}",
        selection["image"],
    ):
        raise ValueError("select an immutable ECR digest or local image ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", selection["project"]):
        raise ValueError("use the existing Compose project identity")
    path = selection["secret_directory"]
    if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", path) or ".." in Path(path).parts:
        raise ValueError("use an absolute protected staging directory")


def save(selection: dict, path: Path = SELECTION) -> None:
    validate(selection)
    fd, temporary = tempfile.mkstemp(prefix=".chatgpt-selection-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(selection, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path: Path = SELECTION) -> dict:
    selection = json.loads(path.read_text())
    validate(selection)
    return selection


def check_legacy_inactive() -> None:
    """Legacy and Compose mechanisms must never be enabled together."""
    for operation in ("is-active", "is-enabled"):
        result = subprocess.run(
            ["systemctl", operation, "--quiet", "moomoo-chatgpt-tunnel.service"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        # systemctl: inactive/disabled/not-found are nonzero. Unknown errors
        # are not evidence that a possibly enabled legacy unit is safe.
        acceptable = {3, 4} if operation == "is-active" else {1, 4}
        if result.returncode not in acceptable:
            raise ValueError(
                "Stop and disable the legacy tunnel before Compose enablement"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--image", required=True)
    select.add_argument("--secret-directory", required=True)
    select.add_argument("--project", required=True)
    update = commands.add_parser("set-image")
    update.add_argument("--image", required=True)
    commands.add_parser("compose-values")
    commands.add_parser("check-start")
    # Retain the old command for existing operator scripts.
    commands.add_parser("check-release")
    commands.add_parser("disable")
    args = parser.parse_args()
    try:
        if args.command in {"check-start", "check-release"}:
            load()
            check_legacy_inactive()
        elif args.command == "select":
            # Selection does not start either mechanism. The wrapper checks legacy
            # exclusivity, authentication and mode before any Compose start.
            save(
                {
                    "version": 1,
                    "image": args.image,
                    "secret_directory": args.secret_directory,
                    "project": args.project,
                }
            )
            print("Selection saved; managed startup checks apply before enablement.")
        elif args.command == "compose-values":
            selection = load()
            for key in ("image", "secret_directory", "project"):
                print(selection[key])
        elif args.command == "set-image":
            selection = load()
            selection["image"] = args.image
            save(selection)
        elif args.command == "disable":
            if SELECTION.exists():
                wrapper = str(ROOT / "scripts/compose-prod.sh")
                subprocess.run([wrapper, "stop", "chatgpt-tunnel"], check=True)
                subprocess.run([wrapper, "rm", "-f", "chatgpt-tunnel"], check=True)
                SELECTION.unlink()
            print("Tunnel selection disabled; brokerage volumes retained.")
        return 0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        # No values are printed: validation diagnostics are fixed strings.
        print(str(exc) if isinstance(exc, ValueError) else "Tunnel selection failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
