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
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = "moomoo-mcp"
OPEND_DATA_PATH = "/home/opend/.com.moomoo.OpenD"

# Any value: the prod overlay only requires that these are set (`${VAR:?}`).
PROD_ENV = {
    "ECR_REGISTRY": "123456789012.dkr.ecr.ap-southeast-1.amazonaws.com",
    "IMAGE_TAG": "0000000",
}


def _compose_available() -> bool:
    """Whether the Compose CLI is usable. It needs no daemon to render config."""
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        cwd=ROOT,
    )
    return probe.returncode == 0


def _render(*overlays: str, env: dict[str, str] | None = None) -> dict:
    """Return the configuration Compose actually resolves for these files."""
    files: list[str] = []
    for overlay in overlays:
        files += ["-f", overlay]
    result = subprocess.run(
        # An empty env file, because Compose otherwise reads the developer's
        # own .env and these assertions would depend on an untracked file.
        [
            "docker",
            "compose",
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
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"docker compose config failed for {files}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


class ComposeTopologyTest(unittest.TestCase):
    """One container, serving one endpoint, with the gateway behind it."""

    @classmethod
    def setUpClass(cls):
        if not _compose_available():
            # Skipping in CI would defeat the point: this is the only check
            # covering the compose file, so an absent toolchain is a failure
            # there rather than a reason to pass quietly.
            if os.environ.get("CI"):
                raise AssertionError(
                    "docker compose is unavailable, so the compose topology is "
                    "unchecked. CI must not pass without it."
                )
            raise unittest.SkipTest("docker compose is not installed")
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

    def test_orphan_reaping_is_configured_as_well_as_implemented(self):
        self.assertTrue(
            self._service().get("init"),
            "docker-init backs up the supervisor's own reaping",
        )

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
            {"OPEND_BINARY", "OPEND_RESTART_WINDOW_SECONDS"},
            "the overlay may stand in for the gateway binary and hurry its "
            "restarts along, and nothing else",
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
