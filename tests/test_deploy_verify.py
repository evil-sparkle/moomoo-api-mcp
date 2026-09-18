"""Exercise scripts/deploy_verify.py against a real curl and a local HTTP server.

The probe is only as good as what actually crosses the wire, so nothing here
stubs curl: a throwaway server records the headers it receives and answers with
whatever each test scripts. Configuration comes from a stand-in for
compose-prod.sh, because what Compose itself resolves is the business of
tests/test_compose_config_resolution.py.
"""

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scripts import deploy_verify

ROOT = Path(__file__).resolve().parents[1]
REAL_CURL = shutil.which("curl")
TOKEN = "dummy-token-not-a-secret"
# Stands for every other credential the resolved configuration carries.
OTHER_SECRET = "dummy-trade-password-not-a-secret"

VALID_RESULT = {
    "jsonrpc": "2.0",
    "id": deploy_verify.REQUEST_ID,
    "result": {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "moomoo-api-mcp", "version": "1.0"},
    },
}


def model(token: object = TOKEN, *, extra=None):
    environment: dict[str, object] = {
        "MCP_AUTH_TOKEN": token,
        "MOOMOO_TRADE_PASSWORD": OTHER_SECRET,
    }
    environment.update(extra or {})
    return {"services": {"moomoo-mcp": {"environment": environment}}}


def fake_compose(output, returncode=0, stderr=""):
    """A command that prints ``output`` the way compose-prod.sh config would."""
    script = (
        "import sys;"
        f"sys.stdout.write({output!r});"
        f"sys.stderr.write({stderr!r});"
        f"sys.exit({returncode})"
    )
    return [sys.executable, "-c", script]


def json_reply(message, status=200):
    return (status, "application/json", json.dumps(message).encode())


def sse_reply(*messages, line_end="\n"):
    body = ""
    for message in messages:
        body += f"event: message{line_end}data: {json.dumps(message)}{line_end}"
        body += line_end
    return (200, "text/event-stream", body.encode())


class ScriptedServer:
    """Answers each POST with the next scripted reply, repeating the last."""

    def __init__(self, replies, delay=0.0):
        self.replies = list(replies)
        self.delay = delay
        self.requests = []
        self.received = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                server.requests.append(
                    {
                        "headers": dict(self.headers.items()),
                        "body": self.rfile.read(length).decode(),
                    }
                )
                server.received.set()
                if server.delay:
                    time.sleep(server.delay)
                index = min(len(server.requests), len(server.replies)) - 1
                reply = server.replies[index]
                status, content_type, body = reply[:3]
                # A fourth element overstates Content-Length by that many
                # bytes. The handler returns and the connection closes with
                # bytes still declared, so real curl exits 18 (partial
                # transfer) — even when every byte of the body arrived.
                extra = reply[3] if len(reply) > 3 else 0
                self.send_response(status)
                if content_type:
                    self.send_header("Content-Type", content_type)
                if status in (301, 302):
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", str(len(body) + extra))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        # A short poll, so shutting the server down between subtests is quick.
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, args=(0.05,), daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port_url():
    """A loopback URL nothing is listening on."""
    probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()
    return f"http://127.0.0.1:{port}/mcp"


def run_verify(url, token=TOKEN, timeout=0):
    """verify() in process, returning (verified, what it printed)."""
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        verified = deploy_verify.verify(url, timeout, token)
    return verified, captured.getvalue()


