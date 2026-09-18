"""Prove the verifier sends what Compose actually gives the container.

deploy_verify.py never interprets an env file: it reads MCP_AUTH_TOKEN from the
configuration `scripts/compose-prod.sh config --format json` resolves. The
other deployment tests stub Docker, so they cannot say what that configuration
is. These run the real Compose CLI against the real wrapper and compose files.

Three layers, each needing a little more of Docker:

- ConfigResolutionTest (the Compose CLI, no daemon): the helper reads the
  service's environment rather than an identically named variable, env files
  and the shell take precedence the way Compose says, and anything Compose
  refuses is a configuration error.
- DeployResolutionTest (the Compose CLI, no daemon): deploy.sh checks the
  configuration of the commit it checked out, with the .deploy.env it wrote.
- ContainerAgreementTest (a daemon): a disposable container is started by
  Compose for each env-file case, and the token in its environment is compared
  with the Authorization header the verifier sent it. No expected values are
  written down: whatever Compose resolves is, by definition, right, and the
  helper must agree with it or refuse. That container runs a stand-in endpoint
  (tests/fixtures/fake_mcp_server.py) with dummy values: no OpenD, broker
  account or ECR image.

The only production detail replaced is the Docker context: compose-prod.sh
names `rootless`, which exists on the VPS, so a shim drops that argument and
runs the local Docker instead. Everything after it is the real command line.

Skipped locally without Docker. In CI a missing prerequisite is a failure: the
compose-agreement job exists to run these, and a skip there would be a pass
that checked nothing.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import deploy_verify
from tests.test_deploy_scripts import REGISTRY, _env_without_git_vars

ROOT = Path(__file__).resolve().parents[1]
REAL_DOCKER = shutil.which("docker")
SERVICE = "moomoo-mcp"
# The base file's container_name, which is global rather than per-project.
CONTAINER_NAME = "moomoo-api-mcp"
PROJECT = "deploy-verify-agreement"
# A local tag the prod overlay's ${ECR_REGISTRY}/moomoo-api-mcp:${IMAGE_TAG}
# resolves to, so `up` starts the stand-in instead of pulling from ECR.
TEST_REGISTRY = "deploy-verify-test.invalid"
TEST_IMAGE_TAG = "agreement"
TEST_IMAGE = f"{TEST_REGISTRY}/moomoo-api-mcp:{TEST_IMAGE_TAG}"
# Variables a case may set, cleared from the inherited environment so a
# developer's own shell cannot decide the outcome.
CASE_VARIABLES = ("MCP_AUTH_TOKEN", "MCP_TOKEN_V2", "TOKEN_BASE")


def require(available: bool, reason: str) -> None:
    """Skip locally; fail in CI, where these tests are the point."""
    if available:
        return
    if os.environ.get("CI"):
        raise AssertionError(f"{reason}. CI must not skip the Compose agreement tests.")
    raise unittest.SkipTest(reason)


def compose_cli_available() -> bool:
    """The Compose CLI resolves configuration without a daemon."""
    if REAL_DOCKER is None:
        return False
    probe = subprocess.run([REAL_DOCKER, "compose", "version"], capture_output=True)
    return probe.returncode == 0


def write_context_shim(bin_dir: Path) -> None:
    """A docker that drops compose-prod.sh's `--context rootless`."""
    shim = bin_dir / "docker"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --context ] && [ "$2" = rootless ]; then shift 2; fi\n'
        f'exec "{REAL_DOCKER}" "$@"\n'
    )
    shim.chmod(0o755)


