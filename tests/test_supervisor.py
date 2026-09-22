"""The recovery policy, exercised against real child processes.

Not against OpenD and not against the MCP server: what needs proving here is
that the *policy* holds — which exit restarts what, which exit takes the
container down, and that a stop signal actually stops both — and stand-in
children make each of those a deterministic test instead of an integration run.
scripts/smoke-test.sh covers the same policy against the real image.

These tests fork, and the supervisor reaps with ``waitpid(-1)`` because PID 1
must also collect orphans. That means a test here will happily reap any other
child of the pytest process, so nothing in this file may spawn subprocesses
outside the supervisor's knowledge except where a test does so deliberately.
"""

import ast
import os
import signal
import sys
import time
from pathlib import Path

import pytest

import moomoo_mcp.supervisor as supervisor_module
from moomoo_mcp.supervisor import (
    EXIT_GATEWAY_UNRECOVERABLE,
    EXIT_OK,
    EXIT_SERVER_EXITED,
    GATEWAY,
    SERVER,
    ChildSpec,
    GatewayLoginError,
    Supervisor,
    SupervisorConfigError,
    build_supervisor,
    gateway_spec,
    has_remembered_token,
    redacted,
    server_spec,
)

STAY = "import time\ntime.sleep(3600)\n"
QUIT = "import sys\nsys.exit(3)\n"
STUBBORN = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(3600)\n"
)


def child(tmp_path: Path, name: str, body: str) -> ChildSpec:
    """A stand-in process, run by this interpreter."""
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    return ChildSpec(name=name, argv=[sys.executable, str(path)])


