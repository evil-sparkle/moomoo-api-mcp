"""Rebuild the pinned official source without changing its application dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen


def verify_source(source: Path, manifest: dict) -> None:
    for name, key in (("go.mod", "go_mod_sha256"), ("go.sum", "go_sum_sha256")):
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError("Pinned dependency manifest changed: " + name)
    if (source / "pkg/version/VERSION").read_text().strip() != manifest["version"]:
        raise ValueError("Pinned source version changed")


def fetch(destination: Path, manifest: dict) -> None:
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "source.tar.gz"
        with (
            urlopen(manifest["url"], timeout=60) as response,
            archive.open("wb") as out,
        ):  # noqa: S310
            shutil.copyfileobj(response, out)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("Pinned source archive SHA-256 mismatch")
        unpacked = Path(directory) / "unpacked"
        with tarfile.open(archive) as bundle:
            bundle.extractall(unpacked, filter="data")
        source = unpacked / ("tunnel-client-" + manifest["commit"])
        verify_source(source, manifest)
        shutil.copytree(source, destination)


def build(source: Path, destination: Path, manifest: dict) -> None:
    verify_source(source, manifest)
    version = subprocess.check_output(["go", "version"], text=True).split()[2]
    actual = tuple(int(part) for part in version.removeprefix("go").split("."))
    minimum = tuple(int(part) for part in manifest["minimum_go"].split("."))
    if actual < minimum:
        raise ValueError("Go toolchain lacks the required SPKI rejection fix")
    flags = "-trimpath -buildvcs=false"
    prefix = "github.com/openai/tunnel-client/pkg/version."
    linker = "-s -w " + " ".join(
        "-X " + prefix + name + "=" + value
        for name, value in {
            "GitSHA": manifest["commit"],
            "semanticVersion": manifest["version"],
            "GoVersion": version,
            "BuildFlags": "'" + flags + "'",
            "Flavor": "full",
        }.items()
    )
    # Quote the complete assignment containing spaces for Go's linker parser.
    linker = linker.replace(
        prefix + "BuildFlags='" + flags + "'",
        "'" + prefix + "BuildFlags=" + flags + "'",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ | {
        "CGO_ENABLED": "0",
        "GOOS": "linux",
        "GOARCH": "amd64",
        "GOAMD64": "v1",
        "GOTOOLCHAIN": "local",
        "GOWORK": "off",
        "GOENV": "off",
    }
    subprocess.run(
        [
            "go",
            "build",
            "-p=2",
            "-mod=readonly",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags",
            linker,
            "-o",
            str(destination.resolve()),
            "./cmd/client",
        ],
        cwd=source,
        env=environment,
        check=True,
    )
    verify_source(source, manifest)
    reported = subprocess.check_output(
        [str(destination.resolve()), "--version"], text=True
    )
    if manifest["version"] + "+" + manifest["commit"] not in reported:
        raise ValueError("Rebuilt client reported unexpected source identity")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("fetch", "build"))
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).with_name("source.json")
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.mode == "fetch":
        fetch(args.destination, manifest)
    elif args.source is not None:
        build(args.source, args.destination, manifest)
    else:
        parser.error("build requires --source")


if __name__ == "__main__":
    main()
