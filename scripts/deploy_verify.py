#!/usr/bin/env python3
"""Check a deployment's configuration, then verify its MCP endpoint.

deploy.sh decides what to deploy and how to roll it back. This resolves the
configuration through Docker Compose and checks MCP and gateway readiness:

  check-config  Does Compose resolve a usable MCP_AUTH_TOKEN for the service?
  verify        Does the running endpoint accept that token and answer an MCP
                initialize with a well-formed result?
  gateway-readiness  Do read-only gateway probes succeed, with quote login
                     explicitly confirmed?

The configuration is what ``scripts/compose-prod.sh config --format json``
resolves -- the same wrapper, Docker context, env files and compose files that
start the container -- read from the service's own environment rather than
from an identically named interpolation variable. There is no other source: no
.env parsing here, and no fallback when Compose cannot answer. The resolved
model carries every credential in the deployment, so it stays in memory and is
never printed or written anywhere; diagnostics name what went wrong, not what
was read.

The token reaches curl on stdin (``--header @-``), never argv or a file.

Verification means the configured authentication can access the endpoint
and receive a structurally expected initialize response matching the request.
It does not certify protocol version compatibility or trading availability.

Standard library only, Python 3.10 or newer: this runs on the host, not in the
image.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SERVICE = "moomoo-mcp"
TOKEN_VARIABLE = "MCP_AUTH_TOKEN"
# Next to this file, so the wrapper is always the one from the same checkout:
# deploy.sh runs this from the target commit after checking it out.
COMPOSE_CONFIG = (
    str(Path(__file__).resolve().parent / "compose-prod.sh"),
    "config",
    "--format",
    "json",
)
BASE_COMPOSE_CONFIG = (
    "docker",
    "--context",
    "rootless",
    "compose",
    "--env-file",
    ".env",
    "--env-file",
    ".deploy.env",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.prod.yml",
    "config",
    "--format",
    "json",
)
DEFAULT_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_TIMEOUT = 90
DEFAULT_GATEWAY_TIMEOUT = 60
# One attempt never outlives this, nor the deadline.
ATTEMPT_SECONDS = 10.0
RETRY_INTERVAL = 3.0
# A string id, so a response with id 1 or true cannot compare equal to it.
REQUEST_ID = "deploy-verify"
INITIALIZE_REQUEST = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": REQUEST_ID,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "deploy-verify", "version": "0"},
        },
    },
    separators=(",", ":"),
)
HEALTH_REQUEST_ID = "deploy-gateway-readiness"
HEALTH_REQUEST = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": HEALTH_REQUEST_ID,
        "method": "tools/call",
        "params": {"name": "check_health", "arguments": {}},
    },
    separators=(",", ":"),
)
# curl exits that mean "not answering yet": cannot resolve, cannot connect,
# timed out, empty reply, send or receive failure. Anything else (a malformed
# URL, an unsupported protocol, a TLS failure) will not improve by waiting.
RETRYABLE_CURL_EXITS = frozenset({6, 7, 28, 52, 55, 56})


class ConfigError(Exception):
    """Compose could not supply a token this helper can send."""


class Interrupted(BaseException):
    """SIGINT or SIGTERM arrived. BaseException, so nothing swallows it."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class Attempt:
    """One probe's outcome. ``problem`` is safe to print: it never quotes a
    header, a response body or the configuration."""

    status: str
    verified: bool = False
    retryable: bool = False
    problem: str = ""


def say(message: str) -> None:
    print(f"deploy_verify: {message}", file=sys.stderr, flush=True)


