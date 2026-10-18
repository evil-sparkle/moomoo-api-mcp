#!/usr/bin/env python3
"""Operate a frozen public release without printing server configuration."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from deployment_lock import ROOT_ENV, deployment_lock
from deployment_manifest import require_hex, validate_image, validate_project

STATE_TARGETS = {
    "/home/opend/.com.moomoo.OpenD": "opend-state",
    "/var/lib/moomoo-mcp/data": "paper-state",
    "/app/real-execution-data": "real-state",
    "/var/lib/moomoo/trading-controls": "controls-state",
}
STATE_SUFFIXES = {
    "opend-state": "opend-data",
    "paper-state": "execution-data",
    "real-state": "real-execution-data",
    "controls-state": "trading-controls-data",
}
SNAPSHOT = "resolved-compose.json"


def execute(arguments: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(
        arguments, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ValueError("Deployment command failed; raw output was suppressed.")
    return result.stdout


def private_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def escape_runtime(value):
    """Compose snapshots escape dollars; Docker inspection returns raw values."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escape_runtime(item) for item in value]
    if isinstance(value, dict):
        return {key: escape_runtime(item) for key, item in value.items()}
    return value


def decoded_environment(model: dict) -> dict[str, str]:
    environment = model["services"]["moomoo-mcp"]["environment"]
    return {
        key: str(value).replace("$$", "$")
        for key, value in environment.items()
        if value is not None
    }


def settings(directory: Path) -> dict:
    value = json.loads((directory / "deployment.json").read_text())
    require_hex(value["revision"], 40, "Source revision")
    validate_image(value["image"])
    validate_image(value["tunnel_image"])
    if value["expected_mode"] not in {"READ_ONLY", "SIMULATE", "REAL"}:
        raise ValueError("Select an explicit supported trading mode.")
    if type(value["tunnel_enabled"]) is not bool:
        raise ValueError("Select a boolean tunnel setting.")
    validate_project(value["project"])
    if not Path(value["env_file"]).is_absolute():
        raise ValueError("Select an absolute server-side credential path.")
    return value


def compose(
    directory: Path, value: dict, *, sources: list[str] | None = None
) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        value["project"],
        "--project-directory",
        str(directory),
    ]
    if sources is None:
        return command + ["-f", str(directory / SNAPSHOT)]
    if sources:
        command += ["--env-file", value["env_file"]]
    for source in sources:
        command += ["-f", str(directory / source)]
    return command


def credential_metadata(value: dict) -> None:
    metadata = Path(value["env_file"]).stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(
            "The existing configuration must be owned by this user at 0600."
        )


def validate_model(model: dict, value: dict) -> None:
    environment = model["services"]["moomoo-mcp"]["environment"]
    mode = environment.get("MOOMOO_TRADING_MODE", "READ_ONLY").strip().upper()
    if mode != value["expected_mode"]:
        raise ValueError("The resolved trading mode differs from the public selection.")
    token = environment.get("MCP_AUTH_TOKEN", "")
    if not token.strip() or environment.get("MCP_ALLOW_UNAUTHENTICATED_HTTP"):
        raise ValueError("Deployment requires ordinary authenticated MCP HTTP.")
    if environment.get("MCP_TRANSPORT", "streamable-http") != "streamable-http":
        raise ValueError("Deployment requires Streamable HTTP transport.")
    if environment.get("OPEND_INTERACTIVE", "0") != "0":
        raise ValueError(
            "Complete interactive OpenD login before unattended deployment."
        )
    if value["tunnel_enabled"]:
        tunnel = model["services"]["chatgpt-tunnel"]["environment"]
        if not tunnel.get("CHATGPT_TUNNEL_API_KEY", "").strip():
            raise ValueError("The selected tunnel needs its server-side runtime key.")
        if not tunnel.get("CHATGPT_TUNNEL_ID", "").startswith("tunnel_"):
            raise ValueError(
                "The selected tunnel needs its existing public identifier."
            )
        restricted = environment.get("MCP_RESTRICTED_AUTH_TOKEN", "")
        if (
            not restricted
            or restricted == token
            or tunnel.get("MCP_AUTH_TOKEN") != restricted
        ):
            raise ValueError(
                "The tunnel requires its independent restricted MCP identity."
            )


def validate_configuration(directory: Path, value: dict) -> dict:
    path = directory / SNAPSHOT
    metadata = path.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
        raise ValueError("The frozen configuration must be owned by this user at 0600.")
    model = json.loads(path.read_text())
    validate_model(model, value)
    return model


