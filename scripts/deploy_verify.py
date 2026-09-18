#!/usr/bin/env python3
"""Check a deployment's configuration, then verify its MCP endpoint.

deploy.sh decides what to deploy and how to roll it back. This answers the two
questions it used to answer by re-implementing Docker Compose in Bash:

  check-config  Does Compose resolve a usable MCP_AUTH_TOKEN for the service?
  verify        Does the running endpoint accept that token and answer an MCP
                initialize with a well-formed result?

The configuration is what ``scripts/compose-prod.sh config --format json``
resolves -- the same wrapper, Docker context, env files and compose files that
start the container -- read from the service's own environment rather than
from an identically named interpolation variable. There is no other source: no
.env parsing here, and no fallback when Compose cannot answer. The resolved
model carries every credential in the deployment, so it stays in memory and is
never printed or written anywhere; diagnostics name what went wrong, not what
was read.

The token reaches curl on stdin (``--header @-``), never argv or a file.

Verification means the endpoint accepts the configured authentication and
returns a valid initialize result. It says nothing about the broker login or
whether trading works.

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
DEFAULT_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_TIMEOUT = 90
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


def resolve_token(command: Sequence[str] = COMPOSE_CONFIG) -> str:
    """Return the token Compose resolves for the service; "" means no auth.

    The value is used exactly as Compose resolved it: no quote stripping,
    trimming or interpolation, because Compose has already done all of that.
    The one translation is Compose's own output escaping (see
    decode_compose_dollars), which is how the resolved value is printed.
    """
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
    service = services.get(SERVICE) if isinstance(services, dict) else None
    if not isinstance(service, dict):
        raise ConfigError(f"the Compose configuration has no {SERVICE} service.")
    environment = service.get("environment")
    if not isinstance(environment, dict) or TOKEN_VARIABLE not in environment:
        raise ConfigError(f"{SERVICE} does not set {TOKEN_VARIABLE}.")
    token = environment[TOKEN_VARIABLE]
    if not isinstance(token, str):
        raise ConfigError(
            f"{TOKEN_VARIABLE} for {SERVICE} resolves to no value. Set it to the "
            "token, or to an empty string to run without authentication."
        )
    token = decode_compose_dollars(token)
    check_header_safe(token)
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


def probe(url: str, token: str, limit: float) -> Attempt:
    """POST one initialize through curl and classify what came back.

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
        "Accept: application/json, text/event-stream",
        "--data-binary",
        INITIALIZE_REQUEST,
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
    problem = initialize_problem(body, content_type.decode("latin-1"))
    if problem is not None:
        return Attempt(status, problem=problem)
    return Attempt(status, verified=True)


def initialize_problem(body: bytes, content_type: str) -> str | None:
    """Why a 200 response is not a successful initialize, or None if it is.

    Streamable HTTP answers a POST either with the JSON-RPC message itself or
    with an SSE stream carrying it, so both are accepted and checked alike.
    """
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        try:
            message: Any = json.loads(body)
        except ValueError:
            return "the body is not valid JSON"
        return initialize_result_problem(message)
    if media_type == "text/event-stream":
        return sse_problem(body)
    shown = printable(media_type) if media_type else "untyped"
    return f"the body is {shown}, not JSON or an SSE stream"


def sse_events(text: str) -> list[str]:
    """The data of each complete event in an SSE stream.

    Lines end in CRLF, LF or CR; a blank line dispatches an event; a trailing
    event with no blank line after it is incomplete and, as the SSE standard
    says, dropped.
    """
    events: list[str] = []
    data: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line == "":
            if data:
                events.append("\n".join(data))
                data = []
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
    return events


def sse_problem(body: bytes) -> str | None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "the SSE stream is not UTF-8"
    for data in sse_events(text):
        try:
            message: Any = json.loads(data)
        except ValueError:
            return "an SSE event is not valid JSON"
        # Notifications may precede the response; only the answer counts.
        if isinstance(message, dict) and message.get("id") == REQUEST_ID:
            return initialize_result_problem(message)
    return "the SSE stream carries no response to the initialize request"


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
    if not isinstance(version, str) or not supported_protocol_version(version):
        return "the initialize result has no supported protocolVersion"
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


def supported_protocol_version(version: Any) -> bool:
    """Whether protocolVersion indicates a valid MCP initialize response.

    Accepts any non-empty string. A deploy probe verifies that the running server
    completed the MCP initialize handshake, avoiding brittle deploy breakages
    when MCP protocol versions are upgraded or negotiated.
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


def on_signal(signum: int, _frame: FrameType | None) -> None:
    raise Interrupted(signum)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deploy_verify.py",
        description=(
            "Read MCP_AUTH_TOKEN from the configuration Compose resolves, and "
            "verify the MCP endpoint accepts it."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check-config", help="fail unless Compose resolves a usable token"
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
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        token = resolve_token()
        if args.command == "check-config":
            if token:
                say(f"{TOKEN_VARIABLE} resolves for {SERVICE}; verify will send it.")
            else:
                say(
                    f"{TOKEN_VARIABLE} is empty for {SERVICE}: the endpoint runs "
                    "without authentication, and verify will send no token."
                )
            return 0
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