def resolve_model(command: Sequence[str]) -> dict[str, Any]:
    """Resolve Compose internally without relaying configuration or raw errors."""
    try:
        completed = subprocess.run(list(command), capture_output=True, check=False)
    except OSError:
        raise ConfigError(f"could not run {Path(command[0]).name}.") from None
    if completed.returncode != 0:
        # Compose's own error can quote the offending line of an env file, so
        # it is not relayed. The operator can ask for it directly.
        raise ConfigError(
            f"Compose could not resolve the deployment configuration (exit "
            f"{completed.returncode}). Run scripts/compose-prod.sh config --quiet "
            "to see its error."
        )
    try:
        model: Any = json.loads(completed.stdout)
    except ValueError:
        raise ConfigError("Compose did not return a JSON configuration.") from None
    services = model.get("services") if isinstance(model, dict) else None
    if not isinstance(services, dict) or not isinstance(services.get(SERVICE), dict):
        raise ConfigError(f"the Compose configuration has no {SERVICE} service.")
    return model


def resolve_services(command: Sequence[str]) -> dict[str, Any]:
    return resolve_model(command)["services"]


def save_private_model(path: Path, model: dict[str, Any]) -> None:
    """Persist effective configuration without exposing credentials or truncation."""
    descriptor, temporary = tempfile.mkstemp(prefix=".deploy-config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(model, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_application(model: dict[str, Any]) -> None:
    """Run the selected image's settings validator with no network or broker."""
    service = model["services"][SERVICE]
    environment = service.get("environment", {})
    image = service.get("image")
    if not isinstance(environment, dict) or not isinstance(image, str) or not image:
        raise ConfigError("The deployment needs a selected image and environment.")
    values = {
        name: decode_compose_dollars(value)
        for name, value in environment.items()
        if isinstance(value, str)
    }
    command = [
        "docker",
        "--context",
        "rootless",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,mode=1777,size=16m",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--entrypoint",
        "python",
    ]
    storage_targets = {
        "/var/lib/moomoo-mcp/data",
        "/app/real-execution-data",
        "/var/lib/moomoo/trading-controls",
    }
    for mount in service.get("volumes", []):
        if not isinstance(mount, dict) or mount.get("target") not in storage_targets:
            continue
        kind = mount.get("type")
        source = mount.get("source")
        if kind == "volume":
            source = model.get("volumes", {}).get(source, {}).get("name", source)
        if kind in {"volume", "bind"} and isinstance(source, str):
            command.extend(
                [
                    "--mount",
                    "type="
                    + kind
                    + ",source="
                    + source
                    + ",target="
                    + mount["target"]
                    + ",readonly",
                ]
            )
    command.extend([image, "-m", "moomoo_mcp.settings", "--validate-storage"])
    try:
        completed = subprocess.run(
            command, input=json.dumps(values).encode(), capture_output=True, check=False
        )
    except OSError:
        raise ConfigError(
            "Could not run the selected image's offline validator."
        ) from None
    if completed.returncode:
        raise ConfigError(
            "The selected image rejected its application settings; "
            "the running deployment was not replaced."
        )


def capture_runtime(path: Path) -> None:
    """Capture an older installation's actual configuration before first upgrade."""
    command = ["docker", "--context", "rootless", "container", "inspect"]

    def inspect(identifier: str) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [*command, "--format", "{{json .}}", identifier],
                capture_output=True,
                check=False,
            )
            value = json.loads(result.stdout)
        except (OSError, ValueError):
            raise ConfigError(
                "Could not snapshot the current runtime configuration."
            ) from None
        if result.returncode or not isinstance(value, dict):
            raise ConfigError("Could not snapshot the current runtime configuration.")
        return value

    container = inspect("moomoo-api-mcp")
    project = (container.get("Config", {}).get("Labels") or {}).get(
        "com.docker.compose.project"
    )
    if not isinstance(project, str) or not project:
        raise ConfigError("The running container has no Compose project identity.")
    model: dict[str, Any] = {"name": project, "services": {}, "volumes": {}}

    def add_service(name: str, runtime: dict[str, Any]) -> None:
        config = runtime["Config"]
        host = runtime["HostConfig"]
        service: dict[str, Any] = {
            "image": runtime["Image"],
            "container_name": runtime["Name"].removeprefix("/"),
            "restart": (host.get("RestartPolicy") or {}).get("Name")
            or "unless-stopped",
            "environment": {
                entry.split("=", 1)[0]: entry.split("=", 1)[1].replace("$", "$$")
                for entry in config.get("Env", [])
                if "=" in entry
            },
            "stdin_open": bool(config.get("OpenStdin")),
            "tty": bool(config.get("Tty")),
        }
        if config.get("User"):
            service["user"] = config["User"]
        mounts = []
        for mount in runtime.get("Mounts", []):
            if mount["Type"] not in {"bind", "volume"}:
                continue
            source = mount.get("Name") if mount["Type"] == "volume" else mount["Source"]
            if mount["Type"] == "volume":
                alias = "state-" + str(len(model["volumes"]))
                model["volumes"][alias] = {"external": True, "name": source}
                source = alias
            mounts.append(
                {
                    "type": mount["Type"],
                    "source": source,
                    "target": mount["Destination"],
                    "read_only": not mount.get("RW", True),
                }
            )
        if mounts:
            service["volumes"] = mounts
        ports = []
        for target, bindings in (host.get("PortBindings") or {}).items():
            for binding in bindings or []:
                number, protocol = target.split("/", 1)
                ports.append(
                    {
                        "target": int(number),
                        "published": binding["HostPort"],
                        "host_ip": binding["HostIp"],
                        "protocol": protocol,
                    }
                )
        if ports:
            service["ports"] = ports
        for source, target in (
            ("ReadonlyRootfs", "read_only"),
            ("CapDrop", "cap_drop"),
            ("SecurityOpt", "security_opt"),
        ):
            if host.get(source):
                service[target] = host[source]
        if host.get("Tmpfs"):
            service["tmpfs"] = [
                target + ":" + options for target, options in host["Tmpfs"].items()
            ]
        model["services"][name] = service

    add_service(SERVICE, container)
    try:
        tunnels = subprocess.run(
            [
                "docker",
                "--context",
                "rootless",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + project,
                "--filter",
                "label=com.docker.compose.service=chatgpt-tunnel",
            ],
            capture_output=True,
            check=False,
        )
        if tunnels.returncode:
            raise ConfigError("Could not snapshot the current tunnel selection.")
        identifiers = tunnels.stdout.decode().split()
        if len(identifiers) > 1:
            raise ConfigError("The current tunnel selection is ambiguous.")
        if identifiers:
            add_service("chatgpt-tunnel", inspect(identifiers[0]))
    except OSError:
        raise ConfigError("Could not snapshot the current tunnel selection.") from None
    save_private_model(path, model)


