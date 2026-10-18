#!/usr/bin/env python3
"""Serialize deployment and lifecycle operations with explicit lock delegation."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

TOKEN_ENV = "MOOMOO_DEPLOY_LOCK_TOKEN"
ROOT_ENV = "MOOMOO_DEPLOY_LOCK_ROOT"


def lock_path() -> Path:
    root = os.environ.get(ROOT_ENV)
    directory = Path(root) if root else Path.home() / ".local/share/moomoo-api-mcp"
    return directory / ".deployment-lock"


def require_owner(path: Path, token: str) -> None:
    if not token or not secrets.compare_digest(
        json.loads((path / "owner.json").read_text()).get("token", ""), token
    ):
        raise ValueError("Deployment lock belongs to another caller.")


def acquire(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=0o700)
    token = secrets.token_hex(32)
    try:
        descriptor = os.open(
            path / "owner.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"token": token}, stream)
            stream.write("\n")
    except BaseException:
        path.rmdir()
        raise
    return token


def release(path: Path, token: str) -> None:
    require_owner(path, token)
    (path / "owner.json").unlink()
    path.rmdir()


@contextmanager
def deployment_lock():
    path = lock_path()
    delegated = os.environ.get(TOKEN_ENV, "")
    if delegated:
        require_owner(path, delegated)
        yield
        return
    token = acquire(path)
    previous = os.environ.get(TOKEN_ENV)
    os.environ[TOKEN_ENV] = token
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = previous
        release(path, token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["acquire", "release", "run"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.mode == "acquire":
            print(acquire(lock_path()))
        elif args.mode == "release":
            release(lock_path(), os.environ.get(TOKEN_ENV, ""))
        else:
            command = args.arguments
            if command[:1] == ["--"]:
                command = command[1:]
            if not command:
                raise ValueError("Select a lifecycle command.")
            with deployment_lock():
                return subprocess.run(command, check=False).returncode
    except (ValueError, OSError, KeyError, TypeError):
        print(
            "Deployment lock is unavailable; inspect its owner before retrying.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
