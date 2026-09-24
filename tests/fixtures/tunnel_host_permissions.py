#!/usr/bin/env python3
"""Exercise tunnel host file permissions with a real unprivileged process."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import install_private_chatgpt_credential as credential_installer  # pyright: ignore[reportMissingImports]

ROOT = Path("/tmp/tunnel-host-permissions")
CONFIG_DIRECTORY = ROOT / "etc" / "moomoo-chatgpt-tunnel"
SERVICE_UID = 42001
SERVICE_GID = 42001


def prepare_layout() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    CONFIG_DIRECTORY.mkdir(parents=True)
    os.chown(CONFIG_DIRECTORY, 0, SERVICE_GID)
    CONFIG_DIRECTORY.chmod(0o750)

    config = CONFIG_DIRECTORY / "tunnel-client.yaml"
    config.write_text("mcp:\n  server_urls: []\n", encoding="utf-8")
    os.chown(config, 0, SERVICE_GID)
    config.chmod(0o640)

    credential_installer.write_credential(
        CONFIG_DIRECTORY,
        "control-plane-api-key",
        "synthetic-fixture-value",
        owner_uid=0,
        owner_gid=0,
    )
    credential_installer.write_credential(
        CONFIG_DIRECTORY,
        "mcp-authorization",
        "Bearer synthetic-fixture-value",
        owner_uid=0,
        owner_gid=0,
    )


def run_as_service_identity() -> None:
    os.setgroups([SERVICE_GID])
    os.setgid(SERVICE_GID)
    os.setuid(SERVICE_UID)

    config = CONFIG_DIRECTORY / "tunnel-client.yaml"
    assert "server_urls" in config.read_text(encoding="utf-8")
    for name in ("control-plane-api-key", "mcp-authorization"):
        try:
            (CONFIG_DIRECTORY / name).read_bytes()
        except PermissionError:
            continue
        raise AssertionError(f"service identity could read source credential {name}")


def main() -> None:
    assert os.geteuid() == 0
    prepare_layout()
    directory_info = CONFIG_DIRECTORY.stat()
    assert (directory_info.st_uid, directory_info.st_gid) == (0, SERVICE_GID)
    assert stat.S_IMODE(directory_info.st_mode) == 0o750
    config_info = (CONFIG_DIRECTORY / "tunnel-client.yaml").stat()
    assert (config_info.st_uid, config_info.st_gid) == (0, SERVICE_GID)
    assert stat.S_IMODE(config_info.st_mode) == 0o640
    for name in ("control-plane-api-key", "mcp-authorization"):
        credential_info = (CONFIG_DIRECTORY / name).stat()
        assert (credential_info.st_uid, credential_info.st_gid) == (0, 0)
        assert stat.S_IMODE(credential_info.st_mode) == 0o600
    child = os.fork()
    if child == 0:
        try:
            run_as_service_identity()
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    print("tunnel host permissions passed for an unprivileged service identity")


if __name__ == "__main__":
    main()