class ComposeCheckout:
    """The real wrapper, helper and compose files in a disposable directory."""

    def __init__(self, root: Path):
        self.repo = root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("compose-prod.sh", "deploy_verify.py"):
            shutil.copy2(ROOT / "scripts" / name, self.repo / "scripts" / name)
        for name in ("docker-compose.yml", "docker-compose.prod.yml"):
            shutil.copy2(ROOT / name, self.repo / name)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        write_context_shim(bin_dir)
        self.env = {
            name: value
            for name, value in os.environ.items()
            if name not in CASE_VARIABLES
        }
        self.env["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        # Never the directory name: that would be "repo" for every fixture.
        self.env["COMPOSE_PROJECT_NAME"] = PROJECT

    def write(self, env_file: bytes, deploy_env: bytes = b"") -> None:
        (self.repo / ".env").write_bytes(env_file)
        (self.repo / ".deploy.env").write_bytes(
            f"ECR_REGISTRY={TEST_REGISTRY}\nIMAGE_TAG={TEST_IMAGE_TAG}\n".encode()
            + deploy_env
        )

    def remap_token(self, mapping: str) -> None:
        """Point the service's MCP_AUTH_TOKEN at a different expression."""
        compose_file = self.repo / "docker-compose.yml"
        text = compose_file.read_text()
        original = "- MCP_AUTH_TOKEN=${MCP_AUTH_TOKEN:-}"
        if original not in text:
            raise AssertionError("docker-compose.yml no longer maps MCP_AUTH_TOKEN")
        compose_file.write_text(text.replace(original, f"- MCP_AUTH_TOKEN={mapping}"))

    def compose(self, *args: str, exported=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.repo / "scripts" / "compose-prod.sh"), *args],
            env={**self.env, **(exported or {})},
            capture_output=True,
            text=True,
            timeout=300,
        )

    def helper(self, *args: str, exported=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "deploy_verify.py"), *args],
            env={**self.env, **(exported or {})},
            capture_output=True,
            text=True,
            timeout=120,
        )

    def resolve(self, exported=None) -> str:
        """The helper's token, from this checkout's compose-prod.sh."""
        command = (str(self.repo / "scripts" / "compose-prod.sh"), "config")
        command += ("--format", "json")
        environment = {**self.env, **(exported or {})}
        with mock.patch.dict(os.environ, environment, clear=True):
            return deploy_verify.resolve_token(command)


