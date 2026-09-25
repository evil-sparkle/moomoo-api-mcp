#!/usr/bin/env python3
"""Root provisioning helper. Never print secret values or run the daemon as root.

Use a local Docker socket and a verified immutable image. The marker probe contains
no secrets. Host ownership is measured, never inferred from a subuid offset.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import stat
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

NAMES = ("control-plane-api-key", "mcp-authorization", "tunnel-id")


class StagingError(Exception):
    pass


def execute(args: list[str]) -> str:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    if result.returncode:
        raise StagingError("permission probe or provisioning command failed")
    return result.stdout


def mapped(identity: int, rows: list[list[int]]) -> int:
    matches = [
        outside + identity - inside
        for inside, outside, count in rows
        if inside <= identity < inside + count
    ]
    if len(matches) != 1:
        raise StagingError("ambiguous identity map")
    return matches[0]


def probe(docker_host: str, image: str) -> dict[str, int]:
    if not docker_host.startswith("unix:///"):
        raise StagingError("only a local Unix Docker socket is supported")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise StagingError("an immutable local image ID is required")
    socket = Path(docker_host.removeprefix("unix://"))
    daemon_uid = socket.stat().st_uid
    with tempfile.TemporaryDirectory(prefix="tunnel-identity-") as directory:
        root = Path(directory)
        if daemon_uid:
            execute(["setfacl", "-m", f"u:{daemon_uid}:rwx", directory])
        code = """import os,json
from pathlib import Path
p=Path('/probe/marker');p.touch(mode=0o600);os.chown(p,10002,10002)
maps={}
for k in ('uid','gid'):
    rows=Path('/proc/self/'+k+'_map').read_text().splitlines()
    maps[k]=[[int(x) for x in row.split()] for row in rows]
print(json.dumps(maps))"""
        raw = execute(
            [
                "docker",
                "--host",
                docker_host,
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={root},dst=/probe",
                "--entrypoint",
                "python",
                image,
                "-c",
                code,
            ]
        )
        maps = json.loads(raw)
        owner = (root / "marker").stat()
        uid, gid = mapped(10002, maps["uid"]), mapped(10002, maps["gid"])
        if (uid, gid) != (owner.st_uid, owner.st_gid):
            raise StagingError("observed marker ownership disagrees with identity maps")
        if uid in {0, daemon_uid, mapped(10001, maps["uid"])} or gid == 0:
            raise StagingError("runtime identity conflicts with a privileged identity")
        try:
            pwd.getpwuid(uid)
        except KeyError:
            pass
        else:
            raise StagingError("runtime UID belongs to an existing host account")
        try:
            existing_group = grp.getgrgid(gid)
        except KeyError:
            pass
        else:
            if existing_group.gr_mem:
                raise StagingError("runtime GID grants access to unrelated identities")
        return {"uid": uid, "gid": gid, "daemon_uid": daemon_uid}


def open_directory(path: Path) -> int:
    """Walk absolute parents with directory descriptors; no symlink traversal."""
    if not path.is_absolute() or ".." in path.parts:
        raise StagingError("absolute canonical directory required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            following = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = following
            info = os.fstat(fd)
            # /tmp is allowed only as a sticky ancestor for disposable fixtures.
            if info.st_uid != 0 or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise StagingError("unsafe directory ownership or writable parent")
        return fd
    except BaseException:
        os.close(fd)
        raise


def stage(master: Path, destination: Path, mapping: dict[str, int]) -> None:
    """Read root-only masters and atomically expose fixed-name 0400 mapped copies."""
    source_fd = open_directory(master)
    target_fd = open_directory(destination)
    try:
        if stat.S_IMODE(os.fstat(source_fd).st_mode) not in {0o700, 0o750}:
            raise StagingError("master directory must have mode 0700 or legacy 0750")
        for name in NAMES:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise StagingError("master must be a root-owned 0600 regular file")
                value = stream.read(8193)
                if not value or len(value) > 8192:
                    raise StagingError("master has an invalid size")
            temporary = ".stage-" + os.urandom(12).hex()
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=target_fd,
                )
                with os.fdopen(fd, "wb") as stream:
                    os.fchown(stream.fileno(), mapping["uid"], mapping["gid"])
                    os.fchmod(stream.fileno(), 0o400)
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Reject a destination symlink rather than silently replacing it.
                try:
                    old = os.stat(name, dir_fd=target_fd, follow_symlinks=False)
                    if not stat.S_ISREG(old.st_mode):
                        raise StagingError("destination is not a regular file")
                except FileNotFoundError:
                    pass
                os.replace(temporary, name, src_dir_fd=target_fd, dst_dir_fd=target_fd)
                os.fsync(target_fd)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=target_fd)
    finally:
        os.close(source_fd)
        os.close(target_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--master-directory", type=Path)
    parser.add_argument("--staging-directory", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("root provisioning is required; runtime remains non-root")
    try:
        mapping = probe(args.docker_host, args.image)
        if args.master_directory or args.staging_directory:
            if not args.master_directory or not args.staging_directory:
                raise StagingError("both master and staging directories are required")
            stage(args.master_directory, args.staging_directory, mapping)
            acl = ",".join(
                f"u:{uid}:--x" for uid in {mapping["uid"], mapping["daemon_uid"]} if uid
            )
            execute(["setfacl", "-b", str(args.staging_directory)])
            os.chmod(args.staging_directory, 0o700)
            execute(["setfacl", "-m", acl, str(args.staging_directory)])
        print(json.dumps(mapping, sort_keys=True))
        return 0
    except (OSError, ValueError, StagingError, subprocess.SubprocessError):
        print("tunnel staging failed: check identity mapping and protected directories")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
