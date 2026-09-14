"""Guard the container topology the deployment depends on.

These assertions exist because of a specific outage shape: moomoo-mcp used to
run inside OpenD's network namespace (``network_mode: service:opend``) and
published port 8000 through it. Restarting the OpenD container then destroyed
the namespace moomoo-mcp was still living in — its loopback no longer reached
the new gateway, and the host's published port mapped into a new sandbox where
nothing was listening. Clients lost the MCP endpoint entirely, and the SDK's
reconnect retried forever against an address that could never answer again.

Nothing else in the suite would notice that regression, and CI builds the images
without ever starting the stack, so the compose file is checked here instead.
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
    """The stack's two containers must be independently restartable."""

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

    def _service(self, name: str) -> dict:
        self.assertIn(name, self.config["services"])
        return self.config["services"][name]

    def test_neither_service_shares_a_network_namespace(self):
        """The outage itself: a shared namespace dies with its owner."""
        for name, service in self.config["services"].items():
            self.assertNotIn(
                "network_mode",
                service,
                f"{name} must keep its own network namespace so restarting the "
                "other container cannot take its networking with it",
            )

    def test_both_services_share_the_trading_net_bridge(self):
        self.assertIn("trading-net", self.config.get("networks", {}))
        for name in ("opend", "moomoo-mcp"):
            self.assertIn("trading-net", self._service(name).get("networks", {}))

    def test_the_bridge_is_not_internal(self):
        """OpenD needs outbound access to reach Moomoo's servers."""
        self.assertNotEqual(
            self.config["networks"]["trading-net"].get("internal"), True
        )

    def test_the_mcp_endpoint_is_published_by_the_server_that_serves_it(self):
        """Published through opend, the endpoint vanished when opend restarted."""
        ports = self._service("moomoo-mcp").get("ports", [])
        published = {(p.get("host_ip"), str(p.get("published"))) for p in ports}

        self.assertEqual(published, {("127.0.0.1", "8000")})
        self.assertFalse(
            self._service("opend").get("ports"),
            "opend must publish nothing: it no longer fronts the MCP endpoint",
        )

    def test_the_gateway_port_is_never_published(self):
        """OpenD's API has no authentication of its own."""
        for name, service in self.config["services"].items():
            for port in service.get("ports", []):
                self.assertNotIn(
                    "11111",
                    (str(port.get("published")), str(port.get("target"))),
                    f"{name} must not expose the OpenD API beyond trading-net",
                )

    def test_the_mcp_server_reaches_the_gateway_by_service_name(self):
        """Loopback only ever worked because the namespace was shared."""
        environment = self._service("moomoo-mcp")["environment"]

        self.assertEqual(environment["MOOMOO_OPEND_HOST"], "opend")
        self.assertEqual(str(environment["MOOMOO_OPEND_PORT"]), "11111")

    def test_the_gateway_listens_beyond_loopback(self):
        """A gateway bound to 127.0.0.1 is unreachable from another namespace."""
        opend = self._service("opend")
        entrypoint = " ".join(opend.get("entrypoint", []))

        self.assertNotIn("-api_ip=127.0.0.1", entrypoint)
        self.assertIn("-api_ip=$${OPEND_API_IP:-0.0.0.0}", entrypoint)
        self.assertEqual(opend["environment"]["OPEND_API_IP"], "0.0.0.0")

    def test_compose_recreates_the_mcp_server_with_the_gateway(self):
        depends_on = self._service("moomoo-mcp").get("depends_on", {})

        self.assertIn("opend", depends_on)
        self.assertIs(depends_on["opend"].get("restart"), True)

    def test_the_production_overlay_keeps_the_same_topology(self):
        """The VPS runs the overlay, so its merge is what actually deploys."""
        for name in ("opend", "moomoo-mcp"):
            service = self.prod["services"][name]
            self.assertNotIn("network_mode", service)
            self.assertIn("trading-net", service.get("networks", {}))
            self.assertTrue(service.get("image"), f"{name} must run a built image")

        self.assertFalse(self.prod["services"]["opend"].get("ports"))
        self.assertEqual(
            [
                (p.get("host_ip"), str(p.get("published")))
                for p in self.prod["services"]["moomoo-mcp"].get("ports", [])
            ],
            [("127.0.0.1", "8000")],
        )


if __name__ == "__main__":
    unittest.main()