def paper_overlay() -> str:
    """Keep paper execution configured after disabling a SIMULATE tunnel."""
    services = resolve_services(BASE_COMPOSE_CONFIG)
    environment = services[SERVICE].get("environment", {})
    if not isinstance(environment, dict):
        raise ConfigError(f"{SERVICE} has invalid environment settings.")
    mode = environment.get("MOOMOO_TRADING_MODE") or "READ_ONLY"
    if not isinstance(mode, str):
        raise ConfigError(f"{SERVICE} has an invalid trading mode.")
    return "docker-compose.paper.yml" if mode.strip().upper() == "SIMULATE" else ""


def execution_overlays() -> list[str]:
    """Select environment-owned storage without changing gateway persistence."""
    services = resolve_services(BASE_COMPOSE_CONFIG)
    environment = services[SERVICE].get("environment", {})
    if not isinstance(environment, dict):
        raise ConfigError(f"{SERVICE} has invalid environment settings.")
    mode = environment.get("MOOMOO_TRADING_MODE") or "READ_ONLY"
    if not isinstance(mode, str):
        raise ConfigError(f"{SERVICE} has an invalid trading mode.")
    mode = mode.strip().upper()
    overlays = []
    if mode == "SIMULATE":
        overlays.append("docker-compose.paper.yml")
    elif mode == "REAL":
        paper_accounts = environment.get("MOOMOO_SIMULATED_ACC_IDS") or ""
        if not isinstance(paper_accounts, str):
            raise ConfigError("MOOMOO_SIMULATED_ACC_IDS has an invalid value.")
        if paper_accounts.strip():
            overlays.append("docker-compose.paper.yml")
        overlays.append("docker-compose.real.yml")
    elif mode == "READ_ONLY":
        # The operator channel remains available after write permission is
        # withdrawn; its durable pause/approval state retains the same store.
        pass
    else:
        raise ConfigError(
            f"{SERVICE} has an invalid trading mode. "
            "Set MOOMOO_TRADING_MODE to READ_ONLY, SIMULATE, or REAL; "
            "the ChatGPT tunnel permits only READ_ONLY or SIMULATE."
        )
    if (
        environment.get("MOOMOO_MAX_DAILY_NOTIONAL_BY_CURRENCY") is not None
        or any(
            str(environment.get(name) or "").strip()
            for name in ("MOOMOO_TRADING_CONTROLS_PATH", "MOOMOO_OPERATOR_AUTH_TOKEN")
        )
        or any(
            str(environment.get(name) or "0").strip() != "0"
            for name in (
                "MOOMOO_REQUIRE_TRADE_APPROVAL",
                "MOOMOO_CREATE_TRADING_CONTROLS",
            )
        )
    ):
        overlays.append("docker-compose.controls.yml")
    return overlays