class ConfigResolutionTest(unittest.TestCase):
    """What the helper reads is the service environment Compose resolves."""

    @classmethod
    def setUpClass(cls):
        require(compose_cli_available(), "the docker compose CLI is not installed")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.checkout = ComposeCheckout(Path(temp.name))

    def test_the_service_environment_is_read_not_the_variable(self):
        """A mapping that reads another variable is followed, not second-guessed."""
        self.checkout.remap_token("${MCP_TOKEN_V2:-}")
        self.checkout.write(
            b"MCP_AUTH_TOKEN=only-an-interpolation-input\n"
            b"MCP_TOKEN_V2=what-the-service-gets\n"
        )
        self.assertEqual(self.checkout.resolve(), "what-the-service-gets")

    def test_a_literal_in_the_compose_file_wins_over_the_env_file(self):
        self.checkout.remap_token("set-in-the-compose-file")
        self.checkout.write(b"MCP_AUTH_TOKEN=from-the-env-file\n")
        self.assertEqual(self.checkout.resolve(), "set-in-the-compose-file")

    def test_the_later_env_file_wins(self):
        """compose-prod.sh passes .env, then .deploy.env."""
        self.checkout.write(
            b"MCP_AUTH_TOKEN=from-dot-env\n", b"MCP_AUTH_TOKEN=from-deploy-env\n"
        )
        self.assertEqual(self.checkout.resolve(), "from-deploy-env")

    def test_an_exported_variable_wins_over_the_env_files(self):
        self.checkout.write(b"MCP_AUTH_TOKEN=from-dot-env\n")
        exported = {"MCP_AUTH_TOKEN": "from-the-shell"}
        self.assertEqual(self.checkout.resolve(exported), "from-the-shell")

    def test_an_explicitly_empty_export_disables_authentication(self):
        """Empty, not unset: the shell's empty value still takes precedence."""
        self.checkout.write(b"MCP_AUTH_TOKEN=from-dot-env\n")
        self.assertEqual(self.checkout.resolve({"MCP_AUTH_TOKEN": ""}), "")

    def test_an_unset_token_is_the_compose_files_empty_default(self):
        self.checkout.write(b"UNRELATED=1\n")
        self.assertEqual(self.checkout.resolve(), "")

    def test_what_compose_refuses_is_a_configuration_error(self):
        self.checkout.remap_token("${MCP_AUTH_TOKEN:?must be set}")
        self.checkout.write(b"UNRELATED=1\n")
        with self.assertRaises(deploy_verify.ConfigError) as caught:
            self.checkout.resolve()
        self.assertIn("Compose could not resolve", str(caught.exception))
        result = self.checkout.helper("check-config")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Configuration error", result.stderr)

    def test_a_missing_env_file_is_a_configuration_error(self):
        self.checkout.write(b"")
        (self.checkout.repo / ".env").unlink()
        with self.assertRaises(deploy_verify.ConfigError):
            self.checkout.resolve()

    def test_a_multiline_value_compose_accepts_is_refused_as_a_header(self):
        """Compose resolves it correctly; HTTP still cannot carry it."""
        self.checkout.write(b"UNRELATED=1\n")
        exported = {"MCP_AUTH_TOKEN": "first line\nsecond line"}
        rendered = self.checkout.compose(
            "config", "--format", "json", exported=exported
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        environment = json.loads(rendered.stdout)["services"][SERVICE]["environment"]
        self.assertEqual(environment["MCP_AUTH_TOKEN"], "first line\nsecond line")
        result = self.checkout.helper("check-config", exported=exported)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Authorization header", result.stderr)
        self.assertNotIn("second line", result.stderr)

    def test_the_resolved_configuration_is_never_printed(self):
        """The model carries every credential in the deployment, not just one."""
        self.checkout.write(
            b"MCP_AUTH_TOKEN=dummy-token-value\n"
            b"MOOMOO_TRADE_PASSWORD=dummy-trade-password\n"
        )
        result = self.checkout.helper("check-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        for secret in ("dummy-token-value", "dummy-trade-password"):
            self.assertNotIn(secret, result.stdout + result.stderr)


class DeployResolutionTest(unittest.TestCase):
    """deploy.sh checks the target commit's configuration, as it will run.

    Real Compose answers `config`; everything that would touch a registry or
    start a container is stubbed.
    """

    @classmethod
    def setUpClass(cls):
        require(compose_cli_available(), "the docker compose CLI is not installed")

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=self.repo,
            text=True,
            stderr=subprocess.PIPE,
            env=_env_without_git_vars(),
        )

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.repo = root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("deploy.sh", "compose-prod.sh", "deploy_verify.py"):
            shutil.copy2(ROOT / "scripts" / name, self.repo / "scripts" / name)
        for name in ("docker-compose.yml", "docker-compose.prod.yml", ".gitignore"):
            shutil.copy2(ROOT / name, self.repo / name)
        self.git("init", "-b", "main")
        self.git("config", "core.hooksPath", str(root / "no-hooks"))
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Deployment Test")
        self.git("add", ".")
        self.git("commit", "-m", "maps MCP_AUTH_TOKEN")
        self.old_commit = self.git("rev-parse", "HEAD").strip()
        # The target commit changes which variable the service's token reads.
        compose_file = self.repo / "docker-compose.yml"
        compose_file.write_text(
            compose_file.read_text().replace(
                "- MCP_AUTH_TOKEN=${MCP_AUTH_TOKEN:-}",
                "- MCP_AUTH_TOKEN=${MCP_TOKEN_V2:-}",
            )
        )
        self.git("commit", "-am", "maps MCP_TOKEN_V2 instead")
        self.target = self.git("rev-parse", "HEAD").strip()
        self.git("remote", "add", "origin", str(self.repo))
        self.git("checkout", "--quiet", "--detach", self.old_commit)

        (self.repo / ".env").write_text(
            "MCP_AUTH_TOKEN=token-for-the-old-mapping\n"
            "MCP_TOKEN_V2=token-for-the-new-mapping\n"
        )
        # The previous deployment's settings override .env's token. deploy.sh
        # rewrites the file, so none of it may survive into the target.
        (self.repo / ".deploy.env").write_text(
            f"ECR_REGISTRY={REGISTRY}\nIMAGE_TAG=previous\n"
            "MCP_AUTH_TOKEN=token-from-the-old-deploy-env\n"
        )

        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.log = root / "calls.jsonl"
        # Real Compose for `config`; a recorder for everything else.
        (bin_dir / "docker").write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['--context', 'rootless']:\n"
            "    args = args[2:]\n"
            f"with open({str(self.log)!r}, 'a') as log:\n"
            "    log.write(json.dumps(['docker', args]) + '\\n')\n"
            "if 'config' in args:\n"
            f"    os.execv({REAL_DOCKER!r}, [{REAL_DOCKER!r}, *args])\n"
        )
        (bin_dir / "aws").write_text("#!/bin/sh\necho sha256:test-digest\n")
        (bin_dir / "curl").write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "header = sys.stdin.read() if '@-' in sys.argv else None\n"
            f"with open({str(self.log)!r}, 'a') as log:\n"
            "    log.write(json.dumps(['curl-header', header]) + '\\n')\n"
            "result = {'protocolVersion': '2025-06-18', 'capabilities': {},\n"
            "          'serverInfo': {'name': 'stub', 'version': '0'}}\n"
            "body = {'jsonrpc': '2.0', 'id': 'deploy-verify', 'result': result}\n"
            "sys.stdout.write(json.dumps(body) + '\\n200\\napplication/json')\n"
        )
        for name in ("docker", "aws", "curl"):
            (bin_dir / name).chmod(0o755)
        self.env = {
            name: value
            for name, value in _env_without_git_vars().items()
            if name not in CASE_VARIABLES + ("ECR_REGISTRY", "IMAGE_TAG")
        }
        self.env.update(
            PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
            DEPLOY_VERIFY_TIMEOUT="0",
        )

    def test_the_fixture_discriminates(self):
        """Before the deploy, the old checkout resolves the old deploy's token —
        so the assertion below could fail."""
        command = (str(self.repo / "scripts" / "compose-prod.sh"), "config")
        with mock.patch.dict(os.environ, self.env, clear=True):
            token = deploy_verify.resolve_token((*command, "--format", "json"))
        self.assertEqual(token, "token-from-the-old-deploy-env")

    def test_the_probe_sends_the_target_commits_token(self):
        result = subprocess.run(
            [str(self.repo / "scripts" / "deploy.sh"), self.target],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Deploy verified", result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        headers = [header for name, header in calls if name == "curl-header"]
        self.assertEqual(headers, ["Authorization: Bearer token-for-the-new-mapping\n"])


def daemon_available() -> bool:
    if REAL_DOCKER is None:
        return False
    return subprocess.run([REAL_DOCKER, "info"], capture_output=True).returncode == 0


def port_8000_free() -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8000))
        except OSError:
            return False
    return True


