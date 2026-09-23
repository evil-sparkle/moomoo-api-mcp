"""Guard the container topology the deployment depends on.

These assertions exist because of two specific outage shapes.

The first: moomoo-mcp used to run inside OpenD's network namespace
(``network_mode: service:opend``) and published port 8000 through it. Restarting
the OpenD container then destroyed the namespace moomoo-mcp was still living in
— its loopback no longer reached the new gateway, and the host's published port
mapped into a new sandbox where nothing was listening. Clients lost the MCP
endpoint entirely, and the SDK's reconnect retried forever against an address
that could never answer again.

The second is not an outage but an exposure, and it is why the two containers
became one: OpenD's API has no authentication, so while the gateway answered on
``0.0.0.0:11111`` over a shared bridge, the only thing standing between it and
anything else on that bridge was the fact that nothing else had been attached
yet. It now listens on container loopback, where the process boundary does that
job instead.

Nothing else in the suite would notice either regression, and CI builds the
image without ever starting the stack, so the compose file is checked here.
``docker compose config`` is used rather than a YAML parse so the checks run
against Compose's own interpolation, overlay merge, and schema validation.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

SERVICE = "moomoo-mcp"
OPEND_DATA_PATH = "/home/opend/.com.moomoo.OpenD"

# Any value: the prod overlay only requires that these are set (`${VAR:?}`).
PROD_ENV = {
    "ECR_REGISTRY": "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com",
    "IMAGE_TAG": "0000000",
}


def _compose_command(env: dict[str, str]) -> list[str]:
    """Find Compose without loading the user's Docker configuration."""
    commands = []
    standalone = shutil.which("docker-compose")
    if standalone:
        commands.append([standalone])
    # Resolve the user plugin executable before isolating HOME/DOCKER_CONFIG.
    # Only the executable is accessed; config.json is never read or copied.
    config = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker")))
    plugin = config / "cli-plugins" / "docker-compose"
    if plugin.is_file() and os.access(plugin, os.X_OK):
        commands.append([str(plugin)])
    docker = shutil.which("docker")
    if docker:
        commands.append([docker, "compose"])
    for command in commands:
        probe = subprocess.run(
            [*command, "version", "--short"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
        )
        major = probe.stdout.strip().lstrip("v").split(".", 1)[0]
        if probe.returncode == 0 and major.isdecimal() and int(major) >= 2:
            return command
    message = "Docker Compose v2 or newer is unavailable in the isolated environment"
    if os.environ.get("CI"):
        raise AssertionError(message)
    raise unittest.SkipTest(message)


def _render(*overlays: str, env: dict[str, str] | None = None) -> dict:
    """Return the configuration Compose actually resolves for these files."""
    files: list[str] = []
    for overlay in overlays:
        files += ["-f", overlay]
    with tempfile.TemporaryDirectory(prefix="moomoo-compose-config-") as docker_config:
        isolated_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(ROOT),
            "DOCKER_CONFIG": docker_config,
            **(env or {}),
        }
        command = _compose_command(isolated_env)
        result = subprocess.run(
            # An empty env file and isolated HOME/DOCKER_CONFIG keep these
            # topology checks independent of private local configuration.
            [
                *command,
                "--env-file",
                os.devnull,
                *files,
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=isolated_env,
        )
    if result.returncode != 0:
        raise AssertionError(
            f"docker compose config failed for {files}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


@pytest.mark.parametrize("custom_config", [False, True])
def test_user_plugin_survives_config_isolation(tmp_path, monkeypatch, custom_config):
    user_home = tmp_path / "user"
    config = tmp_path / "custom" if custom_config else user_home / ".docker"
    plugin = config / "cli-plugins" / "docker-compose"
    plugin.parent.mkdir(parents=True)
    plugin.touch()
    plugin.chmod(0o700)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("DOCKER_CONFIG", raising=False)
    if custom_config:
        monkeypatch.setenv("DOCKER_CONFIG", str(config))
    monkeypatch.setenv("MOOMOO_TRADING_MARKET", "US")
    monkeypatch.setattr(
        shutil, "which", lambda name: "/bin/docker" if name == "docker" else None
    )
    with patch.object(subprocess, "run") as run:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "v2.40.0", ""),
            subprocess.CompletedProcess([], 0, '{"services": {}}', ""),
        ]
        assert _render("docker-compose.yml") == {"services": {}}
    probe, render = run.call_args_list
    assert probe.args[0] == [str(plugin), "version", "--short"]
    assert render.args[0][0] == str(plugin)
    assert probe.kwargs["env"] == render.kwargs["env"]
    assert render.kwargs["env"]["HOME"] != str(user_home)
    assert render.kwargs["env"]["DOCKER_CONFIG"] != str(config)
    assert "MOOMOO_TRADING_MARKET" not in render.kwargs["env"]
    assert render.args[0][1:3] == ["--env-file", os.devnull]


