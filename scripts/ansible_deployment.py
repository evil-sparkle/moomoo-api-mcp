#!/usr/bin/env python3
"""Operate an exact public release without copying or printing server credentials."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from deployment_manifest import require_hex, validate_image

STATE_TARGETS = {
    "/home/opend/.com.moomoo.OpenD": "opend-state",
    "/var/lib/moomoo-mcp/data": "paper-state",
    "/app/real-execution-data": "real-state",
}


def execute(arguments: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(
        arguments, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        # Compose errors can contain an offending credential-file line.
        raise ValueError("Deployment command failed; raw output was suppressed.")
    return result.stdout


def settings(directory: Path) -> dict:
    value = json.loads((directory / "deployment.json").read_text())
    require_hex(value["revision"], 40, "Source revision")
    validate_image(value["image"])
    validate_image(value["tunnel_image"])
    if value["expected_mode"] != "READ_ONLY":
        raise ValueError("This migration requires READ_ONLY operation.")
    if type(value["tunnel_enabled"]) is not bool:
        raise ValueError("Select a boolean tunnel setting.")
    project = value["project"]
    if (
        not isinstance(project, str)
        or not project
        or project[0] not in "abcdefghijklmnopqrstuvwxyz0123456789"
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in project)
    ):
        raise ValueError("Invalid Compose project name.")
    credential_path = Path(value["env_file"])
    if not credential_path.is_absolute():
        raise ValueError("Select an absolute server-side credential path.")
    return value


def compose(directory: Path, value: dict) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        value["project"],
        "--project-directory",
        str(directory),
        "--env-file",
        value["env_file"],
        "-f",
        str(directory / "docker-compose.yml"),
        "-f",
        str(directory / "compose.ansible.yml"),
    ]
    return command


def validate_configuration(directory: Path, value: dict) -> None:
    metadata = Path(value["env_file"]).stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(
            "The existing credential file must be owned by this user at 0600."
        )
    model = json.loads(
        execute(compose(directory, value) + ["config", "--format", "json"])
    )
    environment = model["services"]["moomoo-mcp"]["environment"]
    if environment.get("MOOMOO_TRADING_MODE", "READ_ONLY") != value["expected_mode"]:
        raise ValueError(
            "Migration refused: the existing trading mode is not READ_ONLY."
        )
    token = environment.get("MCP_AUTH_TOKEN", "")
    if (
        not token
        or not token.strip()
        or environment.get("MCP_ALLOW_UNAUTHENTICATED_HTTP")
    ):
        raise ValueError("Migration requires ordinary authenticated MCP HTTP.")
    if environment.get("MCP_TRANSPORT", "streamable-http") != "streamable-http":
        raise ValueError("Migration requires Streamable HTTP transport.")
    if environment.get("OPEND_INTERACTIVE", "0") != "0":
        raise ValueError(
            "Complete interactive OpenD login before unattended migration."
        )
    if value["tunnel_enabled"]:
        tunnel = model["services"]["chatgpt-tunnel"]["environment"]
        if not tunnel.get("CHATGPT_TUNNEL_API_KEY", "").strip():
            raise ValueError("The selected tunnel needs its existing server-side key.")
        identifier = tunnel.get("CHATGPT_TUNNEL_ID", "")
        if not identifier.startswith("tunnel_"):
            raise ValueError(
                "The selected tunnel needs its existing public identifier."
            )


def prepare(directory: Path, value: dict) -> None:
    """Discover only mount metadata; never read remembered login or journal data."""
    container = json.loads(
        execute(
            [
                "docker",
                "inspect",
                "moomoo-api-mcp",
                "--format",
                '{"mounts":{{json .Mounts}},"labels":{{json .Config.Labels}}}',
            ]
        )
    )
    labels = container.get("labels") or {}
    if (
        labels.get("com.docker.compose.project") != value["project"]
        or labels.get("com.docker.compose.service") != "moomoo-mcp"
    ):
        raise ValueError("Existing container does not belong to the selected project.")
    tunnel_containers = execute(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + value["project"],
            "--filter",
            "label=com.docker.compose.service=chatgpt-tunnel",
        ]
    ).strip()
    if tunnel_containers and not value["tunnel_enabled"]:
        raise ValueError("Select the existing tunnel before migrating this deployment.")
    mounts = []
    volumes = {}
    for mount in container["mounts"]:
        target = mount.get("Destination")
        if target not in STATE_TARGETS:
            raise ValueError("Existing deployment has an unsupported additional mount.")
        if mount.get("Type") != "volume" or not mount.get("Name"):
            raise ValueError(
                "Migration currently requires existing named state volumes."
            )
        alias = STATE_TARGETS[target]
        volumes[alias] = {"external": True, "name": mount["Name"]}
        mounts.append({"type": "volume", "source": alias, "target": target})
    if "opend-state" not in volumes:
        raise ValueError("Existing OpenD volume was not found; migration stopped.")
    service = {
        "image": value["image"],
        "volumes": mounts,
        "environment": {
            "MCP_ALLOW_CHATGPT_TUNNEL_HOST": "1" if value["tunnel_enabled"] else ""
        },
    }
    services = {"moomoo-mcp": service}
    if value["tunnel_enabled"]:
        services["chatgpt-tunnel"] = {
            "image": value["tunnel_image"],
            "platform": "linux/amd64",
            "user": "10002:10002",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "restart": "unless-stopped",
            "stop_grace_period": "20s",
            "environment": {
                name: "${" + name + ":-}"
                for name in [
                    "MCP_AUTH_TOKEN",
                    "CHATGPT_TUNNEL_API_KEY",
                    "CHATGPT_TUNNEL_ID",
                ]
            },
            "tmpfs": [
                "/run/moomoo-chatgpt-tunnel:uid=10002,gid=10002,mode=0700,size=16m,noexec,nosuid,nodev"
            ],
        }
    # JSON is valid YAML. Reset build in a second YAML document is unsupported,
    # so remove the base build through an explicit YAML override first.
    text = "services:\n  moomoo-mcp:\n    build: !reset null\n"
    text += "    image: " + json.dumps(service.pop("image")) + "\n"
    for key, item in service.items():
        text += "    " + key + ": " + json.dumps(item) + "\n"
    if value["tunnel_enabled"]:
        text += "  chatgpt-tunnel: " + json.dumps(services["chatgpt-tunnel"]) + "\n"
    text += "volumes: " + json.dumps(volumes) + "\n"
    (directory / "compose.ansible.yml").write_text(text)
    (directory / "state-volumes.json").write_text(json.dumps(volumes) + "\n")
    validate_configuration(directory, value)


def verify(directory: Path, value: dict) -> None:
    execute(
        compose(directory, value)
        + [
            "exec",
            "-T",
            "moomoo-mcp",
            "python",
            "-c",
            (directory / "scripts/deployment_probe.py").read_text(),
        ]
    )
    public = execute(
        [
            "docker",
            "inspect",
            "moomoo-api-mcp",
            "--format",
            '{"user":{{json .Config.User}},"ports":{{json .HostConfig.PortBindings}},'
            '"mounts":{{json .Mounts}},"image":{{json .Image}}}',
        ]
    )
    runtime = json.loads(public)
    identity = execute(
        ["docker", "image", "inspect", value["image"], "--format", "{{.Id}}"]
    )
    if runtime["user"] not in ["opend", "10001", "10001:10001"]:
        raise ValueError("Runtime must use the existing non-root OpenD identity.")
    if runtime["image"] != identity.strip():
        raise ValueError(
            "Runtime does not use the selected immutable application image."
        )
    if runtime["ports"] != {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}:
        raise ValueError("MCP must remain host-loopback-only with OpenD unpublished.")
    expected = json.loads((directory / "state-volumes.json").read_text())
    actual = {
        STATE_TARGETS[mount["Destination"]]: mount.get("Name")
        for mount in runtime["mounts"]
        if mount.get("Destination") in STATE_TARGETS
    }
    if actual != {name: volume["name"] for name, volume in expected.items()}:
        raise ValueError("Runtime did not preserve the existing state volumes.")
    print("Authenticated MCP initialize, tools and READ_ONLY mode verified.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=["prepare", "validate", "compose", "verify", "gateway", "tunnel"],
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1]
    try:
        value = settings(directory)
        if args.mode == "prepare":
            prepare(directory, value)
        elif args.mode == "validate":
            validate_configuration(directory, value)
        elif args.mode == "compose":
            if any(
                item in {"up", "start", "restart", "run", "create"}
                for item in args.arguments
            ):
                validate_configuration(directory, value)
            execute(compose(directory, value) + args.arguments)
        elif args.mode == "verify":
            verify(directory, value)
        elif args.mode == "gateway":
            print(
                execute(
                    compose(directory, value)
                    + [
                        "exec",
                        "-T",
                        "moomoo-mcp",
                        "python",
                        "-c",
                        (directory / "scripts/deployment_probe.py").read_text(),
                        "gateway",
                    ]
                ).strip()
            )
        else:
            if not value["tunnel_enabled"]:
                raise ValueError("Tunnel is not selected.")
            print(
                execute(
                    compose(directory, value)
                    + [
                        "exec",
                        "-T",
                        "chatgpt-tunnel",
                        "python",
                        "-c",
                        "import sys; sys.path.insert(0, '/opt/tunnel'); "
                        "from runtime import probe; "
                        "raise SystemExit(0 if probe('healthz') "
                        "and probe('readyz') else 1)",
                    ]
                ).strip()
            )
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        print(
            "Deployment validation failed; credentials and command output suppressed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
