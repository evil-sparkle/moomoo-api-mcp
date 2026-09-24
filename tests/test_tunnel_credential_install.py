"""Behavioral tests for no-echo tunnel credential installation."""

from __future__ import annotations

import os
import pty
import select
import signal
import stat
import subprocess
import sys
import termios
import time
from pathlib import Path

from scripts import install_private_chatgpt_credential as installer

ROOT = Path(__file__).resolve().parents[1]


def read_until(master_fd: int, marker: bytes, timeout: float = 3) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while marker not in output and time.monotonic() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.1)
        if readable:
            output.extend(os.read(master_fd, 4096))
    assert marker in output, bytes(output)
    return bytes(output)


def drain(master_fd: int, process: subprocess.Popen[bytes]) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        readable, _, _ = select.select([master_fd], [], [], 0.05)
        if readable:
            try:
                output.extend(os.read(master_fd, 4096))
            except OSError:
                break
        if process.poll() is not None and not readable:
            break
    process.wait(timeout=1)
    return bytes(output)


def start_prompt(kind: str) -> tuple[subprocess.Popen[bytes], int, int, int]:
    master_fd, slave_fd = pty.openpty()
    original_flags = termios.tcgetattr(slave_fd)[3]
    code = (
        "from scripts.install_private_chatgpt_credential import prompt_secret; "
        f"prompt_secret({kind!r}); print('accepted')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
    )
    return process, master_fd, slave_fd, original_flags


def test_prompt_never_echoes_the_credential_and_restores_terminal() -> None:
    synthetic = b"Bearer synthetic-review-credential"
    process, master_fd, slave_fd, original_flags = start_prompt("mcp-authorization")
    try:
        output = read_until(master_fd, b"Authorization header: ")
        os.write(master_fd, synthetic + b"\n")
        output += drain(master_fd, process)

        assert process.returncode == 0
        assert synthetic not in output
        assert b"accepted" in output
        assert (
            termios.tcgetattr(slave_fd)[3] & termios.ECHO
            == original_flags & termios.ECHO
        )
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_interruption_never_echoes_partial_input_and_restores_terminal() -> None:
    synthetic = b"synthetic-partial-secret"
    process, master_fd, slave_fd, original_flags = start_prompt("control-plane-api-key")
    try:
        output = read_until(master_fd, b"runtime API key: ")
        os.write(master_fd, synthetic)
        process.send_signal(signal.SIGINT)
        output += drain(master_fd, process)

        assert process.returncode == -signal.SIGINT
        assert synthetic not in output
        assert (
            termios.tcgetattr(slave_fd)[3] & termios.ECHO
            == original_flags & termios.ECHO
        )
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_writer_atomically_replaces_a_root_only_credential(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    directory.mkdir(mode=0o750)
    destination = directory / "mcp-authorization"
    destination.write_text("old value", encoding="utf-8")
    destination.chmod(0o644)

    installed = installer.write_credential(
        directory,
        "mcp-authorization",
        "Bearer synthetic-review-credential",
        owner_uid=os.getuid(),
        owner_gid=os.getgid(),
    )

    info = installed.stat()
    assert installed == destination
    assert installed.read_text(encoding="utf-8") == "Bearer synthetic-review-credential"
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.getuid()
    assert info.st_gid == os.getgid()
    assert not list(directory.glob(".credential-*"))


def test_writer_rejects_malformed_values_without_creating_a_file(
    tmp_path: Path,
) -> None:
    for value in ("", "token-without-bearer", "Bearer ", "Bearer value\nsecond"):
        try:
            installer.write_credential(
                tmp_path,
                "mcp-authorization",
                value,
                owner_uid=os.getuid(),
                owner_gid=os.getgid(),
            )
        except installer.CredentialInstallError:
            pass
        else:
            raise AssertionError("malformed credential was accepted")
    assert not list(tmp_path.iterdir())