@pytest.mark.parametrize("standalone_version", [None, "1.29.2", "v2.40.0"])
def test_compose_selection_uses_isolated_probe(
    tmp_path, monkeypatch, standalone_version
):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/" + name)
    isolated = {"HOME": str(tmp_path), "DOCKER_CONFIG": str(tmp_path)}
    first = subprocess.CompletedProcess(
        [], 0 if standalone_version else 1, standalone_version or "", ""
    )
    with patch.object(subprocess, "run") as run:
        run.side_effect = [first, subprocess.CompletedProcess([], 0, "2.40.0", "")]
        command = _compose_command(isolated)
    assert command == (
        ["/bin/docker-compose"]
        if standalone_version == "v2.40.0"
        else ["/bin/docker", "compose"]
    )
    assert all(call.kwargs["env"] == isolated for call in run.call_args_list)


@pytest.mark.parametrize("ci", [False, True])
def test_missing_compose_fails_in_ci_and_skips_locally(tmp_path, monkeypatch, ci):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.delenv("CI", raising=False)
    if ci:
        monkeypatch.setenv("CI", "true")
    with pytest.raises(AssertionError if ci else unittest.SkipTest):
        _compose_command({"HOME": str(tmp_path), "DOCKER_CONFIG": str(tmp_path)})


