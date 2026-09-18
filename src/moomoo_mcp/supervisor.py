"""Own both processes this container needs, under one explicit recovery policy.

The gateway and the MCP server share a container so that OpenD's API — which
has no authentication of its own — listens on loopback and nowhere else. That
packaging creates a problem it does not solve: Docker's restart policy reacts
to *the container* exiting, so a child process that dies inside a container
whose PID 1 keeps running is invisible to it. Backgrounding OpenD and exec'ing
the server would produce exactly that: a live container serving a gateway that
is no longer there.

So this module is PID 1, and the policy is deliberate rather than implied:

    gateway exits              restart it in place; the server never notices
    gateway keeps failing      stop the server, exit non-zero, let Docker
                               replace the whole container
    server exits               stop the gateway, exit non-zero, same
    gateway up but not logged  do nothing; that is `degraded`, not a failure
    SIGTERM / SIGINT           forward to both, wait, then SIGKILL, exit 0

The asymmetry is the point. A gateway restart costs a connected client nothing
— the SDK reconnects underneath it and replays its subscriptions — while the
gateway needs ~30s to log back in to Moomoo. Cycling the container on every
gateway hiccup would spend a client-visible outage to buy nothing. The server,
by contrast, is stateless over HTTP and holds no session worth preserving, so
there is nothing to gain by restarting it in place.

Two things this deliberately does not do. It never reads health: a gateway that
is running but cannot reach the broker is reported `degraded` by ``check_health``
and left alone, because restarting it would not shorten the outage. And it never
waits for the gateway before starting the server — the MCP lifespan runs per
request, so a server that has not been called has not dialled the gateway and
should still answer.
"""

import contextlib
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

GATEWAY = "opend"
SERVER = "moomoo-mcp"

DEFAULT_OPEND_BINARY = "/opt/moomooOpenD/OpenD"
DEFAULT_OPEND_HOME = "/home/opend"

# Explicit, not inherited. OpenD's default is loopback, but that default has
# never been verified against a real build in this repository any more than
# `-api_ip=0.0.0.0` was, and this is the boundary keeping an unauthenticated
# trading API off every network. Asking for the value we want leaves only one
# way to be wrong instead of two.
GATEWAY_LISTEN_IP = "127.0.0.1"

# Exit codes. Docker's `unless-stopped` restarts regardless of the code, so
# these exist to tell a human reading `docker inspect` which half gave up.
EXIT_OK = 0
EXIT_SERVER_EXITED = 1
EXIT_GATEWAY_UNRECOVERABLE = 2
EXIT_CONFIG = 3


class GatewayLoginError(Exception):
    """No usable way to log the gateway in, so starting it would only hang."""


@dataclass(frozen=True)
class ChildSpec:
    """A process to run, and where to run it."""

    name: str
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def has_remembered_token(home: str = DEFAULT_OPEND_HOME) -> bool:
    """Whether OpenD has state a `-login_by_remember=1` start could use.

    Mirrors the check the compose entrypoint used to make in shell. Getting it
    wrong in the permissive direction is the expensive mistake: OpenD would
    start, fail to log in, and sit there looking healthy.
    """
    data = Path(home) / ".com.moomoo.OpenD"
    if (data / "F3CNN" / "UserAccMap").is_dir():
        return True
    auth_list = data / "F3CNN" / "ftnet" / "auth_acc_list"
    try:
        if any(auth_list.iterdir()):
            return True
    except OSError:
        pass
    # Left from an older image that ran the gateway as root. Still worth
    # honouring: the alternative is demanding an SMS code for nothing.
    return Path("/root/.com.moomoo.OpenD/F3CNN/UserAccMap").is_dir()