# One per group of env-file syntax Compose documents. There is no expected value
# on purpose: the container's environment is the expected value.
AGREEMENT_CASES = {
    "ordinary": (b"MCP_AUTH_TOKEN=plain-dummy-token\n", b"", {}),
    "double quotes, spaces, inline comment": (
        b'MCP_AUTH_TOKEN = "two words"   # an inline comment\n',
        b"",
        {},
    ),
    "single quotes keep a dollar": (
        b"MCP_AUTH_TOKEN='literal $NOT_EXPANDED'\n",
        b"",
        {},
    ),
    "comments, blank lines, CRLF": (
        b"# a comment\r\n\r\nMCP_AUTH_TOKEN=crlf-dummy-token\r\n",
        b"",
        {},
    ),
    "= and : inside the value": (b"MCP_AUTH_TOKEN=abc=def:ghi==\n", b"", {}),
    "colon delimiter": (b"MCP_AUTH_TOKEN: colon-dummy-token\n", b"", {}),
    "escapes in double quotes": (b'MCP_AUTH_TOKEN="say \\"hi\\" \\\\o/"\n', b"", {}),
    "unbraced variable": (
        b"TOKEN_BASE=base\nMCP_AUTH_TOKEN=$TOKEN_BASE-suffix\n",
        b"",
        {},
    ),
    "braced variable": (
        b'TOKEN_BASE=base\nMCP_AUTH_TOKEN="${TOKEN_BASE}-suffix"\n',
        b"",
        {},
    ),
    "duplicate assignment": (
        b"MCP_AUTH_TOKEN=first-dummy\nMCP_AUTH_TOKEN=second-dummy\n",
        b"",
        {},
    ),
    "export prefix": (b"export MCP_AUTH_TOKEN=export-prefixed\n", b"", {}),
    "later env file": (
        b"MCP_AUTH_TOKEN=from-dot-env\n",
        b"MCP_AUTH_TOKEN=from-deploy-env\n",
        {},
    ),
    "exported variable": (
        b"MCP_AUTH_TOKEN=from-dot-env\n",
        b"",
        {"MCP_AUTH_TOKEN": "from-the-shell"},
    ),
    "exported empty variable": (
        b"MCP_AUTH_TOKEN=from-dot-env\n",
        b"",
        {"MCP_AUTH_TOKEN": ""},
    ),
    "empty in the file": (b"MCP_AUTH_TOKEN=\n", b"", {}),
    "multiline": (b'MCP_AUTH_TOKEN="first\\nsecond"\n', b"", {}),
    "unterminated quote": (b'MCP_AUTH_TOKEN="never closed\n', b"", {}),
}