class ComposeTopologyTest(unittest.TestCase):
    """One container, serving one endpoint, with the gateway behind it."""

    @classmethod
    def setUpClass(cls):
        cls.config = _render("docker-compose.yml")
        cls.prod = _render(
            "docker-compose.yml", "docker-compose.prod.yml", env=PROD_ENV
        )

    def _service(self, config: dict | None = None) -> dict:
        config = self.config if config is None else config
        self.assertIn(SERVICE, config["services"])
        return config["services"][SERVICE]

    def test_the_stack_is_one_service(self):
        """Two services meant two lifecycles to keep in step. There is now one."""
        self.assertEqual(list(self.config["services"]), [SERVICE])

    def test_no_service_shares_a_network_namespace(self):
        """The original outage: a borrowed namespace dies with its owner."""
        for name, service in self.config["services"].items():
            self.assertNotIn(
                "network_mode",
                service,
                f"{name} must not take its networking from another container",
            )

    def test_the_gateway_port_is_never_published(self):
        """OpenD's API has no authentication of its own."""
        for name, service in self.config["services"].items():
            for port in service.get("ports", []):
                self.assertNotIn(
                    "11111",
                    (str(port.get("published")), str(port.get("target"))),
                    f"{name} must not expose the OpenD API outside the container",
                )

    def test_the_mcp_endpoint_is_published_to_host_loopback_only(self):
        ports = self._service().get("ports", [])
        published = {(p.get("host_ip"), str(p.get("published"))) for p in ports}

        self.assertEqual(published, {("127.0.0.1", "8000")})

    def test_the_mcp_server_reaches_the_gateway_over_container_loopback(self):
        """A service name here would mean the gateway is listening on a bridge."""
        environment = self._service()["environment"]

        self.assertEqual(environment["MOOMOO_OPEND_HOST"], "127.0.0.1")
        self.assertEqual(str(environment["MOOMOO_OPEND_PORT"]), "11111")

    def test_trade_market_default_and_operator_override_reach_both_compose_files(self):
        self.assertEqual(
            self._service()["environment"]["MOOMOO_TRADING_MARKET"], "NONE"
        )
        self.assertEqual(
            self._service(self.prod)["environment"]["MOOMOO_TRADING_MARKET"],
            "NONE",
        )

        base_hk = _render("docker-compose.yml", env={"MOOMOO_TRADING_MARKET": "HK"})
        production_hk = _render(
            "docker-compose.yml",
            "docker-compose.prod.yml",
            env={**PROD_ENV, "MOOMOO_TRADING_MARKET": "HK"},
        )
        self.assertEqual(
            self._service(base_hk)["environment"]["MOOMOO_TRADING_MARKET"], "HK"
        )
        self.assertEqual(
            self._service(production_hk)["environment"]["MOOMOO_TRADING_MARKET"],
            "HK",
        )

    def test_the_listener_is_not_an_operator_setting(self):
        """`OPEND_API_IP` let a deployment widen an unauthenticated API by
        editing .env. The supervisor pins it, and nothing here may re-open it."""
        for name, service in self.config["services"].items():
            self.assertNotIn(
                "OPEND_API_IP",
                service.get("environment", {}),
                f"{name} must not make the gateway's listener configurable",
            )

    def test_the_container_is_replaced_when_the_supervisor_gives_up(self):
        """The recovery policy's other half lives out here: the supervisor exits
        non-zero when a child cannot be recovered, and this is what acts on it."""
        service = self._service()

        self.assertEqual(service.get("restart"), "unless-stopped")

    def test_nothing_is_inserted_in_front_of_the_supervisor(self):
        """`init: true` would make docker-init PID 1 and the supervisor PID 2.

        That is a coherent design, but it is not this one: the supervisor
        installs its own signal handlers because PID 1 has no default
        dispositions, and reaps orphans itself. The spec and its tests describe
        the supervisor as PID 1, so the compose file has to actually give it
        that job.
        """
        self.assertNotIn("init", self._service())

    def test_the_device_authorization_volume_keeps_its_path(self):
        """A moved mount point reads as an empty directory, and OpenD would ask
        for a fresh device authorization over SMS."""
        targets = {v.get("target") for v in self._service().get("volumes", [])}

        self.assertIn(OPEND_DATA_PATH, targets)

    def test_the_interactive_login_can_still_reach_a_tty(self):
        service = self._service()

        self.assertTrue(service.get("stdin_open"))
        self.assertTrue(service.get("tty"))

    def test_the_smoke_overlay_changes_only_the_gateway_binary(self):
        """The smoke test has to exercise the deployed stack, not its own.

        docker-compose.smoke.yml swaps the OpenD binary for a stand-in the CI
        runner can start. If it ever moved a port, a volume, the restart policy
        or the supervisor's own settings too, the smoke test would be proving
        something about a stack nobody deploys.
        """
        smoke = _render("docker-compose.yml", "docker-compose.smoke.yml")

        deployed = self._service()
        stubbed = self._service(smoke)
        for key in ("ports", "volumes", "restart", "init", "build"):
            if key == "volumes":
                # The overlay mounts the stub in addition; the deployed mounts
                # must all survive it.
                deployed_targets = {v.get("target") for v in deployed.get(key, [])}
                stubbed_targets = {v.get("target") for v in stubbed.get(key, [])}
                self.assertTrue(deployed_targets <= stubbed_targets, "a mount was lost")
                continue
            self.assertEqual(deployed.get(key), stubbed.get(key), f"{key} was moved")

        changed = {
            name
            for name in set(deployed["environment"]) | set(stubbed["environment"])
            if deployed["environment"].get(name) != stubbed["environment"].get(name)
        }
        self.assertEqual(
            changed,
            {
                "OPEND_BINARY",
                "OPEND_RESTART_WINDOW_SECONDS",
                "MOOMOO_LOGIN_ACCOUNT",
                "MOOMOO_LOGIN_PWD_MD5",
            },
            "the overlay may stand in for the gateway binary, hurry its restarts "
            "along and hand it a fake login to start with, and nothing else",
        )

    def test_the_production_overlay_keeps_the_same_topology(self):
        """The VPS runs the overlay, so its merge is what actually deploys."""
        service = self._service(self.prod)

        self.assertNotIn("network_mode", service)
        self.assertTrue(service.get("image"), "the deployment must run a built image")
        self.assertEqual(service.get("restart"), "unless-stopped")
        self.assertEqual(
            [
                (p.get("host_ip"), str(p.get("published")))
                for p in service.get("ports", [])
            ],
            [("127.0.0.1", "8000")],
        )
        self.assertIn(
            OPEND_DATA_PATH, {v.get("target") for v in service.get("volumes", [])}
        )


if __name__ == "__main__":
    unittest.main()
