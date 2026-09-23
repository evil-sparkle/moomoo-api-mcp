"""Everything this process reads from its environment, parsed once at startup.

Configuration used to be read where it was needed: the policy in
``_build_services``, the trade credential inside the just-in-time unlock, the
auth token in ``main``. A bad value therefore surfaced on the first tool call
that happened to touch it, and the supervisor kept a server running whose every
request failed.

``main()`` calls :func:`load_settings` before it chooses a transport, so an
invalid value exits the process before anything listens. The container's
``restart: unless-stopped`` then produces a crash loop whose log line names the
variable — which is the intended fail-closed behaviour, and is what the deploy
runbook tells an operator to look for.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from moomoo import SecurityFirm

from moomoo_mcp.services.trading_market import (
    DEFAULT_TRADING_MARKET,
    TRADING_MARKET_FILTERS,
    VALID_TRADING_MARKETS,
)
from moomoo_mcp.services.trading_policy import (
    TradingMode,
    TradingModeConfigError,
    TradingPolicy,
)

logger = logging.getLogger(__name__)

ENV_OPEND_HOST = "MOOMOO_OPEND_HOST"
ENV_OPEND_PORT = "MOOMOO_OPEND_PORT"
ENV_SECURITY_FIRM = "MOOMOO_SECURITY_FIRM"
ENV_TRADING_MARKET = "MOOMOO_TRADING_MARKET"
ENV_TRADE_PASSWORD = "MOOMOO_TRADE_PASSWORD"
ENV_TRADE_PASSWORD_MD5 = "MOOMOO_TRADE_PASSWORD_MD5"
ENV_TRANSPORT = "MCP_TRANSPORT"
ENV_AUTH_TOKEN = "MCP_AUTH_TOKEN"
ENV_ALLOW_UNAUTHENTICATED_HTTP = "MCP_ALLOW_UNAUTHENTICATED_HTTP"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111
VALID_TRANSPORTS = ("stdio", "sse", "streamable-http")
HTTP_TRANSPORTS = frozenset({"sse", "streamable-http"})


def _valid_security_firms() -> tuple[str, ...]:
    """The firms the SDK defines, excluding its not-applicable placeholder.

    ``SecurityFirm.NONE`` is the SDK's "not applicable" value, not a firm. A
    deployment that configures it has said nothing, and accepting it as a
    configured firm would make that indistinguishable from naming a real one.
    """
    return tuple(
        sorted(
            name
            for name in dir(SecurityFirm)
            if name.isupper() and not name.startswith("_") and name != "NONE"
        )
    )


@dataclass(frozen=True)
class Settings:
    """The validated configuration of one server process."""

    opend_host: str
    opend_port: int
    policy: TradingPolicy
    security_firm: str | None
    trading_market: str
    # The stored trade credential, already resolved to one of the two forms.
    # Plain text wins when both are set, as the spec requires.
    trade_password: str | None
    trade_password_md5: str | None
    transport: str
    auth_token: str
    allow_unauthenticated_http: bool

    @property
    def has_trade_credential(self) -> bool:
        """Whether this deployment stores a credential it can unlock with.

        This is what decides the whole unlock lifecycle: with a stored
        credential the server locks the gateway at rest and unlocks just in time
        for a write; without one it leaves the gateway alone and a person
        unlocks it by hand.
        """
        return bool(self.trade_password or self.trade_password_md5)

    @property
    def locks_gateway_at_rest(self) -> bool:
        """Whether this deployment asserts a lock on connect and reconnect."""
        if self.policy.mode is TradingMode.READ_ONLY:
            return True
        return self.policy.mode is TradingMode.REAL and self.has_trade_credential


def _load_port(environ: Mapping[str, str]) -> int:
    raw = (environ.get(ENV_OPEND_PORT) or "").strip()
    if not raw:
        return DEFAULT_PORT
    if not raw.isdigit():
        raise TradingModeConfigError(
            f"{ENV_OPEND_PORT} must be a port number, got {raw!r}."
        )
    port = int(raw)
    if not 1 <= port <= 65535:
        raise TradingModeConfigError(
            f"{ENV_OPEND_PORT} must be between 1 and 65535, got {port}."
        )
    return port


def _load_security_firm(environ: Mapping[str, str]) -> str | None:
    raw = (environ.get(ENV_SECURITY_FIRM) or "").strip()
    if not raw:
        return None
    candidate = raw.upper()
    valid = _valid_security_firms()
    if candidate not in valid:
        # Previously an unknown firm was silently dropped, so a deployment that
        # meant to route through one firm quietly routed through the gateway's
        # default instead.
        raise TradingModeConfigError(
            f"{ENV_SECURITY_FIRM} is set to {raw!r}, which is not a securities "
            f"firm this SDK defines. Valid values: {', '.join(valid)}."
        )
    return candidate


def _load_trading_market(environ: Mapping[str, str]) -> str:
    raw = (environ.get(ENV_TRADING_MARKET) or "").strip()
    if not raw:
        return DEFAULT_TRADING_MARKET
    candidate = raw.upper()
    if candidate not in TRADING_MARKET_FILTERS:
        raise TradingModeConfigError(
            f"{ENV_TRADING_MARKET} is set to {raw!r}. Valid values: "
            f"{', '.join(VALID_TRADING_MARKETS)}."
        )
    return candidate


def _load_transport(environ: Mapping[str, str]) -> str:
    raw = (environ.get(ENV_TRANSPORT) or "stdio").strip().lower()
    if raw not in VALID_TRANSPORTS:
        raise TradingModeConfigError(
            f"{ENV_TRANSPORT} is set to {raw!r}. Valid values: "
            f"{', '.join(VALID_TRANSPORTS)}."
        )
    return raw


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Read and validate this process's configuration.

    Args:
        environ: Environment mapping to read. Defaults to ``os.environ``.

    Returns:
        The validated settings.

    Raises:
        TradingModeConfigError: If any variable holds an invalid value. The
            message names the variable. Nothing falls back to a default in
            place of a value that was set and rejected.
    """
    env = environ if environ is not None else os.environ

    policy = TradingPolicy.from_env(dict(env))

    password = (env.get(ENV_TRADE_PASSWORD) or "").strip() or None
    password_md5 = (env.get(ENV_TRADE_PASSWORD_MD5) or "").strip() or None
    if password and password_md5:
        # Stated precedence rather than an accident of which branch runs first:
        # a deployment that sets both gets the plain-text password, and the hash
        # is ignored for every unlock.
        password_md5 = None

    transport = _load_transport(env)
    auth_token = (env.get(ENV_AUTH_TOKEN) or "").strip()
    allow_unauthenticated = (
        env.get(ENV_ALLOW_UNAUTHENTICATED_HTTP) or ""
    ).strip() == "1"

    return Settings(
        opend_host=(env.get(ENV_OPEND_HOST) or "").strip() or DEFAULT_HOST,
        opend_port=_load_port(env),
        policy=policy,
        security_firm=_load_security_firm(env),
        trading_market=_load_trading_market(env),
        trade_password=password,
        trade_password_md5=password_md5,
        transport=transport,
        auth_token=auth_token,
        allow_unauthenticated_http=allow_unauthenticated,
    )


