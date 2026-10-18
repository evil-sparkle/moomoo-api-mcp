#!/usr/bin/env python3
"""A disposable Docker recorder; never connects to an engine or prints settings."""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state_path = Path(os.environ["SYNTHETIC_DOCKER_STATE"])
state = json.loads(state_path.read_text())
with Path(os.environ["SYNTHETIC_DOCKER_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")

DEFAULT_MOUNTS = [
    {
        "Type": "volume",
        "Name": "original-project_opend-data",
        "Destination": "/home/opend/.com.moomoo.OpenD",
    },
    {
        "Type": "volume",
        "Name": "original-project_execution-data",
        "Destination": "/var/lib/moomoo-mcp/data",
    },
]


def image_id(value):
    return (
        value
        if value.startswith("sha256:")
        else "sha256:" + value.split("@sha256:")[-1]
    )


def environment(path):
    values = {"MOOMOO_TRADING_MODE": "READ_ONLY", "OPEND_INTERACTIVE": "0"}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def runtime():
    return {
        "mounts": state.get("mounts", DEFAULT_MOUNTS),
        "labels": {
            "com.docker.compose.project": "original-project",
            "com.docker.compose.service": "moomoo-mcp",
        },
        "config": {
            "Env": state.get(
                "environment",
                ["MCP_AUTH_TOKEN=old-synthetic-token", "MOOMOO_TRADING_MODE=READ_ONLY"],
            ),
            "User": "opend",
            "Entrypoint": ["python", "-m", "moomoo_mcp.supervisor"],
            "OpenStdin": True,
            "Tty": True,
        },
        "host": {
            "RestartPolicy": {"Name": "unless-stopped"},
            "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]},
        },
        "image": image_id(state["image"]),
    }


if args[0] == "info":
    print(json.dumps(["name=rootless"]))
elif args[0] == "inspect":
    if '"labels"' in args[-1]:
        selected = runtime()
        if args[1] != "moomoo-api-mcp":
            selected["config"]["Env"] = state.get("tunnel_environment", [])
            selected["image"] = state.get("tunnel_image", state["image"])
            selected["mounts"] = []
            selected["host"] = {}
        print(json.dumps(selected))
    else:
        print(
            json.dumps(
                {
                    "mounts": state.get("mounts", DEFAULT_MOUNTS),
                    "user": "opend",
                    "ports": {
                        "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]
                    },
                    "image": image_id(state["image"]),
                }
            )
        )
elif args[:2] == ["image", "inspect"]:
    print(image_id(args[2]))
elif args[0] == "ps":
    print("synthetic-tunnel" if state.get("tunnel") else "")
elif args[0] == "run":
    values = json.load(sys.stdin)
    if values.get("SYNTHETIC_INVALID_SETTINGS") == "1":
        print("synthetic-secret-must-not-escape", file=sys.stderr)
        raise SystemExit(1)
elif args[0] == "compose":
    directory = Path(args[args.index("--project-directory") + 1])
    selection = json.loads((directory / "deployment.json").read_text())
    if "config" in args:
        values = environment(Path(selection["env_file"]))
        files = [args[index + 1] for index, value in enumerate(args) if value == "-f"]
        paths = {Path(value).name for value in files}
        model = {
            "services": {
                "moomoo-mcp": {"image": selection["image"], "environment": values}
            }
        }
        for overlay, field, path in [
            (
                "docker-compose.paper.yml",
                "MOOMOO_JOURNAL_PATH",
                "/var/lib/moomoo-mcp/data/execution.sqlite3",
            ),
            (
                "docker-compose.real.yml",
                "MOOMOO_REAL_JOURNAL_PATH",
                "/app/real-execution-data/execution.db",
            ),
            (
                "docker-compose.controls.yml",
                "MOOMOO_TRADING_CONTROLS_PATH",
                "/var/lib/moomoo/trading-controls/controls.sqlite3",
            ),
        ]:
            if overlay in paths:
                values[field] = path
        if "compose.ansible.yml" in paths:
            for line in (directory / "compose.ansible.yml").read_text().splitlines():
                if line.startswith("volumes: "):
                    model["volumes"] = json.loads(line.removeprefix("volumes: "))
                if line.startswith("    volumes: !override "):
                    model["services"]["moomoo-mcp"]["volumes"] = json.loads(
                        line.removeprefix("    volumes: !override ")
                    )
                if line.startswith("  chatgpt-tunnel: "):
                    tunnel = json.loads(line.removeprefix("  chatgpt-tunnel: "))
                    tunnel["environment"] = {
                        "MCP_AUTH_TOKEN": values.get("MCP_RESTRICTED_AUTH_TOKEN", ""),
                        "CHATGPT_TUNNEL_API_KEY": values.get(
                            "CHATGPT_TUNNEL_API_KEY", ""
                        ),
                        "CHATGPT_TUNNEL_ID": values.get("CHATGPT_TUNNEL_ID", ""),
                    }
                    model["services"]["chatgpt-tunnel"] = tunnel
        print(json.dumps(model))
    elif "up" in args:
        files = [args[index + 1] for index, value in enumerate(args) if value == "-f"]
        model = json.loads(Path(files[-1]).read_text())
        service = model["services"]["moomoo-mcp"]
        state["image"] = service["image"]
        state["environment"] = [
            key + "=" + value.replace("$$", "$")
            for key, value in service["environment"].items()
        ]
        state["mounts"] = [
            {
                "Type": "volume",
                "Name": model["volumes"][mount["source"]]["name"],
                "Destination": mount["target"],
            }
            for mount in service.get("volumes", [])
        ]
        state["tunnel"] = "chatgpt-tunnel" in model["services"]
        if state["tunnel"]:
            sidecar = model["services"]["chatgpt-tunnel"]
            state["tunnel_image"] = sidecar["image"]
            state["tunnel_environment"] = [
                key + "=" + value for key, value in sidecar["environment"].items()
            ]
        state_path.write_text(json.dumps(state))
        if Path(files[-1]).name == "previous-compose.json":
            Path(os.environ["SYNTHETIC_LEGACY_RESTORED"]).write_text("restored")
    elif "exec" in args and state["image"] == os.environ.get("SYNTHETIC_FAIL_IMAGE"):
        print("synthetic-secret-must-not-escape", file=sys.stderr)
        raise SystemExit(1)
else:
    raise SystemExit("Unsupported disposable Docker command.")