def runtime_service(container: dict) -> dict:
    """Freeze only Compose-relevant runtime settings, including private environment."""
    configuration = container["config"]
    host = container["host"]
    environment = {}
    for entry in configuration.get("Env") or []:
        key, separator, value = entry.partition("=")
        if separator:
            environment[key] = value
    service = {
        "image": container["image"],
        "container_name": configuration.get("Hostname", ""),
        "environment": environment,
        "user": configuration.get("User", ""),
        "restart": (host.get("RestartPolicy") or {}).get("Name", "unless-stopped"),
        "stdin_open": bool(configuration.get("OpenStdin")),
        "tty": bool(configuration.get("Tty")),
        "read_only": bool(host.get("ReadonlyRootfs")),
    }
    for source, target in [("Entrypoint", "entrypoint"), ("Cmd", "command")]:
        if configuration.get(source) is not None:
            service[target] = configuration[source]
    ports = []
    for target, bindings in (host.get("PortBindings") or {}).items():
        number, _, protocol = target.partition("/")
        for binding in bindings or []:
            ports.append(
                {
                    "target": int(number),
                    "published": binding["HostPort"],
                    "host_ip": binding["HostIp"],
                    "protocol": protocol or "tcp",
                }
            )
    if ports:
        service["ports"] = ports
    for source, target in [
        ("CapDrop", "cap_drop"),
        ("SecurityOpt", "security_opt"),
    ]:
        if host.get(source):
            service[target] = host[source]
    if host.get("Tmpfs"):
        service["tmpfs"] = [
            target + ":" + options for target, options in host["Tmpfs"].items()
        ]
    return service


def inspect_runtime(identifier: str) -> dict:
    return json.loads(
        execute(
            [
                "docker",
                "inspect",
                identifier,
                "--format",
                '{"mounts":{{json .Mounts}},"labels":{{json .Config.Labels}},'
                '"config":{{json .Config}},"host":{{json .HostConfig}},'
                '"image":{{json .Image}}}',
            ]
        )
    )


def state_mounts(container: dict) -> tuple[list[dict], dict]:
    mounts = []
    volumes = {}
    for mount in container["mounts"]:
        target = mount.get("Destination")
        if target not in STATE_TARGETS:
            raise ValueError("Existing deployment has an unsupported additional mount.")
        if mount.get("Type") != "volume" or not mount.get("Name"):
            raise ValueError("Deployment requires existing named state volumes.")
        alias = STATE_TARGETS[target]
        volumes[alias] = {"external": True, "name": mount["Name"]}
        mounts.append({"type": "volume", "source": alias, "target": target})
    if "opend-state" not in volumes:
        raise ValueError("Existing OpenD volume was not found; deployment stopped.")
    return mounts, volumes