def resolve_token(command: Sequence[str] = COMPOSE_CONFIG) -> str:
    """Return the token Compose resolves for the service; "" means no auth.

    The value is used exactly as Compose resolved it: no quote stripping,
    trimming or interpolation, because Compose has already done all of that.
    The one translation is Compose's own output escaping (see
    decode_compose_dollars), which is how the resolved value is printed.
    """
    services = resolve_services(command)
    service = services.get(SERVICE)
    if not isinstance(service, dict):
        raise ConfigError(f"the Compose configuration has no {SERVICE} service.")
    environment = service.get("environment")
    if not isinstance(environment, dict) or TOKEN_VARIABLE not in environment:
        raise ConfigError(f"{SERVICE} does not set {TOKEN_VARIABLE}.")
    if isinstance(services, dict) and "chatgpt-tunnel" in services:
        mode = (environment.get("MOOMOO_TRADING_MODE") or "READ_ONLY").strip().upper()
        restricted = (environment.get("MCP_RESTRICTED_AUTH_TOKEN") or "").strip()
        permissions = {
            value.strip()
            for value in (
                environment.get("MCP_RESTRICTED_PERMISSIONS")
                or "market_read,real_read,simulate_read,simulate_write"
            ).split(",")
        }
        if restricted and permissions.intersection({"real_write", "gateway_control"}):
            raise ConfigError(
                "The selected tunnel credential must exclude REAL writes "
                "and gateway control."
            )
        if mode == "REAL" and not restricted:
            raise ConfigError(
                "A REAL backend requires a restricted ChatGPT credential."
            )
        if mode not in {"READ_ONLY", "SIMULATE", "REAL"}:
            raise ConfigError("The selected tunnel has an invalid trading mode.")
    token = environment[TOKEN_VARIABLE]
    if not isinstance(token, str):
        raise ConfigError(
            f"{TOKEN_VARIABLE} for {SERVICE} resolves to no value. Set it to the "
            "token, or to an empty string to run without authentication."
        )
    token = decode_compose_dollars(token)
    check_header_safe(token)
    if (
        isinstance(services, dict)
        and "chatgpt-tunnel" in services
        and not token.strip()
    ):
        raise ConfigError("The selected tunnel requires MCP_AUTH_TOKEN.")
    if isinstance(services, dict) and "chatgpt-tunnel" in services:
        tunnel_environment = services["chatgpt-tunnel"].get("environment", {})
        if not isinstance(tunnel_environment, dict):
            raise ConfigError("The selected tunnel has invalid environment settings.")
        for variable in ("CHATGPT_TUNNEL_API_KEY", "CHATGPT_TUNNEL_ID"):
            value = tunnel_environment.get(variable)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"The selected tunnel requires {variable}.")
            if any(character < " " or character > "~" for character in value):
                raise ConfigError(f"The selected tunnel has invalid {variable}.")
        identifier = tunnel_environment["CHATGPT_TUNNEL_ID"].strip()
        suffix = identifier.removeprefix("tunnel_")
        if (
            not identifier.startswith("tunnel_")
            or not 1 <= len(suffix) <= 128
            or not all(
                character.isascii() and (character.isalnum() or character in "_-")
                for character in suffix
            )
        ):
            raise ConfigError("The selected tunnel has invalid CHATGPT_TUNNEL_ID.")
    return token