def gateway_spec(environ: dict[str, str] | None = None) -> ChildSpec:
    """Build OpenD's command line, or refuse to start it.

    This was shell inside docker-compose.yml, which is the only reason it was
    ever written in shell. The branches are unchanged, minus `-api_ip`, which
    is no longer an operator's to widen.

    Raises:
        GatewayLoginError: if no branch yields a login that can actually
            complete, rather than starting OpenD to prompt at a stdin nobody
            is attached to.
    """
    env = dict(os.environ if environ is None else environ)
    binary = env.get("OPEND_BINARY", DEFAULT_OPEND_BINARY)
    home = env.get("HOME", DEFAULT_OPEND_HOME)
    account = env.get("MOOMOO_LOGIN_ACCOUNT", "").strip()
    pwd_md5 = env.get("MOOMOO_LOGIN_PWD_MD5", "").strip()
    region = env.get("MOOMOO_LOGIN_REGION", "sg").strip() or "sg"
    interactive = env.get("OPEND_INTERACTIVE", "0").strip() == "1"
    by_remember = env.get("MOOMOO_LOGIN_BY_REMEMBER", "1").strip() == "1"

    argv = [
        binary,
        f"-api_ip={GATEWAY_LISTEN_IP}",
        "-no_monitor=1",
        "-lang=en",
        f"-login_region={region}",
    ]

    def spec(*extra: str) -> ChildSpec:
        return ChildSpec(
            name=GATEWAY, argv=[*argv, *extra], cwd=str(Path(binary).parent)
        )

    if interactive:
        # The one-time device login. OpenD prompts on stdin and a human answers,
        # so an absent account is fine here and only here.
        return spec(f"-login_account={account}") if account else spec()
    if pwd_md5:
        return spec(f"-login_account={account}", f"-login_pwd_md5={pwd_md5}")
    if by_remember and account and has_remembered_token(home):
        return spec(f"-login_account={account}", "-login_by_remember=1")
    if account:
        raise GatewayLoginError(
            f"No usable headless login for {account}: set MOOMOO_LOGIN_BY_REMEMBER=1 "
            "and perform the one-time interactive login so OpenD has a remembered "
            "token, or supply MOOMOO_LOGIN_PWD_MD5. One-time login: "
            "docker compose run --rm -it -e OPEND_INTERACTIVE=1 moomoo-mcp"
        )
    # Without an account OpenD prompts on stdin forever, which under `up -d`
    # looks "Up" while never logging in. Fail loudly instead of hanging.
    raise GatewayLoginError(
        "MOOMOO_LOGIN_ACCOUNT is empty. Set it in .env; a headless start also "
        "needs a remembered token from the interactive login (OPEND_INTERACTIVE=1)."
    )


def server_spec(environ: dict[str, str] | None = None) -> ChildSpec:
    """Run the MCP server out of this same interpreter's environment."""
    env = dict(os.environ if environ is None else environ)
    return ChildSpec(
        name=SERVER,
        argv=[env.get("MCP_PYTHON", sys.executable), "-m", "moomoo_mcp.server"],
    )