class ContainerAgreementTest(unittest.TestCase):
    """What Compose gave the container = what the probe sent it."""

    @classmethod
    def setUpClass(cls):
        require(compose_cli_available(), "the docker compose CLI is not installed")
        require(daemon_available(), "no Docker daemon is reachable")
        require(port_8000_free(), "127.0.0.1:8000 is in use")
        existing = subprocess.run(
            [REAL_DOCKER or "docker", "inspect", CONTAINER_NAME], capture_output=True
        )
        require(existing.returncode != 0, f"a container named {CONTAINER_NAME} exists")
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        context = root / "image"
        context.mkdir()
        shutil.copy2(ROOT / "tests" / "fixtures" / "fake_mcp_server.py", context)
        (context / "Dockerfile").write_text(
            "FROM python:3.12-slim\n"
            "COPY fake_mcp_server.py /fake_mcp_server.py\n"
            'ENTRYPOINT ["python3", "-u", "/fake_mcp_server.py"]\n'
        )
        built = subprocess.run(
            # linux/amd64 because docker-compose.yml pins it: a stand-in built
            # for another platform does not match the service, and Compose
            # would try the (nonexistent) registry for it.
            [
                REAL_DOCKER or "docker",
                "build",
                "-q",
                "--platform",
                "linux/amd64",
                "-t",
                TEST_IMAGE,
                str(context),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if built.returncode != 0:
            cls.temp.cleanup()
            raise AssertionError(
                f"could not build the stand-in: {built.stderr[-2000:]}"
            )
        cls.checkout = ComposeCheckout(root)

    @classmethod
    def tearDownClass(cls):
        cls.checkout.write(b"")
        cls.checkout.compose("down", "-v", "--remove-orphans")
        subprocess.run(
            [REAL_DOCKER or "docker", "image", "rm", "-f", TEST_IMAGE],
            capture_output=True,
        )
        cls.temp.cleanup()

    def container_token(self) -> str | None:
        """MCP_AUTH_TOKEN as the running container actually has it."""
        container_id = self.checkout.compose("ps", "-q", SERVICE).stdout.strip()
        self.assertTrue(container_id, "the service is not running")
        inspected = subprocess.run(
            [REAL_DOCKER or "docker", "inspect", "--format", "{{json .Config.Env}}"]
            + [container_id],
            capture_output=True,
            text=True,
            check=True,
        )
        for entry in json.loads(inspected.stdout):
            name, _, value = entry.partition("=")
            if name == "MCP_AUTH_TOKEN":
                return value
        return None

    def received_headers(self) -> list:
        container_id = self.checkout.compose("ps", "-q", SERVICE).stdout.strip()
        record = subprocess.run(
            [REAL_DOCKER or "docker", "exec", container_id]
            + ["cat", "/tmp/authorization.jsonl"],
            capture_output=True,
            text=True,
        )
        return [
            json.loads(line)["authorization"] for line in record.stdout.splitlines()
        ]

    def test_the_probe_sends_what_the_container_received(self):
        for name, (env_file, deploy_env, exported) in AGREEMENT_CASES.items():
            with self.subTest(name):
                self.check_agreement(env_file, deploy_env, exported)

    def check_agreement(self, env_file, deploy_env, exported):
        checkout = self.checkout
        checkout.write(env_file, deploy_env)
        rendered = checkout.compose("config", "--quiet", exported=exported)
        if rendered.returncode != 0:
            # Compose refuses this syntax. Then nothing starts, and the helper
            # refuses too rather than falling back to a reading of its own.
            checked = checkout.helper("check-config", exported=exported)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn("Configuration error", checked.stderr)
            started = checkout.compose("up", "-d", exported=exported)
            self.assertNotEqual(started.returncode, 0)
            return

        started = checkout.compose(
            "up", "-d", "--force-recreate", "--remove-orphans", exported=exported
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        token = self.container_token()
        self.assertIsNotNone(token, "the container has no MCP_AUTH_TOKEN")
        assert token is not None

        checked = checkout.helper("check-config", exported=exported)
        try:
            deploy_verify.check_header_safe(token)
        except deploy_verify.ConfigError:
            # Compose resolved it and the container has it, but no header can
            # carry it: the helper must say so before anything is probed.
            self.assertEqual(checked.returncode, 1)
            self.assertIn("Authorization header", checked.stderr)
            return
        self.assertEqual(checked.returncode, 0, checked.stderr)

        verified = checkout.helper("verify", "--timeout", "60", exported=exported)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        received = self.received_headers()
        self.assertTrue(received, "the stand-in recorded no request")
        expected = f"Bearer {token}" if token else None
        self.assertEqual(received[-1], expected)
        if token:
            self.assertNotIn(token, verified.stdout + verified.stderr)


if __name__ == "__main__":
    unittest.main()