def counting_child(tmp_path: Path, name: str, marker: Path) -> ChildSpec:
    """A stand-in that records each start and exits immediately."""
    return child(
        tmp_path,
        name,
        f"open({str(marker)!r}, 'a').write('start\\n')\nraise SystemExit(1)\n",
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def supervisors():
    """Hands out supervisors and guarantees their children are not left behind."""
    made: list[Supervisor] = []

    def make(gateway: ChildSpec | None, server: ChildSpec, **kwargs) -> Supervisor:
        kwargs.setdefault("gateway_restart_backoff", 0.0)
        kwargs.setdefault("poll_interval", 0.01)
        kwargs.setdefault("stop_timeout", 2.0)
        supervisor = Supervisor(gateway, server, **kwargs)
        made.append(supervisor)
        supervisor.start()
        return supervisor

    yield make
    for supervisor in made:
        supervisor.shutdown()


def drive(supervisor: Supervisor, until=None, timeout: float = 10.0) -> int | None:
    """Tick until the run ends or ``until`` holds. Returns the exit code, if any."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = supervisor.tick()
        if code is not None:
            return code
        if until is not None and until():
            return None
        time.sleep(0.01)
    raise AssertionError("the supervisor neither finished nor reached the condition")


class TestGatewayFailure:
    """A dead gateway is the routine case, and must cost a client nothing."""

    def test_gateway_exit_restarts_it_in_place_and_leaves_the_server_alone(
        self, tmp_path, supervisors
    ):
        marker = tmp_path / "starts"
        supervisor = supervisors(
            counting_child(tmp_path, GATEWAY, marker),
            child(tmp_path, SERVER, STAY),
            max_gateway_restarts=10,
        )
        server_pid = supervisor._pids[SERVER]

        code = drive(
            supervisor,
            until=lambda: marker.exists() and marker.read_text().count("start") >= 3,
        )

        assert code is None, "a gateway that can be restarted must not end the run"
        assert supervisor._pids[SERVER] == server_pid, (
            "restarting the gateway must not touch the server: that is the whole "
            "reason the policy is asymmetric"
        )
        assert alive(server_pid)

    def test_repeated_gateway_failure_stops_the_server_and_exits_nonzero(
        self, tmp_path, supervisors
    ):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, QUIT),
            child(tmp_path, SERVER, STAY),
            max_gateway_restarts=2,
            gateway_restart_window=60.0,
        )
        server_pid = supervisor._pids[SERVER]

        code = drive(supervisor)

        assert code == EXIT_GATEWAY_UNRECOVERABLE
        assert code != 0, "Docker must see a failure, not a clean exit"
        assert not alive(server_pid), (
            "giving up on the gateway must take the server down with it, or the "
            "container stays up serving a gateway that is not there"
        )

    def test_a_gateway_that_cannot_exec_is_a_gateway_failure_not_a_server_one(
        self, tmp_path, supervisors
    ):
        supervisor = supervisors(
            ChildSpec(name=GATEWAY, argv=["/nonexistent/OpenD"]),
            child(tmp_path, SERVER, STAY),
            max_gateway_restarts=10,
        )
        server_pid = supervisor._pids[SERVER]

        code = drive(supervisor, until=lambda: len(supervisor._gateway_restarts) >= 2)

        assert code is None
        assert alive(server_pid)


class TestServerFailure:
    """The server is not restarted in place: there is nothing to preserve."""

    def test_server_exit_stops_the_gateway_and_exits_nonzero(
        self, tmp_path, supervisors
    ):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STAY),
            child(tmp_path, SERVER, QUIT),
        )
        gateway_pid = supervisor._pids[GATEWAY]

        code = drive(supervisor)

        assert code == EXIT_SERVER_EXITED
        assert code != 0
        assert not alive(gateway_pid)


class TestStopping:
    def test_a_stop_signal_terminates_both_and_exits_zero(self, tmp_path, supervisors):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STAY),
            child(tmp_path, SERVER, STAY),
        )
        pids = dict(supervisor._pids)

        supervisor.request_stop(signal.SIGTERM)
        code = supervisor.tick()

        assert code == EXIT_OK, "an operator stopping the container is not a failure"
        for name, pid in pids.items():
            assert not alive(pid), f"{name} was left running"

    def test_the_installed_handler_asks_for_a_stop(self, tmp_path, supervisors):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STAY), child(tmp_path, SERVER, STAY)
        )

        supervisor._on_stop_signal(signal.SIGTERM, None)

        assert supervisor.tick() == EXIT_OK

    def test_a_child_ignoring_sigterm_is_killed_within_the_bound(
        self, tmp_path, supervisors
    ):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STUBBORN),
            child(tmp_path, SERVER, STAY),
            stop_timeout=0.3,
        )
        gateway_pid = supervisor._pids[GATEWAY]

        started = time.monotonic()
        supervisor.request_stop()
        code = supervisor.tick()
        elapsed = time.monotonic() - started

        assert code == EXIT_OK
        assert not alive(gateway_pid), "SIGTERM was ignored and never escalated"
        assert elapsed < 5.0, f"shutdown was not bounded: took {elapsed:.1f}s"


class TestReaping:
    def test_an_orphan_is_reaped_without_ending_the_run(self, tmp_path, supervisors):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STAY), child(tmp_path, SERVER, STAY)
        )

        # Stands in for a process reparented onto PID 1: this supervisor never
        # started it, so it must be collected and otherwise ignored.
        orphan = os.fork()
        if orphan == 0:  # pragma: no cover - the child never returns
            os._exit(0)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            assert supervisor.tick() is None, "an orphan must not end the run"
            try:
                os.waitpid(orphan, os.WNOHANG)
            except ChildProcessError:
                break  # the supervisor got there first, which is the point
            time.sleep(0.01)
        else:
            raise AssertionError("the orphan was never reaped")

        assert set(supervisor._pids) == {GATEWAY, SERVER}


class TestDegradedIsNotAFailure:
    """A gateway that is up but not logged in is `degraded`, not dead."""

    def test_a_running_gateway_is_never_restarted(self, tmp_path, supervisors):
        supervisor = supervisors(
            child(tmp_path, GATEWAY, STAY), child(tmp_path, SERVER, STAY)
        )
        pids = dict(supervisor._pids)

        for _ in range(20):
            assert supervisor.tick() is None
            time.sleep(0.01)

        assert supervisor._pids == pids
        assert supervisor._gateway_restarts == [], (
            "nothing but a process exit may trigger a restart"
        )

    def test_the_policy_cannot_consult_health(self):
        """Health is a client-facing signal. Acting on it would restart the
        gateway during the ~30s it legitimately needs to log back in, so the
        supervisor is kept without any route to it: no probe, and no import of
        the service layer that could grow into one."""
        tree = ast.parse(Path(supervisor_module.__file__).read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert not [name for name in imported if name.startswith("moomoo_mcp")], (
            "the supervisor imports application code, so a health probe could "
            "grow into the policy"
        )


class TestCredentialsStayOutOfLogs:
    """MD5 of six digits is obfuscation, not protection — and logs travel."""

    def test_the_pin_hash_is_never_rendered(self):
        line = redacted(
            [
                "/opt/moomooOpenD/OpenD",
                "-api_ip=127.0.0.1",
                "-login_account=12345678",
                "-login_pwd_md5=d41d8cd98f00b204e9800998ecf8427e",
            ]
        )

        assert "d41d8cd98f00b204e9800998ecf8427e" not in line
        assert "12345678" not in line, "the account number is an identifier too"
        assert "-login_pwd_md5=<redacted>" in line
        assert "-api_ip=127.0.0.1" in line, "the useful part must survive"

    def test_the_spawn_log_uses_it(self, tmp_path, supervisors, caplog):
        secret = "cafebabecafebabecafebabecafebabe"
        with caplog.at_level("INFO"):
            supervisors(
                ChildSpec(
                    name=GATEWAY,
                    argv=[sys.executable, "-c", "pass", f"-login_pwd_md5={secret}"],
                ),
                child(tmp_path, SERVER, STAY),
                max_gateway_restarts=0,
            )

        assert secret not in caplog.text


class TestAnUnstartableGatewayIsNotFatal:
    """A container that exits here takes check_health with it, and an operator
    asking why gets nothing. The two-container stack kept the server up."""

    def test_a_supervisor_can_run_without_a_gateway(self, tmp_path, supervisors):
        supervisor = supervisors(None, child(tmp_path, SERVER, STAY))

        for _ in range(10):
            assert supervisor.tick() is None
            time.sleep(0.01)

        assert GATEWAY not in supervisor._pids
        assert alive(supervisor._pids[SERVER])

    def test_no_usable_login_still_builds_a_running_supervisor(self):
        supervisor = build_supervisor(
            {"MOOMOO_LOGIN_ACCOUNT": "", "HOME": "/nonexistent"}
        )

        assert supervisor.gateway is None
        assert supervisor.server is not None


class TestTunablesAreValidated:
    """Silently running under a policy nobody chose is the quiet failure."""

    @pytest.mark.parametrize(
        "value", ["nan", "inf", "-inf", "-10", "0", "not-a-number"]
    )
    def test_a_bad_duration_is_refused(self, value):
        with pytest.raises(SupervisorConfigError) as excinfo:
            build_supervisor(
                {
                    "MOOMOO_LOGIN_ACCOUNT": "",
                    "SUPERVISOR_STOP_TIMEOUT_SECONDS": value,
                }
            )

        assert "SUPERVISOR_STOP_TIMEOUT_SECONDS" in str(excinfo.value)

    @pytest.mark.parametrize("value", ["-5", "1.5", "nan", "many"])
    def test_a_bad_restart_count_is_refused(self, value):
        with pytest.raises(SupervisorConfigError) as excinfo:
            build_supervisor({"MOOMOO_LOGIN_ACCOUNT": "", "OPEND_MAX_RESTARTS": value})

        assert "OPEND_MAX_RESTARTS" in str(excinfo.value)

    def test_zero_restarts_is_a_real_choice(self):
        supervisor = build_supervisor(
            {"MOOMOO_LOGIN_ACCOUNT": "", "OPEND_MAX_RESTARTS": "0"}
        )

        assert supervisor.max_gateway_restarts == 0

    def test_blank_means_the_default(self):
        supervisor = build_supervisor(
            {
                "MOOMOO_LOGIN_ACCOUNT": "",
                "OPEND_MAX_RESTARTS": "",
                "OPEND_RESTART_WINDOW_SECONDS": "  ",
            }
        )

        assert supervisor.max_gateway_restarts == 5
        assert supervisor.gateway_restart_window == 300.0


class TestGatewayCommandLine:
    """The branches that used to live in docker-compose.yml's entrypoint."""

    def env(self, **overrides) -> dict[str, str]:
        base = {
            "OPEND_BINARY": "/opt/moomooOpenD/OpenD",
            "HOME": "/nonexistent",
            "MOOMOO_LOGIN_ACCOUNT": "",
            "MOOMOO_LOGIN_PWD_MD5": "",
            "MOOMOO_LOGIN_BY_REMEMBER": "1",
            "MOOMOO_LOGIN_REGION": "sg",
            "OPEND_INTERACTIVE": "0",
        }
        base.update(overrides)
        return base

    def test_the_listener_is_pinned_to_loopback(self):
        spec = gateway_spec(self.env(OPEND_INTERACTIVE="1"))

        assert "-api_ip=127.0.0.1" in spec.argv
        assert not any("0.0.0.0" in arg for arg in spec.argv), (
            "the gateway API has no authentication; it must never be widened"
        )

    def test_it_runs_from_the_binary_directory(self):
        spec = gateway_spec(self.env(OPEND_INTERACTIVE="1"))

        assert spec.cwd == "/opt/moomooOpenD"

    def test_common_flags_are_preserved(self):
        spec = gateway_spec(self.env(OPEND_INTERACTIVE="1", MOOMOO_LOGIN_REGION="hk"))

        assert "-no_monitor=1" in spec.argv
        assert "-lang=en" in spec.argv
        assert "-login_region=hk" in spec.argv

    def test_interactive_without_an_account_still_starts(self):
        spec = gateway_spec(self.env(OPEND_INTERACTIVE="1"))

        assert not any(arg.startswith("-login_account") for arg in spec.argv)

    def test_interactive_with_an_account_passes_it(self):
        spec = gateway_spec(
            self.env(OPEND_INTERACTIVE="1", MOOMOO_LOGIN_ACCOUNT="12345678")
        )

        assert "-login_account=12345678" in spec.argv

    def test_a_password_hash_is_used_when_present(self):
        spec = gateway_spec(
            self.env(MOOMOO_LOGIN_ACCOUNT="12345678", MOOMOO_LOGIN_PWD_MD5="deadbeef")
        )

        assert "-login_account=12345678" in spec.argv
        assert "-login_pwd_md5=deadbeef" in spec.argv

    def test_a_remembered_token_is_used_when_one_exists(self, tmp_path):
        (tmp_path / ".com.moomoo.OpenD" / "F3CNN" / "UserAccMap").mkdir(parents=True)

        spec = gateway_spec(
            self.env(MOOMOO_LOGIN_ACCOUNT="12345678", HOME=str(tmp_path))
        )

        assert "-login_by_remember=1" in spec.argv

    def test_an_account_without_any_usable_login_refuses_to_start(self):
        with pytest.raises(GatewayLoginError) as excinfo:
            gateway_spec(self.env(MOOMOO_LOGIN_ACCOUNT="12345678"))

        assert "12345678" in str(excinfo.value)
        assert "OPEND_INTERACTIVE=1" in str(excinfo.value)

    def test_no_account_refuses_to_start(self):
        with pytest.raises(GatewayLoginError) as excinfo:
            gateway_spec(self.env())

        assert "MOOMOO_LOGIN_ACCOUNT" in str(excinfo.value)

    def test_remembering_is_skipped_when_the_operator_turns_it_off(self, tmp_path):
        (tmp_path / ".com.moomoo.OpenD" / "F3CNN" / "UserAccMap").mkdir(parents=True)

        with pytest.raises(GatewayLoginError):
            gateway_spec(
                self.env(
                    MOOMOO_LOGIN_ACCOUNT="12345678",
                    HOME=str(tmp_path),
                    MOOMOO_LOGIN_BY_REMEMBER="0",
                )
            )


class TestRememberedToken:
    def test_an_empty_home_has_no_token(self, tmp_path):
        assert has_remembered_token(str(tmp_path)) is False

    def test_a_user_account_map_counts(self, tmp_path):
        (tmp_path / ".com.moomoo.OpenD" / "F3CNN" / "UserAccMap").mkdir(parents=True)

        assert has_remembered_token(str(tmp_path)) is True

    def test_an_empty_auth_list_does_not_count(self, tmp_path):
        (tmp_path / ".com.moomoo.OpenD" / "F3CNN" / "ftnet" / "auth_acc_list").mkdir(
            parents=True
        )

        assert has_remembered_token(str(tmp_path)) is False

    def test_a_populated_auth_list_counts(self, tmp_path):
        auth = tmp_path / ".com.moomoo.OpenD" / "F3CNN" / "ftnet" / "auth_acc_list"
        auth.mkdir(parents=True)
        (auth / "12345678").write_text("")

        assert has_remembered_token(str(tmp_path)) is True

    # The probe also reaches a hardcoded /root, which the container's `opend`
    # user may not stat. Python raises PermissionError there through 3.13 and
    # answers False from 3.14 on, so on the image's 3.12 an unreadable
    # directory crashed the supervisor instead of reading as "no account".
    @pytest.mark.skipif(
        os.geteuid() == 0, reason="root can stat a directory whatever its mode"
    )
    def test_an_unreadable_directory_does_not_count(self, tmp_path):
        data = tmp_path / ".com.moomoo.OpenD"
        (data / "F3CNN" / "UserAccMap").mkdir(parents=True)
        data.chmod(0o000)

        try:
            assert has_remembered_token(str(tmp_path)) is False
        finally:
            data.chmod(0o700)


class TestServerCommandLine:
    def test_the_server_runs_its_console_script(self):
        spec = server_spec({"MCP_SERVER_BINARY": "/app/.venv/bin/moomoo-api-mcp"})

        assert spec.argv == ["/app/.venv/bin/moomoo-api-mcp"]

    def test_it_defaults_to_the_script_beside_this_interpreter(self):
        spec = server_spec({})

        assert spec.argv == [str(Path(sys.executable).with_name("moomoo-api-mcp"))]

    def test_the_server_is_never_launched_as_a_module(self):
        """`python -m moomoo_mcp.server` loads server.py a second time as
        __main__, with its own FastMCP instance, while the tool modules it
        imports register against the one under its real name. The served
        instance then has no tools — and nothing about it looks broken: the
        endpoint answers, sessions open, and `tools/list` returns `[]`.

        CI's smoke test caught this in the container. Here so it cannot come
        back as a "tidier" way to invoke the server.
        """
        assert "-m" not in server_spec({}).argv