class Supervisor:
    """Runs both children and applies the policy in this module's docstring.

    Structured as ``start()`` plus a ``tick()`` that returns an exit code only
    when the run is over, so the policy can be driven a step at a time by tests
    against real child processes. ``run()`` is that loop with a sleep in it.
    """

    def __init__(
        self,
        gateway: ChildSpec,
        server: ChildSpec,
        *,
        max_gateway_restarts: int = 5,
        gateway_restart_window: float = 300.0,
        gateway_restart_backoff: float = 1.0,
        max_gateway_restart_backoff: float = 30.0,
        stop_timeout: float = 10.0,
        poll_interval: float = 0.1,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.gateway = gateway
        self.server = server
        self.max_gateway_restarts = max_gateway_restarts
        self.gateway_restart_window = gateway_restart_window
        self.gateway_restart_backoff = gateway_restart_backoff
        self.max_gateway_restart_backoff = max_gateway_restart_backoff
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self._monotonic = monotonic
        self._sleep = sleep

        self._pids: dict[str, int] = {}
        self._gateway_restarts: list[float] = []
        self._gateway_restart_due: float | None = None
        self._stop_signal: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_stop_signal)

    def _on_stop_signal(self, signum, _frame) -> None:
        # Only a flag: the loop below polls, so there is nothing to wake and
        # no handler-reentrancy to reason about.
        self._stop_signal = signum

    def request_stop(self, signum: int = signal.SIGTERM) -> None:
        """Ask for an orderly shutdown, as a received stop signal would."""
        self._stop_signal = signum

    def start(self) -> None:
        self._spawn(self.gateway)
        # Deliberately not conditional on the gateway being up. A server that
        # cannot reach OpenD still answers health, and its own connection is
        # not opened until a client calls it.
        self._spawn(self.server)

    def run(self) -> int:
        self.install_signal_handlers()
        self.start()
        while True:
            code = self.tick()
            if code is not None:
                return code
            self._sleep(self.poll_interval)

    def tick(self) -> int | None:
        """One pass of the policy. Returns an exit code once the run is over."""
        if self._stop_signal is not None:
            logger.info(
                "received %s, stopping both processes",
                signal.Signals(self._stop_signal).name,
            )
            self.shutdown()
            return EXIT_OK

        code = self._reap()
        if code is not None:
            return code

        if (
            self._gateway_restart_due is not None
            and self._monotonic() >= self._gateway_restart_due
        ):
            self._gateway_restart_due = None
            self._spawn(self.gateway)
        return None

    # -- children ----------------------------------------------------------

    def _spawn(self, spec: ChildSpec) -> int:
        env = {**os.environ, **spec.env}
        pid = os.fork()
        if pid == 0:  # child
            try:
                # Back to default dispositions: the handlers installed above
                # belong to the supervisor, and an inherited ignore would make
                # a child unkillable by the shutdown below.
                for sig in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(sig, signal.SIG_DFL)
                if spec.cwd:
                    os.chdir(spec.cwd)
                os.execve(spec.argv[0], spec.argv, env)
            except BaseException:  # noqa: BLE001 - the child must not unwind
                os.write(2, f"failed to exec {spec.argv[0]}\n".encode())
            os._exit(127)
        # Children stay in the supervisor's process group on purpose: the
        # one-time interactive login needs OpenD to read the container's tty,
        # and a process in a background group reading a tty stops on SIGTTIN.
        self._pids[spec.name] = pid
        logger.info("started %s as pid %d: %s", spec.name, pid, " ".join(spec.argv))
        return pid

    def _reap(self) -> int | None:
        """Collect everything that has exited, and decide what it means."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return None
            if pid == 0:
                return None

            name = next((n for n, p in self._pids.items() if p == pid), None)
            if name is None:
                # An orphan reparented to us. Reaping it is the whole of PID 1's
                # duty here; it says nothing about either child.
                logger.debug("reaped orphaned pid %d (%s)", pid, _describe(status))
                continue

            del self._pids[name]
            logger.warning("%s (pid %d) %s", name, pid, _describe(status))
            if name == SERVER:
                self.shutdown()
                return EXIT_SERVER_EXITED
            code = self._on_gateway_exit()
            if code is not None:
                return code

    def _on_gateway_exit(self) -> int | None:
        now = self._monotonic()
        window = self.gateway_restart_window
        self._gateway_restarts = [
            at for at in self._gateway_restarts if now - at < window
        ]
        if len(self._gateway_restarts) >= self.max_gateway_restarts:
            logger.error(
                "%s failed %d times in %.0fs; stopping the server and exiting so the "
                "whole container is replaced",
                GATEWAY,
                len(self._gateway_restarts) + 1,
                self.gateway_restart_window,
            )
            self.shutdown()
            return EXIT_GATEWAY_UNRECOVERABLE

        delay = min(
            self.gateway_restart_backoff * (2 ** len(self._gateway_restarts)),
            self.max_gateway_restart_backoff,
        )
        self._gateway_restarts.append(now)
        self._gateway_restart_due = now + delay
        logger.info("restarting %s in %.1fs (the server keeps serving)", GATEWAY, delay)
        return None

    def shutdown(self) -> None:
        """Stop every remaining child, bounded, and reap it."""
        for name, pid in list(self._pids.items()):
            logger.info("stopping %s (pid %d)", name, pid)
            _signal(pid, signal.SIGTERM)

        deadline = self._monotonic() + self.stop_timeout
        while self._pids and self._monotonic() < deadline:
            self._collect(block=False)
            if self._pids:
                self._sleep(self.poll_interval)

        for name, pid in list(self._pids.items()):
            logger.warning("%s (pid %d) ignored SIGTERM; killing it", name, pid)
            _signal(pid, signal.SIGKILL)
        while self._pids:
            self._collect(block=True)

    def _collect(self, *, block: bool) -> None:
        """Reap exited children without interpreting the exits."""
        try:
            pid, _status = os.waitpid(-1, 0 if block else os.WNOHANG)
        except ChildProcessError:
            self._pids.clear()
            return
        if pid == 0:
            return
        for name, known in list(self._pids.items()):
            if known == pid:
                del self._pids[name]


def _signal(pid: int, signum: int) -> None:
    # Already gone is the ordinary case, not an error: the process may have
    # exited between the reap that missed it and this call.
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signum)


def _describe(status: int) -> str:
    if os.WIFSIGNALED(status):
        return f"was killed by {signal.Signals(os.WTERMSIG(status)).name}"
    return f"exited with status {os.WEXITSTATUS(status)}"


def _number(env: dict[str, str], name: str, default: float) -> float:
    """A tunable, or its default. A typo must not take the container down."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("ignoring %s=%r: not a number", name, raw)
        return default


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SUPERVISOR_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [supervisor] %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    try:
        gateway = gateway_spec()
    except GatewayLoginError as exc:
        logger.error("%s", exc)
        return EXIT_CONFIG
    env = dict(os.environ)
    return Supervisor(
        gateway,
        server_spec(),
        max_gateway_restarts=int(_number(env, "OPEND_MAX_RESTARTS", 5)),
        gateway_restart_window=_number(env, "OPEND_RESTART_WINDOW_SECONDS", 300.0),
        stop_timeout=_number(env, "SUPERVISOR_STOP_TIMEOUT_SECONDS", 10.0),
    ).run()


if __name__ == "__main__":
    sys.exit(main())
