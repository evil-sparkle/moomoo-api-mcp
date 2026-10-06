"""Root-owned synthetic fixture sources. Never use this helper for real credentials."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "action",
    choices=["create", "rotate-runtime", "verify-rejections", "remove"],
)
parser.add_argument("--directory", type=Path)
parser.add_argument("--daemon-uid", type=int, default=0)
parser.add_argument("--runtime-uid", type=int, default=10002)
parser.add_argument("--runtime-gid", type=int, default=10002)
args = parser.parse_args()
assert os.geteuid() == 0
if args.action == "create":
    directory = Path(tempfile.mkdtemp(prefix="tunnel-synthetic-secrets-"))
    directory.chmod(0o700)
    for name in ("master", "staged"):
        (directory / name).mkdir(mode=0o700)
    for name, value in (
        ("control-plane-api-key", "synthetic-runtime-key"),
        ("tunnel-id", "tunnel_0123456789abcdef0123456789abcdef"),
    ):
        path = directory / "master" / name
        path.write_text(value)
        path.chmod(0o600)
    acl = ",".join(f"u:{uid}:--x" for uid in {args.daemon_uid, args.runtime_uid} if uid)
    subprocess.run(["setfacl", "-m", acl, str(directory)], check=True)
    print(json.dumps({"directory": str(directory)}))
else:
    directory = args.directory
    assert directory is not None
    assert directory.parent == Path("/tmp")
    assert directory.name.startswith("tunnel-synthetic-secrets-")
    assert directory.lstat().st_uid == 0 and not directory.is_symlink()
    if args.action == "remove":
        shutil.rmtree(directory)
    elif args.action == "verify-rejections":
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from unittest.mock import patch

        from scripts import stage_tunnel_secrets as staging

        mapping = {
            "uid": args.runtime_uid,
            "gid": args.runtime_gid,
            "daemon_uid": args.daemon_uid,
        }
        master, target = directory / "master", directory / "staged"
        for area in (master, target):
            credential = area / "control-plane-api-key"
            saved = area / ".saved-synthetic"
            credential.rename(saved)
            credential.symlink_to(master / "tunnel-id")
            try:
                try:
                    staging.stage(master, target, mapping)
                except (OSError, staging.StagingError):
                    pass
                else:
                    raise AssertionError("symlink accepted")
            finally:
                credential.unlink()
                saved.rename(credential)
        target.chmod(0o770)
        try:
            try:
                staging.stage(master, target, mapping)
            except staging.StagingError:
                pass
            else:
                raise AssertionError("writable staging parent accepted")
        finally:
            target.chmod(0o700)
        with patch.object(staging.os, "replace", side_effect=OSError("injected")):
            try:
                staging.stage(master, target, mapping)
            except OSError:
                pass
            else:
                raise AssertionError("injected atomic write failure ignored")
        assert not list(target.glob(".stage-*"))
        for source in master.iterdir():
            assert source.stat().st_uid == 0
            assert source.stat().st_mode & 0o777 == 0o600
        print("PASS: source/target symlinks, unsafe parent and atomic failure refused")
    else:
        path = directory / "master" / ".replacement"
        path.write_text("synthetic-runtime-key-rotated")
        path.chmod(0o600)
        path.replace(directory / "master" / "control-plane-api-key")