class ResolveTokenTest(unittest.TestCase):
    """The token is Compose's resolved value, or a configuration error."""

    def test_nonempty_token_is_used_exactly_as_resolved(self):
        # Quotes, inner and surrounding spaces, '=' and '$' are all Compose's
        # business. Whatever it resolved is what gets sent.
        for token in (TOKEN, '"quoted"', " spaced out ", "a=b:c", "$literal"):
            with self.subTest(token=token):
                command = fake_compose(json.dumps(model(token)))
                self.assertEqual(deploy_verify.resolve_token(command), token)

    def test_compose_dollar_escaping_is_decoded(self):
        """config output doubles every literal $; the container got single.

        Compose prints its configuration as compose input, where a literal
        `$` must be escaped: the container holds `literal $X` while the JSON
        the helper reads says `literal $$X`. Sending the printed form 401s a
        healthy deploy whose token contains a dollar, so the pairs are
        decoded back to what the container received. A lone `$` — which
        Compose's escaper never prints — passes through unchanged.
        """
        cases = {
            "a$$b": "a$b",
            "$$": "$",
            "$$$$$$": "$$$",
            "$${x}": "${x}",
            "$$word": "$word",
            "plain": "plain",
            "$": "$",
        }
        for escaped, decoded in cases.items():
            with self.subTest(printed=escaped):
                command = fake_compose(json.dumps(model(escaped)))
                self.assertEqual(deploy_verify.resolve_token(command), decoded)

    def test_explicit_empty_token_means_no_authentication(self):
        command = fake_compose(json.dumps(model("")))
        self.assertEqual(deploy_verify.resolve_token(command), "")

    def test_unusable_configuration_is_an_error_never_a_fallback(self):
        cases = {
            "compose failed": fake_compose("", returncode=1),
            "not json": fake_compose("services: {}"),
            "not an object": fake_compose("[]"),
            "no services": fake_compose("{}"),
            "no service": fake_compose(json.dumps({"services": {"other": {}}})),
            "no environment": fake_compose(
                json.dumps({"services": {"moomoo-mcp": {}}})
            ),
            "token not declared": fake_compose(
                json.dumps({"services": {"moomoo-mcp": {"environment": {"X": "1"}}}})
            ),
            "null token": fake_compose(json.dumps(model(None))),
            "numeric token": fake_compose(json.dumps(model(42))),
            "missing command": [str(ROOT / "no-such-compose-wrapper")],
        }
        for name, command in cases.items():
            with self.subTest(name), self.assertRaises(deploy_verify.ConfigError):
                deploy_verify.resolve_token(command)

    def test_compose_errors_are_not_relayed(self):
        """Compose can quote the env-file line it choked on; that stays private."""
        command = fake_compose("", returncode=15, stderr=f"bad line: {TOKEN}")
        with self.assertRaises(deploy_verify.ConfigError) as caught:
            deploy_verify.resolve_token(command)
        self.assertIn("exit 15", str(caught.exception))
        self.assertIn("config --quiet", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_header_unsafe_values_are_refused_without_being_echoed(self):
        """Compose may rightly resolve these; a header still cannot carry them."""
        for token in ("line\nbreak", "carriage\rreturn", "nul\0byte", "caf\u00e9"):
            with self.subTest(token=token):
                command = fake_compose(json.dumps(model(token)))
                with self.assertRaises(deploy_verify.ConfigError) as caught:
                    deploy_verify.resolve_token(command)
                self.assertIn("Authorization header", str(caught.exception))
                self.assertNotIn(token, str(caught.exception))

    def test_tab_is_header_safe(self):
        command = fake_compose(json.dumps(model("tab\there")))
        self.assertEqual(deploy_verify.resolve_token(command), "tab\there")


@unittest.skipIf(REAL_CURL is None, "curl is not installed")
class ProbeTest(unittest.TestCase):
    """What the endpoint says, classified; with real curl on the wire."""

    def setUp(self):
        patcher = mock.patch.object(deploy_verify, "RETRY_INTERVAL", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_json_result_verifies_and_sends_the_token(self):
        with ScriptedServer([json_reply(VALID_RESULT)]) as server:
            verified, output = run_verify(server.url)
        self.assertTrue(verified, output)
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertIn("text/event-stream", request["headers"]["Accept"])
        self.assertEqual(json.loads(request["body"])["method"], "initialize")
        self.assertNotIn(TOKEN, output)

    def test_empty_token_sends_no_authorization_header(self):
        with ScriptedServer([json_reply(VALID_RESULT)]) as server:
            verified, output = run_verify(server.url, token="")
        self.assertTrue(verified, output)
        self.assertNotIn("Authorization", server.requests[0]["headers"])
        self.assertIn("without a token", output)

    def test_valid_sse_result_verifies(self):
        notification = {"jsonrpc": "2.0", "method": "notifications/message"}
        for line_end in ("\n", "\r\n", "\r"):
            with (
                self.subTest(line_end=line_end),
                ScriptedServer(
                    [sse_reply(notification, VALID_RESULT, line_end=line_end)]
                ) as server,
            ):
                verified, output = run_verify(server.url)
                self.assertTrue(verified, output)

    def test_200_without_a_valid_initialize_result_fails_at_once(self):
        """HTTP 200 alone is never success, and never worth retrying."""
        wrong_id = dict(VALID_RESULT, id=1)
        no_server_info = {
            "jsonrpc": "2.0",
            "id": deploy_verify.REQUEST_ID,
            "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
        no_capabilities = {
            "jsonrpc": "2.0",
            "id": deploy_verify.REQUEST_ID,
            "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "x", "version": "0"},
            },
        }
        error = {
            "jsonrpc": "2.0",
            "id": deploy_verify.REQUEST_ID,
            "error": {"code": -32600, "message": f"echoes {TOKEN}"},
        }
        cases = {
            "malformed json": (200, "application/json", b'{"jsonrpc": "2.0",'),
            "wrong id": json_reply(wrong_id),
            "json-rpc error": json_reply(error),
            "not json-rpc": json_reply({"ok": True}),
            "no serverInfo": json_reply(no_server_info),
            "no capabilities": json_reply(no_capabilities),
            # Field names alone are not the initialize result: the lifecycle
            # puts protocolVersion, capabilities and serverInfo inside the
            # result object, and validation reads them only there.
            "fields outside the result object": json_reply(
                {
                    "jsonrpc": "2.0",
                    "id": deploy_verify.REQUEST_ID,
                    "result": None,
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "x", "version": "0"},
                }
            ),
            "all null at top level": json_reply(
                {"result": None, "protocolVersion": None, "serverInfo": None}
            ),
            "html": (200, "text/html", b"<html>result serverInfo</html>"),
            "no content type": (200, "", json.dumps(VALID_RESULT).encode()),
            "sse without the response": sse_reply({"jsonrpc": "2.0", "method": "x"}),
            "sse with bad json": (200, "text/event-stream", b"data: {nope\n\n"),
            "sse error": sse_reply(error),
            # An event with no blank line after it was never dispatched.
            "sse unterminated": (
                200,
                "text/event-stream",
                f"data: {json.dumps(VALID_RESULT)}".encode(),
            ),
        }
        for name, reply in cases.items():
            with self.subTest(name), ScriptedServer([reply]) as server:
                verified, output = run_verify(server.url, timeout=5)
                self.assertFalse(verified)
                self.assertEqual(len(server.requests), 1, "a 200 must not be retried")
                self.assertIn("Invalid MCP response", output)
                self.assertIn("initialize", output)
                self.assertNotIn(TOKEN, output)

    def test_a_partial_transfer_never_verifies_even_with_valid_content(self):
        """The transport check itself, proven with real curl.

        Curl documents exit 18 as a partial transfer. In every case here the
        connection closes with bytes still declared, so curl exits 18 —
        while the status line says 200 and the captured bytes would parse as
        a complete, valid initialize response. Content must not rescue the
        failed transfer: adding JSON parsing while ignoring curl's exit
        status would leave exactly this case broken, which is why the valid
        content case is here and not just a truncated one.
        """
        complete = json.dumps(VALID_RESULT).encode()
        truncated = complete[: len(complete) // 2]
        cases = {
            "valid json, transfer incomplete": (
                200,
                "application/json",
                complete,
                16,
            ),
            "truncated json, transfer incomplete": (
                200,
                "application/json",
                truncated,
                len(complete) - len(truncated),
            ),
            "complete sse event, transfer incomplete": (
                200,
                "text/event-stream",
                f"event: message\ndata: {json.dumps(VALID_RESULT)}\n\n".encode(),
                7,
            ),
        }
        for name, (status, content_type, body, extra) in cases.items():
            with (
                self.subTest(name),
                ScriptedServer([(status, content_type, body, extra)]) as server,
            ):
                verified, output = run_verify(server.url, timeout=5)
                self.assertFalse(verified)
                self.assertEqual(len(server.requests), 1, "never retried to success")
                self.assertIn("curl exit 18", output)
                self.assertIn("Verification failed", output)
                self.assertNotIn("completed an MCP initialize", output)

    def test_the_protocol_version_must_be_a_supported_one(self):
        """A version is one MCP issued for the initialize lifecycle, not a date.

        `2024-11-05` and `2025-11-25` are real handshake versions from before
        and after this server; a probe must accept a healthy server speaking
        any of them. `2026-07-28` is a real revision that *removed* the
        initialize exchange, so an initialize response claiming it is
        semantically impossible and must not verify; `9999-99-99` and
        `2025-13-40` are date-shaped strings MCP never issued; `banana`, a
        misformatted date, non-ASCII digits and a number are not versions.
        """
        for version in sorted(deploy_verify.HANDSHAKE_PROTOCOL_VERSIONS):
            with self.subTest(accepts=version):
                accepted = dict(VALID_RESULT)
                accepted["result"] = dict(
                    VALID_RESULT["result"], protocolVersion=version
                )
                with ScriptedServer([json_reply(accepted)]) as server:
                    verified, output = run_verify(server.url)
                    self.assertTrue(verified, output)

        for version in (
            "2026-07-28",
            "9999-99-99",
            "2025-13-40",
            "banana",
            "",
            "2025-6-18",
            "٢٠٢٥-٠٦-١٨",
            20250618,
        ):
            with self.subTest(rejects=version):
                invalid = dict(VALID_RESULT)
                invalid["result"] = dict(
                    VALID_RESULT["result"], protocolVersion=version
                )
                with ScriptedServer([json_reply(invalid)]) as server:
                    verified, output = run_verify(server.url, timeout=5)
                    self.assertFalse(verified)
                    self.assertEqual(len(server.requests), 1)
                    self.assertIn("protocolVersion", output)

    def test_auth_refusal_fails_at_once_with_a_diagnosis(self):
        for status in (401, 403):
            with (
                self.subTest(status=status),
                ScriptedServer([(status, "application/json", b"{}")]) as server,
            ):
                verified, output = run_verify(server.url, timeout=5)
                self.assertFalse(verified)
                self.assertEqual(len(server.requests), 1)
                self.assertIn("Authentication refused", output)
                self.assertIn("MCP_AUTH_TOKEN", output)

    def test_other_statuses_fail_at_once(self):
        for status in (302, 404, 400):
            with (
                self.subTest(status=status),
                ScriptedServer([(status, "text/plain", b"no")]) as server,
            ):
                verified, output = run_verify(server.url, timeout=5)
                self.assertFalse(verified)
                # A redirect is reported, not followed.
                self.assertEqual(len(server.requests), 1)
                self.assertIn(f"HTTP {status}", output)

    def test_server_errors_are_retried_until_the_endpoint_recovers(self):
        replies = [(503, "text/plain", b"starting"), (502, "", b"")]
        replies.append(json_reply(VALID_RESULT))
        with ScriptedServer(replies) as server:
            verified, output = run_verify(server.url, timeout=30)
        self.assertTrue(verified, output)
        self.assertEqual(len(server.requests), 3)

    def test_the_deadline_is_elapsed_time(self):
        with ScriptedServer([(503, "text/plain", b"down")]) as server:
            started = time.monotonic()
            verified, output = run_verify(server.url, timeout=1)
            elapsed = time.monotonic() - started
        self.assertFalse(verified)
        self.assertGreater(len(server.requests), 1)
        self.assertGreaterEqual(elapsed, 1)
        self.assertLess(elapsed, 5)
        self.assertIn("Not reachable", output)
        self.assertIn("HTTP 503", output)

    def test_attempts_are_cut_short_by_the_deadline(self):
        """A server that never answers cannot stretch the deadline by a full
        attempt's worth."""
        with ScriptedServer([json_reply(VALID_RESULT)], delay=8) as server:
            started = time.monotonic()
            verified, output = run_verify(server.url, timeout=2)
            elapsed = time.monotonic() - started
        self.assertFalse(verified)
        self.assertLess(elapsed, 5)
        self.assertIn("curl exit 28", output)

    def test_timeout_zero_is_one_attempt(self):
        with ScriptedServer([(503, "text/plain", b"down")]) as server:
            verified, _ = run_verify(server.url, timeout=0)
        self.assertFalse(verified)
        self.assertEqual(len(server.requests), 1)

    def test_nothing_listening_is_reported_as_unreachable(self):
        verified, output = run_verify(closed_port_url(), timeout=0)
        self.assertFalse(verified)
        self.assertIn("Not reachable", output)
        self.assertIn("curl exit 7", output)

    def test_credentials_in_the_url_are_not_printed(self):
        url = closed_port_url().replace("http://", "http://user:hunter2@")
        verified, output = run_verify(url, timeout=0)
        self.assertFalse(verified)
        self.assertNotIn("hunter2", output)
        self.assertIn("http://127.0.0.1:", output)


@unittest.skipIf(REAL_CURL is None, "curl is not installed")
class CommandLineTest(unittest.TestCase):
    """The helper as deploy.sh runs it: a process, with secrets to keep."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        scripts = self.root / "repo" / "scripts"
        scripts.mkdir(parents=True)
        self.helper = scripts / "deploy_verify.py"
        shutil.copy2(ROOT / "scripts" / "deploy_verify.py", self.helper)
        # compose-prod.sh's stand-in prints whatever model the test wrote.
        self.model_file = self.root / "model.json"
        self.model_file.write_text(json.dumps(model()))
        compose = scripts / "compose-prod.sh"
        compose.write_text(f'#!/bin/sh\nexec cat "{self.model_file}"\n')
        compose.chmod(0o755)
        # curl, logging its argv before becoming the real one.
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.argv_log = self.root / "curl-argv.jsonl"
        wrapper = bin_dir / "curl"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"with open({str(self.argv_log)!r}, 'a') as log:\n"
            "    log.write(json.dumps(sys.argv) + '\\n')\n"
            f"os.execv({REAL_CURL!r}, [{REAL_CURL!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        # An empty TMPDIR, so any file the helper leaves behind is visible.
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        self.env = {
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "TMPDIR": str(self.tmpdir),
        }

    def run_helper(self, *args):
        return subprocess.run(
            [sys.executable, str(self.helper), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_no_secret_leaked(self, result):
        for secret in (TOKEN, OTHER_SECRET):
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)
            if self.argv_log.exists():
                self.assertNotIn(secret, self.argv_log.read_text())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.tmpdir.iterdir()), [], "a temporary file was left")
        leftovers = {p.name for p in (self.root / "repo").rglob("*") if p.is_file()}
        self.assertEqual(leftovers, {"deploy_verify.py", "compose-prod.sh"})

    def test_verify_sends_the_token_on_stdin_only(self):
        with ScriptedServer([json_reply(VALID_RESULT)]) as server:
            result = self.run_helper("verify", "--url", server.url, "--timeout", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            server.requests[0]["headers"]["Authorization"], f"Bearer {TOKEN}"
        )
        argv = json.loads(self.argv_log.read_text().splitlines()[0])
        self.assertEqual(argv[1], "--disable", "--disable only works as argument one")
        self.assertIn("@-", argv)
        self.assertNotIn("--location", argv)
        self.assert_no_secret_leaked(result)

    def test_failures_leak_nothing(self):
        cases = {
            "refused": [(401, "application/json", f'{{"t":"{TOKEN}"}}'.encode())],
            "echoing error": [
                json_reply(
                    {
                        "jsonrpc": "2.0",
                        "id": deploy_verify.REQUEST_ID,
                        "error": {"code": 1, "message": TOKEN},
                    }
                )
            ],
        }
        for name, replies in cases.items():
            with self.subTest(name), ScriptedServer(replies) as server:
                result = self.run_helper("verify", "--url", server.url)
                self.assertEqual(result.returncode, 1)
                self.assert_no_secret_leaked(result)

    def test_check_config_reports_without_the_token(self):
        result = self.run_helper("check-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resolves for moomoo-mcp", result.stderr)
        self.assert_no_secret_leaked(result)

    def test_check_config_fails_on_a_config_error(self):
        self.model_file.write_text(json.dumps(model("two\nlines")))
        result = self.run_helper("check-config")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Configuration error", result.stderr)

    def test_verify_refuses_to_probe_without_usable_configuration(self):
        self.model_file.write_text("not json")
        with ScriptedServer([json_reply(VALID_RESULT)]) as server:
            result = self.run_helper("verify", "--url", server.url)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Configuration error", result.stderr)
        self.assertEqual(server.requests, [])

    def test_timeout_must_be_whole_seconds(self):
        for value in ("abc", "-1", "1.5", ""):
            with self.subTest(value=value):
                result = self.run_helper("verify", "--timeout", value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("whole number of seconds", result.stderr)

    def test_interruption_stops_the_probe_and_reports_the_signal(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with (
                self.subTest(signal=signum.name),
                ScriptedServer([json_reply(VALID_RESULT)], delay=30) as server,
            ):
                process = subprocess.Popen(
                    [sys.executable, str(self.helper), "verify", "--url", server.url],
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertTrue(server.received.wait(10), "the probe never arrived")
                started = time.monotonic()
                process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=10)
                self.assertLess(time.monotonic() - started, 5)
                # Died of the signal, as an interrupted command should.
                self.assertEqual(process.returncode, -signum)
                self.assertIn("Interrupted", stderr)
                self.assert_no_secret_leaked(
                    subprocess.CompletedProcess([], process.returncode, stdout, stderr)
                )


if __name__ == "__main__":
    unittest.main()
