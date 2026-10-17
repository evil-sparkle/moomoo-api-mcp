#!/usr/bin/env python3
"""Non-secret optional Compose selection and managed startup checks."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from .deployment_manifest import require_hex, validate_image, validate_project
else:
    from deployment_manifest import require_hex, validate_image, validate_project

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / ".chatgpt-deploy.json"


def validate(selection: dict) -> None:
    if set(selection) != {"version", "image", "project"} or selection["version"] != 2:
        raise ValueError("unsupported tunnel selection schema")
    try:
        image = selection["image"]
        if isinstance(image, str) and image.startswith("sha256:"):
            require_hex(image.removeprefix("sha256:"), 64, "Image digest")
        else:
            validate_image(image)
    except ValueError:
        raise ValueError("select an immutable ECR digest or local image ID") from None
    try:
        validate_project(selection["project"])
    except ValueError:
        raise ValueError("use the existing Compose project identity") from None


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--image", required=True)
    select.add_argument("--project", required=True)
    update = commands.add_parser("set-image")
    update.add_argument("--image", required=True)
    commands.add_parser("compose-values")
    commands.add_parser("check-start")
    commands.add_parser("disable")
    args = parser.parse_args()
    try:
        if args.command == "check-start":
            load()
        elif args.command == "select":
            # Called by deploy.sh; authentication/mode are checked before start.
            save(
                {
                    "version": 2,
                    "image": args.image,
                    "project": args.project,
                }
            )
            print("Selection saved; managed startup checks apply before enablement.")
        elif args.command == "compose-values":
            selection = load()
            for key in ("image", "project"):
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