def prepare(directory: Path, value: dict, *, capture_predecessor: bool = False) -> None:
    """Freeze the candidate; reconstruct legacy state only on first cutover."""
    if (directory / SNAPSHOT).exists():
        raise ValueError(
            "A prepared release is immutable; select a fresh release directory."
        )
    credential_metadata(value)
    container = inspect_runtime("moomoo-api-mcp")
    labels = container.get("labels") or {}
    if (
        labels.get("com.docker.compose.project") != value["project"]
        or labels.get("com.docker.compose.service") != "moomoo-mcp"
    ):
        raise ValueError("Existing container does not belong to the selected project.")
    mounts, volumes = state_mounts(container)
    if capture_predecessor:
        previous = runtime_service(container)
        previous["container_name"] = "moomoo-api-mcp"
        previous["volumes"] = mounts.copy()
        predecessor = {"services": {"moomoo-mcp": previous}, "volumes": dict(volumes)}
        tunnel_ids = execute(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + value["project"],
                "--filter",
                "label=com.docker.compose.service=chatgpt-tunnel",
            ]
        ).split()
        if len(tunnel_ids) > 1:
            raise ValueError("More than one existing tunnel container was found.")
        if tunnel_ids:
            old_tunnel = inspect_runtime(tunnel_ids[0])
            predecessor["services"]["chatgpt-tunnel"] = runtime_service(old_tunnel)
            predecessor["services"]["chatgpt-tunnel"].pop("container_name", None)
        previous_path = directory / "previous-compose.json"
        private_json(previous_path, escape_runtime(predecessor))
        # A malformed rescue document must fail before replacing the old stack.
        execute(
            compose(directory, value, sources=[])
            + ["-f", str(previous_path), "config", "--quiet"]
        )
    base = json.loads(
        execute(
            compose(directory, value, sources=["docker-compose.yml"])
            + ["config", "--format", "json"]
        )
    )
    environment = base["services"]["moomoo-mcp"]["environment"]
    mode = environment.get("MOOMOO_TRADING_MODE", "READ_ONLY").strip().upper()
    selected = set()
    if mode == "SIMULATE" or environment.get("MOOMOO_SIMULATED_ACC_IDS", "").strip():
        selected.add("paper-state")
    if environment.get("MOOMOO_JOURNAL_PATH"):
        selected.add("paper-state")
    if mode == "REAL":
        selected.add("real-state")
    if environment.get("MOOMOO_REAL_JOURNAL_PATH"):
        selected.add("real-state")
    if (
        environment.get("MOOMOO_TRADING_CONTROLS_PATH")
        or environment.get("MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY") is not None
        or environment.get("MOOMOO_REQUIRE_TRADE_APPROVAL", "0") == "1"
        or environment.get("MOOMOO_OPERATOR_AUTH_TOKEN")
    ):
        selected.add("controls-state")
    required = set(volumes) | selected
    for target, alias in STATE_TARGETS.items():
        if alias in required and alias not in volumes:
            volumes[alias] = {"name": value["project"] + "_" + STATE_SUFFIXES[alias]}
            mounts.append({"type": "volume", "source": alias, "target": target})
    service = {
        "volumes": mounts,
        "environment": {
            "MCP_ALLOW_CHATGPT_TUNNEL_HOST": "1" if value["tunnel_enabled"] else ""
        },
    }
    sources = ["docker-compose.yml"]
    for alias, overlay in [
        ("paper-state", "docker-compose.paper.yml"),
        ("real-state", "docker-compose.real.yml"),
        ("controls-state", "docker-compose.controls.yml"),
    ]:
        if alias in selected:
            sources.append(overlay)
    text = (
        "services:\n  moomoo-mcp:\n    build: !reset null\n    image: "
        + json.dumps(value["image"])
        + "\n"
    )
    # Override rather than merge target mounts, so no duplicate aliases create volumes.
    text += "    volumes: !override " + json.dumps(service["volumes"]) + "\n"
    text += "    environment: " + json.dumps(service["environment"]) + "\n"
    if value["tunnel_enabled"]:
        tunnel = {
            "image": value["tunnel_image"],
            "platform": "linux/amd64",
            "user": "10002:10002",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "restart": "unless-stopped",
            "stop_grace_period": "20s",
            "environment": {
                "MCP_AUTH_TOKEN": "${MCP_RESTRICTED_AUTH_TOKEN:-}",
                "CHATGPT_TUNNEL_API_KEY": "${CHATGPT_TUNNEL_API_KEY:-}",
                "CHATGPT_TUNNEL_ID": "${CHATGPT_TUNNEL_ID:-}",
            },
            "tmpfs": [
                "/run/moomoo-chatgpt-tunnel:uid=10002,gid=10002,mode=0700,size=16m,noexec,nosuid,nodev"
            ],
        }
        text += "  chatgpt-tunnel: " + json.dumps(tunnel) + "\n"
    text += "volumes: " + json.dumps(volumes) + "\n"
    (directory / "compose.ansible.yml").write_text(text)
    model = json.loads(
        execute(
            compose(directory, value, sources=sources + ["compose.ansible.yml"])
            + ["config", "--format", "json"]
        )
    )
    validate_model(model, value)
    private_json(directory / SNAPSHOT, model)
    (directory / "state-volumes.json").write_text(json.dumps(volumes) + "\n")