def decode_compose_dollars(value: str) -> str:
    """Decode the dollar escaping Compose applies to the values it prints.

    `config` output is itself a compose file, so a resolved value containing
    a literal `$` is printed as `$$` — every dollar is doubled, including a
    `$` followed by `{` or a word. The container receives the unescaped
    value, so the pairs are decoded back to single dollars to match it. This
    is the escaping of Compose's output format, not dotenv grammar: nothing
    else about the value is interpreted.

    A lone `$` (which Compose's escaper never prints) passes through.
    """
    pieces: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "$" and value[index + 1 : index + 2] == "$":
            pieces.append("$")
            index += 2
        else:
            pieces.append(value[index])
            index += 1
    return "".join(pieces)


def check_header_safe(token: str) -> None:
    """Refuse what an Authorization header cannot carry.

    HTTP safety, not dotenv grammar: Compose may resolve a multiline or binary
    value perfectly correctly, and it still cannot be sent as a header. The
    server compares tokens as ASCII, so anything else could never match.
    """
    for character in token:
        if character == "\t" or " " <= character <= "~":
            continue
        raise ConfigError(
            f"{TOKEN_VARIABLE} for {SERVICE} contains a line break, control or "
            "non-ASCII character, which an HTTP Authorization header cannot carry."
        )


