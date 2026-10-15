#!/usr/bin/env python3
"""A disposable Docker command recorder; never connects to an engine."""

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state_path = Path(os.environ["SYNTHETIC_DOCKER_STATE"])
state = json.loads(state_path.read_text())
with Path(os.environ["SYNTHETIC_DOCKER_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")

mounts = [
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

if args[0] == "info":
    print(json.dumps(["name=rootless"]))
elif args[0] == "inspect":
    if '"labels"' in args[-1]:
        print(
            json.dumps(
                {
                    "mounts": mounts,
                    "labels": {
                        "com.docker.compose.project": "original-project",
                        "com.docker.compose.service": "moomoo-mcp",
                    },
                }
            )
        )
    else:
        print(
            json.dumps(
                {
                    "mounts": mounts,
                    "user": "opend",
                    "ports": {
                        "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]
                    },
                    "image": "sha256:" + state["image"].split("@sha256:")[-1],
                }
            )
        )
elif args[:2] == ["image", "inspect"]:
    print("sha256:" + args[2].split("@sha256:")[-1])
elif args[0] == "ps":
    print("")
elif args[0] == "compose":
    directory = Path(args[args.index("--project-directory") + 1])
    selection = json.loads((directory / "deployment.json").read_text())
    if "config" in args:
        print(
            json.dumps(
                {
                    "services": {
                        "moomoo-mcp": {
                            "environment": {
                                "MCP_AUTH_TOKEN": "synthetic-test-token",
                                "MOOMOO_TRADING_MODE": "READ_ONLY",
                            }
                        }
                    }
                }
            )
        )
    elif "up" in args:
        state["image"] = selection["image"]
        state_path.write_text(json.dumps(state))
    elif "exec" in args and state["image"] == os.environ.get("SYNTHETIC_FAIL_IMAGE"):
        print("synthetic-secret-must-not-escape", file=sys.stderr)
        raise SystemExit(1)
else:
    raise SystemExit("Unsupported disposable Docker command.")
