import atexit
import hmac
import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from moomoo.common import ft_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.instruments import InstrumentAdapter
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import TradingModeConfigError
from moomoo_mcp.settings import (
    Settings,
    check_transport_authentication,
    load_settings,
)

logger = logging.getLogger(__name__)

operator_principal: ContextVar[str | None] = ContextVar(
    "operator_principal", default=None
)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforces constant-time bearer token authorization on HTTP/SSE requests."""

    def __init__(self, app, auth_token: str, operator_token: str | None = None) -> None:
        super().__init__(app)
        self.auth_token = auth_token
        if operator_token and operator_token == auth_token:
            raise ValueError("MCP_OPERATOR_TOKEN must differ from MCP_AUTH_TOKEN")
        self.operator_token = operator_token

    async def dispatch(self, request: Request, call_next) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            is_operator = bool(self.operator_token) and hmac.compare_digest(
                token, self.operator_token or ""
            )
            if is_operator or hmac.compare_digest(token, self.auth_token):
                binding = operator_principal.set("operator" if is_operator else None)
                try:
                    return await call_next(request)
                finally:
                    operator_principal.reset(binding)

        return JSONResponse(
            {"detail": "Unauthorized: Invalid or missing bearer token."},
            status_code=401,
        )


# Disable moomoo library console logging to prevent corruption of MCP stdout protocol
if hasattr(ft_logger, "logger") and hasattr(ft_logger.logger, "console_logger"):
    # Clear existing handlers
    ft_logger.logger.console_logger.handlers = []
    # Replace the internal consoleHandler reference with a NullHandler.
    # This ensures that when fontColor/info/error is called and it tries to re-add
    # self.consoleHandler, it adds a harmless NullHandler instead of a StreamHandler.
    # FTLog declares consoleHandler as a StreamHandler, so the SDK's own
    # type rejects the NullHandler that silences it.
    ft_logger.logger.consoleHandler = logging.NullHandler()  # pyright: ignore[reportAttributeAccessIssue]


@dataclass
class AppContext:
    """Application context with typed dependencies."""

    moomoo_service: MoomooService
    trade_service: TradeService
    market_data_service: MarketDataService


# The gateway connections belong to the process, not to a session. Built once,
# under a lock, and handed to every session that follows.
_services: AppContext | None = None
_prepared_journal: tuple[Settings, ExecutionStore] | None = None
_services_lock = threading.Lock()


def _build_services(settings: Settings | None = None) -> AppContext:
    """Open this process's connections to OpenD.

    Read the note on ``app_lifespan`` before moving anything back inline: this
    runs once per process, not once per session.

    Args:
        settings: The already-validated configuration. Loaded here when absent,
            which is the path direct library use and the tests take; ``main()``
            loads it earlier so a bad value exits before anything listens.
    """
    global _prepared_journal
    prepared = _prepared_journal
    _prepared_journal = None
    resolved = prepared[0] if prepared else settings or load_settings()
    logger.info(f"Connecting to OpenD at {resolved.opend_host}:{resolved.opend_port}")
    logger.info(f"Trading mode: {resolved.policy.mode.value}")
    if resolved.security_firm:
        logger.info(f"Using security firm: {resolved.security_firm}")

    execution_store = (
        prepared[1]
        if prepared
        else (
            ExecutionStore(
                resolved.journal_path,
                create=resolved.create_journal,
                lock_wait_ms=resolved.journal_lock_wait_ms,
            )
            if resolved.journal_path
            else None
        )
    )
    try:
        moomoo_service = MoomooService(
            host=resolved.opend_host, port=resolved.opend_port
        )
    except BaseException:
        if execution_store is not None:
            execution_store.close()
        raise
    try:
        trade_service = TradeService(
            host=resolved.opend_host,
            port=resolved.opend_port,
            security_firm=resolved.security_firm,
            trading_market=resolved.trading_market,
            policy=resolved.policy,
            execution_store=execution_store,
            simulated_account_allowlist=resolved.simulated_account_allowlist,
            trade_password=resolved.trade_password,
            trade_password_md5=resolved.trade_password_md5,
            # Reads the shared quote context through a callable, not the object:
            # the connection is opened lazily and replaced on reconnect, so holding
            # the instance would pin whichever one existed at wiring time.
            instrument_lookup=InstrumentAdapter(lambda: moomoo_service.quote_ctx),
        )
    except BaseException:
        if execution_store is not None:
            execution_store.close()
        moomoo_service.close()
        raise

    try:
        # A downstream connection failure must not take the MCP server down with
        # it: check_health is the tool an operator reaches for precisely when
        # OpenD is unreachable, so it has to stay callable. Whatever did connect
        # is still released by the finally block below.
        for name, service in (("quote", moomoo_service), ("trade", trade_service)):
            try:
                service.connect()
            except Exception as exc:  # noqa: BLE001 - startup must stay available
                logger.error(
                    f"Failed to initialize the {name} connection to OpenD at "
                    f"{resolved.opend_host}:{resolved.opend_port}: {exc}. "
                    "The server will start; use check_health to diagnose."
                )

        # Nothing unlocks at startup. The trade service asserts the lock at rest
        # itself, on every connection and every SDK reconnect, and REAL writes
        # unlock just in time for one order. A startup unlock would be a second
        # unlock path that the SDK then replays after every reconnect, leaving
        # the gateway unlocked for the life of the process.

        # Create market data service using the shared quote context
        market_data_service = MarketDataService(quote_ctx=moomoo_service.quote_ctx)

        return AppContext(
            moomoo_service=moomoo_service,
            trade_service=trade_service,
            market_data_service=market_data_service,
        )
    except BaseException:
        # Nothing above is expected to raise — connection failures are caught
        # and logged individually — but a half-built set of services must not
        # be left holding sockets that no one can reach to close.
        trade_service.close()
        moomoo_service.close()
        raise


def get_services() -> AppContext:
    """Return this process's services, opening them on first use."""
    global _services

    with _services_lock:
        if _services is None:
            _services = _build_services()
            # Sessions come and go; the connections outlive them and are
            # released when the process is.
            atexit.register(close_services)
        return _services