def display_url(url: str) -> str:
    """The URL without any user:password@ it was given."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def printable(text: str, limit: int = 60) -> str:
    """Server-supplied text reduced to something safe to put in a log line."""
    kept = "".join(c if " " <= c <= "~" else "?" for c in text)
    return kept if len(kept) <= limit else kept[:limit] + "..."


def probe(
    url: str,
    token: str,
    limit: float,
    *,
    request: str = INITIALIZE_REQUEST,
    response_check: Callable[[bytes, str], Attempt] | None = None,
) -> Attempt:
    """POST one MCP request through curl and classify what came back.

    An attempt can verify only when all of these hold: curl exits zero
    (the transfer completed — a partial transfer, timeout or signal
    termination exits nonzero), the HTTP status is 200, and the response
    passes structural MCP validation. The exit status is evaluated
    before any captured content, so valid-looking bytes from a failed
    transfer can never verify.
    """
    argv = [
        "curl",
        "--disable",  # must be first: never read a .curlrc
        "--silent",
        "--proto",
        "=http,https",
        # The token is for this endpoint, not for a proxy on the way to it.
        "--noproxy",
        "*",
        "--max-time",
        f"{limit:g}",
        "--header",
        "Content-Type: application/json",
        "--header",
        "Accept: application/json",
        "--data-binary",
        request,
        "--write-out",
        "\n%{http_code}\n%{content_type}",
    ]
    header = b""
    if token:
        argv += ["--header", "@-"]
        header = f"Authorization: Bearer {token}\n".encode("ascii")
    argv += ["--url", url]
    try:
        completed = subprocess.run(
            argv, input=header, capture_output=True, check=False, timeout=limit + 5
        )
    except subprocess.TimeoutExpired:
        return Attempt("000", retryable=True, problem="curl did not return in time")
    except OSError:
        return Attempt("000", problem="could not run curl")
    if completed.returncode != 0:
        # The exit status is retained and evaluated independently of the
        # captured output: curl documents exit 18 as a partial transfer, and
        # a status line of 200 in output from an aborted transfer does not
        # override it. Content is never read on a nonzero exit.
        return Attempt(
            "000",
            retryable=completed.returncode in RETRYABLE_CURL_EXITS,
            problem=f"no HTTP response (curl exit {completed.returncode})",
        )
    head, _, content_type = completed.stdout.rpartition(b"\n")
    body, _, status_bytes = head.rpartition(b"\n")
    status = status_bytes.decode("ascii", "replace") or "000"
    if status in ("401", "403"):
        return Attempt(status, problem=f"HTTP {status}")
    if status == "000" or (len(status) == 3 and status.startswith("5")):
        return Attempt(status, retryable=True, problem=f"HTTP {status}")
    if status != "200":
        return Attempt(status, problem=f"HTTP {printable(status)}")
    if response_check is not None:
        return response_check(body, content_type.decode("latin-1"))
    problem = initialize_problem(body, content_type.decode("latin-1"))
    if problem is not None:
        return Attempt(status, problem=problem)
    return Attempt(status, verified=True)


def initialize_problem(body: bytes, content_type: str) -> str | None:
    """Why a 200 response is not a successful initialize, or None if it is.

    The supported endpoint returns a single JSON-RPC JSON response.
    """
    message, problem = response_message(body, content_type)
    return problem if problem is not None else initialize_result_problem(message)


def response_message(body: bytes, content_type: str) -> tuple[Any, str | None]:
    """Decode the single JSON response required by our HTTP endpoint."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        shown = printable(media_type) if media_type else "untyped"
        return None, f"the body is {shown}, not JSON"
    try:
        return json.loads(body), None
    except ValueError:
        return None, "the body is not valid JSON"


def initialize_result_problem(message: Any) -> str | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return "it is not a JSON-RPC 2.0 message"
    if message.get("id") != REQUEST_ID:
        return "its id does not match the initialize request"
    if "error" in message:
        error = message["error"]
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, int) and not isinstance(code, bool):
            return f"it is JSON-RPC error {code}"
        return "it is a JSON-RPC error"
    result = message.get("result")
    if not isinstance(result, dict):
        return "it carries no initialize result"
    version = result.get("protocolVersion")
    if not isinstance(version, str) or not valid_protocol_version(version):
        return "the initialize result has no valid protocolVersion"
    if not isinstance(result.get("capabilities"), dict):
        return "the initialize result has no capabilities"
    info = result.get("serverInfo")
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("name"), str)
        or not isinstance(info.get("version"), str)
    ):
        return "the initialize result has no well-formed serverInfo"
    return None


def valid_protocol_version(version: Any) -> bool:
    """Whether protocolVersion is a non-empty string.

    The probe confirms that configured authentication can access the endpoint
    and receive a structurally expected initialize response matching the request;
    it does not certify protocol version compatibility or trading availability.
    """
    return isinstance(version, str) and bool(version.strip())