def preflight(directory: Path, value: dict) -> None:
    model = validate_configuration(directory, value)
    environment = decoded_environment(model)
    execute(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev",
            "--env",
            "HOME=/tmp",
            "--entrypoint",
            "python",
            value["image"],
            "-m",
            "moomoo_mcp.settings",
            "--validate",
        ],
        stdin=json.dumps(environment),
    )
    arguments = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev",
        "--env",
        "HOME=/tmp",
        "--entrypoint",
        "python",
    ]
    for mount in model["services"]["moomoo-mcp"].get("volumes", []):
        if (
            mount.get("target") in STATE_TARGETS
            and mount.get("target") != "/home/opend/.com.moomoo.OpenD"
        ):
            volume = model["volumes"][mount["source"]]["name"]
            arguments += [
                "--mount",
                "type=volume,source="
                + volume
                + ",target="
                + mount["target"]
                + ",readonly",
            ]
    execute(
        arguments + [value["image"], "-m", "moomoo_mcp.settings", "--validate-storage"],
        stdin=json.dumps(environment),
    )


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
            "application",
            value["expected_mode"],
        ]
    )
    runtime = json.loads(
        execute(
            [
                "docker",
                "inspect",
                "moomoo-api-mcp",
                "--format",
                '{"user":{{json .Config.User}},'
                '"ports":{{json .HostConfig.PortBindings}},'
                '"mounts":{{json .Mounts}},"image":{{json .Image}}}',
            ]
        )
    )
    identity = execute(
        ["docker", "image", "inspect", value["image"], "--format", "{{.Id}}"]
    )
    if runtime["user"] not in ["opend", "10001", "10001:10001"]:
        raise ValueError("Runtime must use the existing non-root OpenD identity.")
    if runtime["image"] != identity.strip():
        raise ValueError("Runtime does not use the selected immutable image.")
    if runtime["ports"] != {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}:
        raise ValueError("MCP must remain host-loopback-only with OpenD unpublished.")
    expected = json.loads((directory / "state-volumes.json").read_text())
    actual = {
        STATE_TARGETS[mount["Destination"]]: mount.get("Name")
        for mount in runtime["mounts"]
        if mount.get("Destination") in STATE_TARGETS
    }
    if actual != {name: volume["name"] for name, volume in expected.items()}:
        raise ValueError("Runtime did not preserve the selected state volumes.")
    print("Authenticated MCP and " + value["expected_mode"] + " mode verified.")


def rollback(directory: Path, value: dict, release: str) -> None:
    """Select a previously frozen native release without consulting edited .env."""
    root = directory.parent.parent
    current = root / "current"
    if (
        directory.parent.name != "releases"
        or not current.is_symlink()
        or current.resolve() != directory.resolve()
        or not release
        or any(character not in "0123456789abcdef-" for character in release)
    ):
        raise ValueError("Select a saved release from the current native deployment.")
    target = (root / "releases" / release).resolve(strict=True)
    if target.parent != directory.parent.resolve():
        raise ValueError("Rollback must remain inside this deployment's releases.")
    selected = settings(target)
    if selected["project"] != value["project"]:
        raise ValueError("Rollback must preserve the existing Compose project.")
    preflight(target, selected)
    temporary = root / (".current-" + uuid4().hex)
    try:
        execute(
            compose(target, selected) + ["up", "-d", "--no-build", "--remove-orphans"]
        )
        verify(target, selected)
        temporary.symlink_to(target, target_is_directory=True)
        temporary.replace(current)
    except (ValueError, KeyError, OSError, TypeError, subprocess.SubprocessError):
        execute(
            compose(directory, value) + ["up", "-d", "--no-build", "--remove-orphans"]
        )
        raise
    finally:
        temporary.unlink(missing_ok=True)
    print("Restored frozen release " + release + ".")


def operate(directory: Path, value: dict, mode: str, arguments: list[str]) -> None:
    if mode == "prepare":
        if arguments not in ([], ["--capture-predecessor"]):
            raise ValueError("Unsupported preparation option.")
        prepare(directory, value, capture_predecessor=bool(arguments))
    elif mode == "rollback":
        if len(arguments) != 1:
            raise ValueError("Select one saved native release identifier.")
        rollback(directory, value, arguments[0])
    elif mode == "validate":
        validate_configuration(directory, value)
    elif mode == "preflight":
        preflight(directory, value)
    elif mode == "compose":
        if any(
            item in {"up", "start", "restart", "run", "create"} for item in arguments
        ):
            preflight(directory, value)
        if "--volumes" in arguments or "-v" in arguments:
            raise ValueError("Runtime operations must preserve persistent volumes.")
        execute(compose(directory, value) + arguments)
    elif mode == "restore":
        execute(
            compose(directory, value, sources=[])
            + [
                "-f",
                str(directory / "previous-compose.json"),
                "up",
                "-d",
                "--no-build",
                "--remove-orphans",
            ]
        )
    elif mode == "verify":
        verify(directory, value)
    elif mode == "gateway":
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
                    value["expected_mode"],
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=[
            "prepare",
            "validate",
            "preflight",
            "compose",
            "restore",
            "rollback",
            "verify",
            "gateway",
            "tunnel",
        ],
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parents[1]
    if ROOT_ENV not in os.environ and directory.parent.name == "releases":
        os.environ[ROOT_ENV] = str(directory.parent.parent)
    try:
        value = settings(directory)
        with deployment_lock():
            operate(directory, value, args.mode, args.arguments)
    except (ValueError, KeyError, OSError, TypeError, subprocess.SubprocessError):
        print(
            "Deployment validation failed; settings and command output suppressed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
