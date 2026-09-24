#!/usr/bin/env python3
"""Verify and install the pinned official OpenAI tunnel-client binary."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

MANIFEST_PATH = Path(__file__).with_name("release.json")


class InstallError(Exception):
    """Release verification or installation failed."""


@dataclass(frozen=True)
class Release:
    version: str
    archive: str
    url: str
    sha256: str


def load_release(path: Path = MANIFEST_PATH) -> Release:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return Release(
            version=raw["version"],
            archive=raw["archive"],
            url=raw["url"],
            sha256=raw["sha256"],
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallError("release manifest is invalid") from None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise InstallError("release archive could not be read") from None
    return digest.hexdigest()


def download(release: Release, destination: Path) -> None:
    request = Request(release.url, headers={"User-Agent": "moomoo-tunnel-installer"})
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310
            if response.status != 200:
                raise InstallError("release download returned a non-200 status")
            with destination.open("wb") as stream:
                shutil.copyfileobj(response, stream)
    except OSError:
        raise InstallError("release download failed") from None


def extract_verified_binary(archive: Path, release: Release, destination: Path) -> None:
    actual = sha256_file(archive)
    if not hmac.compare_digest(actual, release.sha256):
        raise InstallError("release archive SHA-256 does not match the manifest")
    try:
        with ZipFile(archive) as bundle:
            names = bundle.namelist()
            if names.count("tunnel-client") != 1:
                raise InstallError("release archive has no unique tunnel-client binary")
            with (
                bundle.open("tunnel-client") as source,
                destination.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
    except (BadZipFile, OSError):
        raise InstallError("release archive is not a readable zip file") from None
    destination.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    try:
        completed = subprocess.run(
            [str(destination), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        raise InstallError("verified tunnel-client binary could not execute") from None
    reported = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or release.version.lstrip("v") not in reported:
        raise InstallError("tunnel-client binary did not report the pinned version")


def install(archive: Path, release: Release, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        candidate = Path(staging) / "tunnel-client"
        extract_verified_binary(archive, release, candidate)
        candidate.replace(destination)
    destination.chmod(0o755)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("/opt/openai/tunnel-client/v0.0.14/tunnel-client"),
    )
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    release = load_release(args.manifest)
    with tempfile.TemporaryDirectory() as workspace:
        archive = args.archive
        if archive is None:
            archive = Path(workspace) / release.archive
            download(release, archive)
        if args.verify_only:
            extract_verified_binary(archive, release, Path(workspace) / "tunnel-client")
        else:
            install(archive, release, args.destination)
    print(f"verified official tunnel-client {release.version}")
    return os.EX_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        raise SystemExit(f"tunnel-client install: {exc}") from None