def verify(
    url: str,
    timeout: int,
    token: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Probe until verified, refused, or out of time.

    Only an endpoint that is not answering yet (no response, or a 5xx) is
    retried. A refusal or a wrong answer is final: waiting cannot fix a
    mismatched token or a URL that does not speak MCP. ``timeout`` 0 means one
    attempt and no retries.
    """
    shown = display_url(url)
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        limit = (
            ATTEMPT_SECONDS
            if timeout == 0
            else max(1.0, min(ATTEMPT_SECONDS, remaining))
        )
        attempt = probe(url, token, limit)
        if attempt.verified:
            how = "with the configured token" if token else "without a token"
            say(f"{shown} completed an MCP initialize {how}.")
            return True
        if attempt.status in ("401", "403"):
            say(
                f"Authentication refused ({attempt.problem}): {shown} rejected the "
                f"{TOKEN_VARIABLE} Compose resolves for {SERVICE}. The server is "
                "running with a different token, or this is not its endpoint."
            )
            return False
        if attempt.status == "200":
            say(
                f"Invalid MCP response: {shown} answered HTTP 200, but not with an "
                f"initialize result ({attempt.problem}). DEPLOY_VERIFY_URL may not "
                "point at the MCP endpoint."
            )
            return False
        if not attempt.retryable:
            say(f"Verification failed: {shown} gave {attempt.problem}.")
            return False
        remaining = deadline - clock()
        if remaining <= 0:
            say(
                f"Not reachable: no initialize result from {shown} within "
                f"{timeout}s (last attempt: {attempt.problem})."
            )
            return False
        sleep(min(RETRY_INTERVAL, remaining))


def whole_seconds(value: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError("must be a whole number of seconds")
    return int(value)


def health_response(body: bytes, content_type: str) -> Attempt:
    """Classify health using fixed labels; never relay broker-supplied errors."""
    message, problem = response_message(body, content_type)
    if problem is not None:
        return Attempt("200", problem="invalid check_health response")
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or message.get("id") != HEALTH_REQUEST_ID
        or "error" in message
    ):
        return Attempt("200", problem="invalid check_health response")
    result = message.get("result")
    if not isinstance(result, dict) or result.get("isError", False) is not False:
        return Attempt("200", problem="check_health failed")
    health = result.get("structuredContent")
    if not isinstance(health, dict):
        return Attempt("200", problem="check_health has no structured result")
    status = health.get("status")
    quote = health.get("quote")
    trade = health.get("trade")
    if (
        status not in ("connected", "degraded", "disconnected")
        or not isinstance(quote, dict)
        or not isinstance(trade, dict)
        or quote.get("status") not in ("ok", "error", "timeout", "unavailable")
        or trade.get("status") not in ("ok", "error", "timeout", "unavailable")
    ):
        return Attempt("200", problem="invalid check_health status")
    if quote.get("logged_in") is False:
        return Attempt("200", retryable=True, problem="OpenD login required")
    if status != "connected" or quote["status"] != "ok" or trade["status"] != "ok":
        problem = (
            "OpenD probes incomplete" if status == "connected" else f"OpenD {status}"
        )
        return Attempt("200", retryable=True, problem=problem)
    if quote.get("logged_in") is not True:
        return Attempt("200", retryable=True, problem="OpenD login unconfirmed")
    return Attempt("200", verified=True)


def gateway_readiness(
    url: str,
    timeout: int,
    token: str,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for gateway probes and confirmed login independently of deployment."""
    deadline = clock() + timeout
    problem = "readiness deadline expired"
    while True:
        remaining = deadline - clock()
        limit = ATTEMPT_SECONDS if timeout == 0 else min(ATTEMPT_SECONDS, remaining)
        if limit <= 0:
            break
        attempt = probe(
            url, token, limit, request=HEALTH_REQUEST, response_check=health_response
        )
        if attempt.verified:
            say("OpenD ready: quote login confirmed; quote and trade probes succeeded.")
            return True
        problem = attempt.problem
        remaining = deadline - clock()
        if not attempt.retryable or remaining <= 0:
            break
        sleep(min(RETRY_INTERVAL, remaining))
    say(f"OpenD readiness warning: {problem}.")
    say(
        "OpenD readiness is unconfirmed. Check gateway diagnostics and "
        "account/region configuration. If initial "
        "login is incomplete or remembered login is unavailable, run:"
    )
    say("  ./scripts/compose-prod.sh stop moomoo-mcp")
    say(
        "  ./scripts/compose-prod.sh run --rm --no-deps -it "
        "-e OPEND_INTERACTIVE=1 -e OPEND_MAX_RESTARTS=0 moomoo-mcp"
    )
    say(
        "Complete the terminal prompts, choose Y to remember the password, "
        "confirm successful login, then Ctrl-C. Keep OPEND_INTERACTIVE=0 and "
        "MOOMOO_LOGIN_BY_REMEMBER=1 for background operation, then run:"
    )
    say("  ./scripts/compose-prod.sh up -d moomoo-mcp")
    say("  python3 scripts/deploy_verify.py gateway-readiness")
    return False


def on_signal(signum: int, _frame: FrameType | None) -> None:
    raise Interrupted(signum)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_verify.py",
        description=(
            "Read MCP_AUTH_TOKEN from the configuration Compose resolves, and "
            "verify MCP authentication or separately check OpenD readiness."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check-config", help="fail unless Compose resolves a usable token"
    )
    commands.add_parser(
        "paper-overlay", help="print only the paper overlay needed without a tunnel"
    )
    commands.add_parser(
        "execution-overlays", help="print the storage overlays needed without a tunnel"
    )
    snapshot_parser = commands.add_parser(
        "save-config", help="save effective private configuration for this deployment"
    )
    snapshot_parser.add_argument("path", type=Path)
    capture_parser = commands.add_parser(
        "capture-runtime", help="snapshot the existing runtime before its first upgrade"
    )
    capture_parser.add_argument("path", type=Path)
    commands.add_parser(
        "preflight", help="validate the selected image's application settings offline"
    )
    verify_parser = commands.add_parser(
        "verify", help="send an authenticated MCP initialize and validate the result"
    )
    verify_parser.add_argument("--url", default=DEFAULT_URL)
    verify_parser.add_argument(
        "--timeout",
        type=whole_seconds,
        default=DEFAULT_TIMEOUT,
        help="seconds to keep retrying an endpoint that is not up yet (0: once)",
    )
    gateway_parser = commands.add_parser(
        "gateway-readiness", help="report gateway probes and confirmed quote login"
    )
    gateway_parser.add_argument("--url", default=DEFAULT_URL)
    gateway_parser.add_argument(
        "--timeout",
        type=whole_seconds,
        default=DEFAULT_GATEWAY_TIMEOUT,
        help="seconds to wait for OpenD readiness (0: once)",
    )
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        if args.command == "paper-overlay":
            print(paper_overlay())
            return 0
        if args.command == "execution-overlays":
            for overlay in execution_overlays():
                print(overlay)
            return 0
        if args.command == "capture-runtime":
            capture_runtime(args.path)
            return 0
        token = resolve_token()
        if args.command == "save-config":
            save_private_model(args.path, resolve_model(COMPOSE_CONFIG))
            return 0
        if args.command == "preflight":
            validate_application(resolve_model(COMPOSE_CONFIG))
            say("Selected image application settings validated without broker access.")
            return 0
        if args.command == "check-config":
            if token:
                say(f"{TOKEN_VARIABLE} resolves for {SERVICE}; verify will send it.")
            else:
                say(
                    f"{TOKEN_VARIABLE} is empty for {SERVICE}: the endpoint runs "
                    "without authentication, and verify will send no token."
                )
            return 0
        if args.command == "gateway-readiness":
            return 0 if gateway_readiness(args.url, args.timeout, token) else 1
        return 0 if verify(args.url, args.timeout, token) else 1
    except ConfigError as error:
        say(f"Configuration error: {error}")
        return 1
    except Interrupted as interruption:
        # subprocess.run has already killed curl or Compose on the way out.
        # Die of the same signal, so the caller sees an interruption rather
        # than an ordinary failure.
        say("Interrupted before verification completed.")
        signal.signal(interruption.signum, signal.SIG_DFL)
        os.kill(os.getpid(), interruption.signum)
        return 128 + interruption.signum


if __name__ == "__main__":
    sys.exit(main())
