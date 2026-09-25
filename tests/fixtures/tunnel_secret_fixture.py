"""Root-owned synthetic fixture sources. Never use this helper for real credentials."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "action", choices=["create", "rotate-runtime", "rotate-mcp", "remove"]
)
parser.add_argument("--directory", type=Path)
parser.add_argument("--daemon-uid", type=int, default=0)
parser.add_argument("--runtime-uid", type=int, default=10002)
args = parser.parse_args()
assert os.geteuid() == 0
if args.action == "create":
    directory = Path(tempfile.mkdtemp(prefix="tunnel-synthetic-secrets-"))
    directory.chmod(0o700)
    for name in ("master", "staged"):
        (directory / name).mkdir(mode=0o700)
    for name, value in (
        ("control-plane-api-key", "synthetic-runtime-key"),
        ("mcp-authorization", "Bearer synthetic-mcp-token"),
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
    else:
        path = directory / "master" / ".replacement"
        path.write_text(
            "synthetic-runtime-key-rotated"
            if args.action == "rotate-runtime"
            else "Bearer synthetic-mcp-token-rotated"
        )
        path.chmod(0o600)
        path.replace(
            directory
            / "master"
            / (
                "control-plane-api-key"
                if args.action == "rotate-runtime"
                else "mcp-authorization"
            )
        )
