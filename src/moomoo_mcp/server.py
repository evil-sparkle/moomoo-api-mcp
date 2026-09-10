import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from moomoo.common import ft_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from moomoo_mcp.services.base_service import MoomooService
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
)

logger = logging.getLogger(__name__)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforces constant-time bearer token authorization on HTTP/SSE requests."""

    def __init__(self, app, auth_token: str) -> None:
        super().__init__(app)
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if hmac.compare_digest(token, self.auth_token):
                return await call_next(request)

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
    ft_logger.logger.consoleHandler = logging.NullHandler()


@dataclass
class AppContext:
    """Application context with typed dependencies."""
    moomoo_service: MoomooService
    trade_service: TradeService
    market_data_service: MarketDataService


def _auto_unlock_trade(trade_service: TradeService) -> None:
    """Attempt to auto-unlock trade using environment variables.

    Reads MOOMOO_TRADE_PASSWORD (plain text, preferred) or MOOMOO_TRADE_PASSWORD_MD5.
    Logs status and handles failures gracefully without crashing.

    Only a REAL-mode deployment unlocks. A configured password does not promote
    the mode, and a failed unlock leaves the policy untouched — it never
    triggers a retry or an order.
    """
    password = os.environ.get("MOOMOO_TRADE_PASSWORD")
    password_md5 = os.environ.get("MOOMOO_TRADE_PASSWORD_MD5")

    mode = trade_service.policy.mode.value
    if not password and not password_md5:
        logger.info(
            "No trade password configured (MOOMOO_TRADE_PASSWORD or "
            f"MOOMOO_TRADE_PASSWORD_MD5 not set). Trading mode: {mode}."
        )
        return

    try:
        trade_service.policy.check_unlock()
    except TradingPolicyError as e:
        logger.info(
            f"A trade password is configured, but not unlocking: {e} "
            "Trading mode is unchanged."
        )
        return

    try:
        # Must fetch account list before unlock to initialize account context
        accounts = trade_service.get_accounts()
        logger.info(f"Found {len(accounts)} trading account(s)")

        if password:
            trade_service.unlock_trade(password=password)
            logger.info("Trade unlocked successfully. REAL account access enabled.")
        else:
            trade_service.unlock_trade(password_md5=password_md5)
            logger.info(
                "Trade unlocked successfully (via MD5). "
                "REAL account access enabled."
            )
    except RuntimeError as e:
        logger.warning(
            f"Failed to unlock trade: {e}. "
            "REAL account access will not be available. "
            "Use unlock_trade tool to retry manually."
        )


@asynccontextmanager
async def app_lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage moomoo connections lifecycle."""
    # Read OpenD connection settings from environment
    opend_host = os.environ.get("MOOMOO_OPEND_HOST", "127.0.0.1")
    opend_port_raw = os.environ.get("MOOMOO_OPEND_PORT", "11111")
    opend_port = int(opend_port_raw) if opend_port_raw.isdigit() else 11111
    logger.info(f"Connecting to OpenD at {opend_host}:{opend_port}")

    # Parsed before anything connects: an unknown mode is a configuration error,
    # not something to recover from by picking a permissive default.
    policy = TradingPolicy.from_env()
    logger.info(f"Trading mode: {policy.mode.value}")

    # Read security firm from env (e.g. FUTUSG for SG, FUTUSECURITIES for HK)
    security_firm = os.environ.get("MOOMOO_SECURITY_FIRM")
    if security_firm:
        logger.info(f"Using security firm: {security_firm}")

    moomoo_service = MoomooService(host=opend_host, port=opend_port)
    trade_service = TradeService(
        host=opend_host,
        port=opend_port,
        security_firm=security_firm,
        policy=policy,
    )

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
                    f"{opend_host}:{opend_port}: {exc}. "
                    "The server will start; use check_health to diagnose."
                )

        if trade_service.trade_ctx is not None:
            if policy.mode is TradingMode.READ_ONLY:
                try:
                    trade_service.lock_trade()
                    logger.info(
                        "Proactively locked trade gateway on startup in READ_ONLY mode."
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Failed to proactively lock trade on OpenD: {exc}")
            elif policy.mode is TradingMode.REAL:
                # Auto-unlock trade if password is configured in environment
                _auto_unlock_trade(trade_service)

        # Create market data service using the shared quote context
        market_data_service = MarketDataService(quote_ctx=moomoo_service.quote_ctx)

        yield AppContext(
            moomoo_service=moomoo_service,
            trade_service=trade_service,
            market_data_service=market_data_service,
        )
    finally:
        trade_service.close()
        moomoo_service.close()

mcp = FastMCP(
    "Moomoo Trading",
    lifespan=app_lifespan,
    dependencies=["moomoo-api", "pandas"],
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
        app.add_middleware(BearerAuthMiddleware, auth_token=token)
    return app


def main():
    """Entry point for the MCP server."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()

    if transport in ("sse", "streamable-http"):
        if auth_token:
            logger.info("Enabling bearer token authentication for SSE.")
            import uvicorn

            app = create_sse_app(auth_token=auth_token)
            host = os.environ.get("FASTMCP_HOST", "127.0.0.1")
            port = int(os.environ.get("FASTMCP_PORT", "8000"))
            uvicorn.run(app, host=host, port=port)
        else:
            mcp.run(transport=transport)
    else:
        mcp.run()


if __name__ == "__main__":
    main()