def check_transport_authentication(settings: Settings) -> None:
    """Refuse to serve an HTTP transport that nothing authenticates.

    An unauthenticated HTTP endpoint exposes every tool this server has,
    including the order-mutating ones, to anything that can reach the port. The
    only exception is an explicit opt-out in a mode that cannot write at all.

    Raises:
        TradingModeConfigError: If an HTTP transport has no token and does not
            qualify for the read-only opt-out.
    """
    if settings.transport not in HTTP_TRANSPORTS:
        return
    if settings.auth_token:
        return

    read_only = settings.policy.mode is TradingMode.READ_ONLY
    if settings.allow_unauthenticated_http and read_only:
        logger.warning(
            f"Serving {settings.transport} without authentication: "
            f"{ENV_ALLOW_UNAUTHENTICATED_HTTP}=1 and {TradingMode.READ_ONLY.value} "
            "mode. Anyone who can reach this port can call every read tool. Do "
            "not use this outside local development."
        )
        return

    if settings.allow_unauthenticated_http:
        detail = (
            f"{ENV_ALLOW_UNAUTHENTICATED_HTTP}=1 is honoured only in "
            f"{TradingMode.READ_ONLY.value} mode, and this server is configured "
            f"for {settings.policy.mode.value}."
        )
    else:
        detail = (
            f"Set {ENV_AUTH_TOKEN}, or set {ENV_ALLOW_UNAUTHENTICATED_HTTP}=1 in "
            f"{TradingMode.READ_ONLY.value} mode if an unauthenticated endpoint "
            "is genuinely what you want."
        )
    raise TradingModeConfigError(
        f"{ENV_AUTH_TOKEN} is required for the {settings.transport} transport. {detail}"
    )