def close_services() -> None:
    """Release the process's connections. Idempotent."""
    global _services, _prepared_journal

    with _services_lock:
        services = _services
        _services = None
        prepared = _prepared_journal
        _prepared_journal = None
    if prepared is not None:
        prepared[1].close()

    if services is not None:
        services.trade_service.close()
        services.moomoo_service.close()


@asynccontextmanager
async def app_lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Hand the session this process's gateway connections.

    This looks like a process-level startup hook and is not one. The MCP
    lifespan runs inside ``Server.run()``, which the streamable-HTTP session
    manager calls once per session — and, when the server is stateless, once
    per request. Building the services here therefore opened a fresh pair of
    OpenD connections for every client that connected, waited the trade
    connect timeout each time, and closed them again when that client went
    away. Under stateless HTTP it would do all of that per tool call.

    So the services are built once for the process and shared. Sharing is also
    the more honest model: there is one gateway behind them, one unlock state
    on it, and this server's just-in-time unlock serializes against a single
    trade context rather than racing several.
    """
    yield get_services()


mcp = FastMCP(
    "Moomoo Trading",
    lifespan=app_lifespan,
    dependencies=["moomoo-api", "pandas"],
    # Sessions are held in this process's memory, so a restart invalidates
    # every one of them: the client's next call is answered with 404 and it has
    # to initialize again. Clients are required to handle that, but the ones
    # that do not leave a person reconnecting a remote endpoint by hand.
    #
    # Stateless mode issues no session id, ignores any the client still holds,
    # and treats each request as initialized, so a restart costs a client one
    # failed call rather than its session. What it gives up is state this
    # server does not keep: no resumable event stream and no server-initiated
    # notifications outside a request.
    #
    # Viable only because the gateway connections are no longer built per
    # lifespan — see app_lifespan. Reverting that would open a pair of OpenD
    # connections per tool call.
    stateless_http=True,
    # One JSON body per request instead of an SSE stream, for clients that only
    # read JSON (zeroclaw). The SDK then answers with the result alone and drops
    # every notification a tool emits during the call, so ctx.info / ctx.warning
    # never reach an HTTP client; anything a caller needs belongs in the result.
    json_response=True,
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8000")),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "127.0.0.1",
            "localhost",
            "0.0.0.0:*",
            "0.0.0.0",
            "testserver",
        ],
    ),
)

# Import tools to register them
import moomoo_mcp.tools.account  # noqa: E402, F401
import moomoo_mcp.tools.market_data  # noqa: E402, F401
import moomoo_mcp.tools.system  # noqa: E402, F401
import moomoo_mcp.tools.trading  # noqa: E402, F401


def create_sse_app(auth_token: str | None = None):
    """Build the Starlette SSE application with optional bearer auth."""
    app = mcp.sse_app()
    raw = auth_token if auth_token is not None else os.environ.get("MCP_AUTH_TOKEN", "")
    token = raw.strip()
    if token:
        app.add_middleware(BearerAuthMiddleware, auth_token=token, operator_token=None)
    return app


def create_streamable_http_app(
    auth_token: str | None = None, operator_token: str | None = None
):
    """Build the Starlette Streamable HTTP application with optional bearer auth."""
    app = mcp.streamable_http_app()
    raw = auth_token if auth_token is not None else os.environ.get("MCP_AUTH_TOKEN", "")
    token = raw.strip()
    if token:
        app.add_middleware(
            BearerAuthMiddleware, auth_token=token, operator_token=operator_token
        )
    return app


def main():
    """Entry point for the MCP server.

    Configuration is loaded and validated first, before a transport is chosen
    and before anything listens. A bad value therefore exits the process with a
    message naming the variable, rather than surfacing on whichever tool call
    first happened to need it.
    """
    try:
        settings = load_settings()
        check_transport_authentication(settings)
    except TradingModeConfigError as exc:
        # Deliberately fatal. The supervisor stops OpenD and the container
        # restarts, which produces a crash loop whose log line names the
        # variable — the signal the deploy runbook tells an operator to look
        # for. Serving with a configuration this server rejected would be the
        # worse failure: every request would fail, quietly, one at a time.
        logger.error(f"Refusing to start: {exc}")
        raise SystemExit(1) from exc

    if settings.journal_path:
        # Fail storage startup before a transport can serve any request.
        global _prepared_journal
        with _services_lock:
            if _services is None and _prepared_journal is None:
                # Validate/lock storage before listening; gateway connections stay
                # lazy until the first request, as in READ_ONLY and Stage 1.
                _prepared_journal = (
                    settings,
                    ExecutionStore(
                        settings.journal_path,
                        create=settings.create_journal,
                        lock_wait_ms=settings.journal_lock_wait_ms,
                    ),
                )
                atexit.register(close_services)

    transport = settings.transport

    if transport in ("sse", "streamable-http"):
        host = os.environ.get("FASTMCP_HOST", "127.0.0.1")
        port = int(os.environ.get("FASTMCP_PORT", "8000"))
        endpoint = "/mcp" if transport == "streamable-http" else "/sse"

        if settings.auth_token:
            logger.info(
                f"Enabling bearer token authentication for {transport} transport."
            )
        logger.info(
            f"Serving MCP {transport} endpoint at http://{host}:{port}{endpoint}"
        )

        import uvicorn

        if transport == "streamable-http":
            app = create_streamable_http_app(
                auth_token=settings.auth_token, operator_token=settings.operator_token
            )
        else:
            app = create_sse_app(auth_token=settings.auth_token)

        uvicorn.run(app, host=host, port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
