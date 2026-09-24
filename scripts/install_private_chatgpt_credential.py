#!/usr/bin/env python3
"""Install a private ChatGPT tunnel credential from a no-echo terminal prompt."""

from __future__ import annotations

import argparse
import grp
import os
import stat
import sys
import tempfile
import termios
from contextlib import suppress
from pathlib import Path

DEFAULT_DIRECTORY = Path("/etc/moomoo-chatgpt-tunnel")
SERVICE_GROUP = "moomoo-tunnel"
CREDENTIAL_NAMES = {
    "control-plane-api-key": "control-plane-api-key",
    "mcp-authorization": "mcp-authorization",
}


class CredentialInstallError(Exception):
    """A credential could not be safely read or installed."""


def validate_secret(kind: str, value: str) -> None:
    """Reject empty, malformed, or terminal-control-bearing credential input."""
    if not value or value != value.strip():
        raise CredentialInstallError(
            "credential must be non-empty with no outer whitespace"
        )
    if len(value) > 8192 or any(
        not 0x20 <= ord(character) <= 0x7E for character in value
    ):
        raise CredentialInstallError("credential must contain printable ASCII only")
    if kind == "control-plane-api-key" and " " in value:
        raise CredentialInstallError("runtime API key must not contain spaces")
    if kind == "mcp-authorization" and not value.startswith("Bearer "):
        raise CredentialInstallError("MCP credential must start with 'Bearer '")
    if kind == "mcp-authorization" and not value.removeprefix("Bearer "):
        raise CredentialInstallError("MCP bearer value must not be empty")


def prompt_secret(kind: str, input_fd: int = 0, output_fd: int = 2) -> str:
    """Read one credential line while restoring terminal settings on every exit."""
    if not os.isatty(input_fd):
        raise CredentialInstallError(
            "credential input requires an interactive terminal"
        )
    try:
        original = termios.tcgetattr(input_fd)
    except termios.error:
        raise CredentialInstallError(
            "credential input terminal is unavailable"
        ) from None
    hidden = original.copy()
    hidden[3] &= ~(termios.ECHO | termios.ECHONL)
    label = (
        "runtime API key"
        if kind == "control-plane-api-key"
        else "MCP Authorization header"
    )
    try:
        termios.tcsetattr(input_fd, termios.TCSAFLUSH, hidden)
        os.write(output_fd, f"Enter {label}: ".encode())
        with os.fdopen(os.dup(input_fd), "r", encoding="utf-8", newline="") as stream:
            value = stream.readline(8194)
    finally:
        termios.tcsetattr(input_fd, termios.TCSAFLUSH, original)
        os.write(output_fd, b"\n")
    if not value.endswith(("\n", "\r")):
        raise CredentialInstallError("credential input was incomplete or too long")
    value = value.rstrip("\r\n")
    validate_secret(kind, value)
    return value


def validate_destination(directory: Path, expected_group_id: int) -> None:
    """Require the documented root-owned, service-group-readable directory."""
    try:
        info = directory.lstat()
    except OSError:
        raise CredentialInstallError("credential directory is unavailable") from None
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
        raise CredentialInstallError("credential directory must be a real directory")
    if info.st_uid != 0 or info.st_gid != expected_group_id:
        raise CredentialInstallError("credential directory has unexpected ownership")
    if stat.S_IMODE(info.st_mode) != 0o750:
        raise CredentialInstallError("credential directory must have mode 0750")


def write_credential(
    directory: Path,
    kind: str,
    value: str,
    *,
    owner_uid: int,
    owner_gid: int,
) -> Path:
    """Atomically replace a credential with restrictive ownership and mode."""
    validate_secret(kind, value)
    destination = directory / CREDENTIAL_NAMES[kind]
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".credential-", dir=directory
        )
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, owner_uid, owner_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        temporary_name = ""
        os.chown(destination, owner_uid, owner_gid, follow_symlinks=False)
        os.chmod(destination, 0o600, follow_symlinks=False)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        raise CredentialInstallError("credential could not be installed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name)
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=sorted(CREDENTIAL_NAMES))
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.geteuid() != 0:
        raise CredentialInstallError("credential installation requires root")
    try:
        service_group_id = grp.getgrnam(SERVICE_GROUP).gr_gid
    except KeyError:
        raise CredentialInstallError("service group does not exist") from None
    validate_destination(args.directory, service_group_id)
    value = prompt_secret(args.kind)
    destination = write_credential(
        args.directory,
        args.kind,
        value,
        owner_uid=0,
        owner_gid=0,
    )
    print(f"installed {destination.name} with root-only access")
    return os.EX_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("credential installation cancelled", file=sys.stderr)
        raise SystemExit(130) from None
    except CredentialInstallError as exc:
        raise SystemExit(f"credential install: {exc}") from None
