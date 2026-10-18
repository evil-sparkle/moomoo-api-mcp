"""Everything this process reads from its environment, parsed once at startup.

Configuration used to be read where it was needed: the policy in
``_build_services``, the trade credential inside the just-in-time unlock, the
auth token in ``main``. A bad value therefore surfaced on the first tool call
that happened to touch it, and the supervisor kept a server running whose every
request failed.

``main()`` calls :func:`load_settings` before it starts the HTTP server, so an
invalid value exits the process before anything listens. The container's
``restart: unless-stopped`` then produces a crash loop whose log line names the
variable — which is the intended fail-closed behaviour, and is what the deploy
runbook tells an operator to look for.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from moomoo import SecurityFirm

from moomoo_mcp.client_authorization import DEFAULT_RESTRICTED_PERMISSIONS, ClientGrants
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
ENV_REPORTING_CURRENCY = "MOOMOO_REPORTING_CURRENCY"
ENV_TRADE_PASSWORD = "MOOMOO_TRADE_PASSWORD"
ENV_TRADE_PASSWORD_MD5 = "MOOMOO_TRADE_PASSWORD_MD5"
ENV_TRANSPORT = "MCP_TRANSPORT"
ENV_AUTH_TOKEN = "MCP_AUTH_TOKEN"
ENV_ALLOW_UNAUTHENTICATED_HTTP = "MCP_ALLOW_UNAUTHENTICATED_HTTP"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111
# OpenD account valuation currencies. This setting never changes instrument currencies.
VALID_REPORTING_CURRENCIES = (
    "HKD",
    "USD",
    "CNH",
    "JPY",
    "SGD",
    "AUD",
    "CAD",
    "MYR",
)
VALID_TRANSPORTS = ("streamable-http",)


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
    trade_password: str | None = field(repr=False)
    trade_password_md5: str | None = field(repr=False)
    transport: str
    auth_token: str = field(repr=False)
    allow_unauthenticated_http: bool
    simulated_account_allowlist: frozenset[int] = frozenset()
    journal_path: str | None = None
    create_journal: bool = False
    journal_lock_wait_ms: int = 5000
    allow_chatgpt_tunnel_host: bool = False
    real_journal_path: str | None = None
    create_real_journal: bool = False
    controls_path: str | None = None
    create_controls: bool = False
    operator_auth_token: str = field(default="", repr=False)
    operator_id: str = ""
    reporting_currency: str | None = None
    observe_execution: bool = False
    restricted_auth_token: str = field(default="", repr=False)
    restricted_grants: ClientGrants | None = None

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


def _load_reporting_currency(environ: Mapping[str, str]) -> str | None:
    """Optional account-valuation currency; None preserves the SDK default."""
    raw = (environ.get(ENV_REPORTING_CURRENCY) or "").strip()
    if not raw:
        return None
    candidate = raw.upper()
    if candidate not in VALID_REPORTING_CURRENCIES:
        raise TradingModeConfigError(
            f"{ENV_REPORTING_CURRENCY} is set to {raw!r}. Valid values: "
            f"{', '.join(VALID_REPORTING_CURRENCIES)}."
        )
    return candidate


def _load_transport(environ: Mapping[str, str]) -> str:
    raw = (environ.get(ENV_TRANSPORT) or "streamable-http").strip().lower()
    if raw not in VALID_TRANSPORTS:
        raise TradingModeConfigError(
            f"{ENV_TRANSPORT} is set to {raw!r}. Valid values: "
            f"{', '.join(VALID_TRANSPORTS)}."
        )
    return raw


def load_chatgpt_tunnel_host(environ: Mapping[str, str]) -> bool:
    """Opt in to one Docker service Host."""
    value = environ.get("MCP_ALLOW_CHATGPT_TUNNEL_HOST", "").strip()
    if value not in {"", "0", "1"}:
        raise TradingModeConfigError("MCP_ALLOW_CHATGPT_TUNNEL_HOST must be 0 or 1")
    return value == "1"


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

    allowlist: set[int] = set()
    journal_path = None
    create_journal = False
    lock_wait = 5000
    real_journal_path = None
    create_real_journal = False
    observe = (env.get("MOOMOO_OBSERVE_EXECUTION") or "0").strip()
    if observe not in {"0", "1"}:
        raise TradingModeConfigError("MOOMOO_OBSERVE_EXECUTION must be 0 or 1")
    observe_execution = observe == "1"
    writable = policy.mode is not TradingMode.READ_ONLY
    if writable or observe_execution:
        raw_ids = (env.get("MOOMOO_SIMULATED_ACC_IDS") or "").strip()
        if raw_ids:
            for item in raw_ids.split(","):
                item = item.strip()
                if (
                    not item
                    or not all(c in "0123456789" for c in item)
                    or int(item) <= 0
                ):
                    raise TradingModeConfigError(
                        "MOOMOO_SIMULATED_ACC_IDS must list positive decimal "
                        "account IDs"
                    )
                allowlist.add(int(item))
        journal_path = (env.get("MOOMOO_JOURNAL_PATH") or "").strip() or None
        creation = (env.get("MOOMOO_CREATE_JOURNAL") or "0").strip()
        if creation not in {"0", "1"}:
            raise TradingModeConfigError("MOOMOO_CREATE_JOURNAL must be 0 or 1")
        create_journal = creation == "1" and writable
        raw_wait = (env.get("MOOMOO_JOURNAL_LOCK_WAIT_MS") or "5000").strip()
        if (
            not raw_wait.isascii()
            or not raw_wait.isdecimal()
            or not 1 <= int(raw_wait) <= 60000
        ):
            raise TradingModeConfigError(
                "MOOMOO_JOURNAL_LOCK_WAIT_MS must be between 1 and 60000"
            )
        lock_wait = int(raw_wait)
        if writable and (
            policy.mode is TradingMode.SIMULATE
            or journal_path
            or allowlist
            or create_journal
        ):
            if not allowlist:
                raise TradingModeConfigError(
                    "MOOMOO_SIMULATED_ACC_IDS is required for paper execution"
                )
            if not journal_path:
                raise TradingModeConfigError(
                    "MOOMOO_JOURNAL_PATH is required for paper execution"
                )
            if _load_trading_market(env) not in {"US", "NONE"}:
                raise TradingModeConfigError(
                    "MOOMOO_TRADING_MARKET must be US or NONE for paper execution"
                )

    if policy.mode is TradingMode.REAL or observe_execution:
        real_journal_path = (env.get("MOOMOO_REAL_JOURNAL_PATH") or "").strip() or None
        if not real_journal_path and policy.mode is TradingMode.REAL:
            raise TradingModeConfigError(
                "MOOMOO_REAL_JOURNAL_PATH is required for durable REAL execution"
            )
        creation = (env.get("MOOMOO_CREATE_REAL_JOURNAL") or "0").strip()
        if creation not in {"0", "1"}:
            raise TradingModeConfigError("MOOMOO_CREATE_REAL_JOURNAL must be 0 or 1")
        create_real_journal = creation == "1" and policy.mode is TradingMode.REAL
        if (
            journal_path
            and real_journal_path
            and (
                Path(journal_path).resolve().parent
                == Path(real_journal_path).resolve().parent
            )
        ):
            raise TradingModeConfigError(
                "MOOMOO_REAL_JOURNAL_PATH and MOOMOO_JOURNAL_PATH must use "
                "distinct directories"
            )

    controls_path = None
    create_controls = False
    operator_token = (env.get("MOOMOO_OPERATOR_AUTH_TOKEN") or "").strip()
    operator_id = (env.get("MOOMOO_OPERATOR_ID") or "").strip()
    if operator_token:
        if not auth_token:
            raise TradingModeConfigError(
                "MCP_AUTH_TOKEN is required with an operator channel"
            )
        if operator_token == auth_token:
            raise TradingModeConfigError(
                "MOOMOO_OPERATOR_AUTH_TOKEN must differ from MCP_AUTH_TOKEN"
            )
        if not operator_token.isascii() or any(
            not character.isprintable() or character.isspace()
            for character in operator_token
        ):
            raise TradingModeConfigError("MOOMOO_OPERATOR_AUTH_TOKEN is invalid")
        if not operator_id or len(operator_id) > 128 or not operator_id.isprintable():
            raise TradingModeConfigError("MOOMOO_OPERATOR_ID is required and bounded")
    if writable or observe_execution or operator_token:
        controls_path = (env.get("MOOMOO_TRADING_CONTROLS_PATH") or "").strip() or None
        creation = (env.get("MOOMOO_CREATE_TRADING_CONTROLS") or "0").strip()
        if creation not in {"0", "1"}:
            raise TradingModeConfigError(
                "MOOMOO_CREATE_TRADING_CONTROLS must be 0 or 1"
            )
        create_controls = creation == "1" and writable
        if (
            policy.require_approval or policy.max_daily_notional or operator_token
        ) and not controls_path:
            raise TradingModeConfigError("MOOMOO_TRADING_CONTROLS_PATH is required")
        if writable and policy.require_approval and not operator_token:
            raise TradingModeConfigError(
                "MOOMOO_OPERATOR_AUTH_TOKEN is required for approvals"
            )
        if controls_path:
            for journal in (journal_path, real_journal_path):
                if journal and Path(controls_path).resolve() == Path(journal).resolve():
                    raise TradingModeConfigError(
                        "Trading controls must use distinct storage"
                    )

    restricted_token = (env.get("MCP_RESTRICTED_AUTH_TOKEN") or "").strip()
    restricted_grants = None
    if restricted_token:
        if (
            not auth_token
            or restricted_token in {auth_token, operator_token}
            or not restricted_token.isascii()
            or any(not c.isprintable() or c.isspace() for c in restricted_token)
        ):
            raise TradingModeConfigError(
                "MCP_RESTRICTED_AUTH_TOKEN must be valid and distinct from other tokens"
            )
        raw_permissions = env.get("MCP_RESTRICTED_PERMISSIONS")
        permissions = (
            frozenset(item.strip() for item in raw_permissions.split(","))
            if raw_permissions is not None
            else DEFAULT_RESTRICTED_PERMISSIONS
        )
        account_scopes = []
        for name in ("MCP_RESTRICTED_REAL_ACC_IDS", "MCP_RESTRICTED_SIMULATE_ACC_IDS"):
            raw_scope = env.get(name)
            if raw_scope is None:
                account_scopes.append(None)
                continue
            parts = [item.strip() for item in raw_scope.split(",")]
            if any(
                not p or not p.isascii() or not p.isdecimal() or int(p) <= 0
                for p in parts
            ):
                raise TradingModeConfigError(f"{name} must list positive account IDs")
            account_scopes.append(frozenset(int(p) for p in parts))
        try:
            restricted_grants = ClientGrants(permissions, *account_scopes)
        except ValueError as exc:
            raise TradingModeConfigError(str(exc)) from exc

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
        simulated_account_allowlist=frozenset(allowlist),
        journal_path=journal_path,
        create_journal=create_journal,
        journal_lock_wait_ms=lock_wait,
        allow_chatgpt_tunnel_host=load_chatgpt_tunnel_host(env),
        real_journal_path=real_journal_path,
        create_real_journal=create_real_journal,
        controls_path=controls_path,
        create_controls=create_controls,
        operator_auth_token=operator_token,
        operator_id=operator_id,
        reporting_currency=_load_reporting_currency(env),
        observe_execution=observe_execution,
        restricted_auth_token=restricted_token,
        restricted_grants=restricted_grants,
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


def validate_storage(settings: Settings) -> None:
    """Probe mounted storage read-only without competing for executor ownership."""
    import sqlite3

    stores = (
        (settings.journal_path, settings.create_journal, "SIMULATE"),
        (settings.real_journal_path, settings.create_real_journal, "REAL"),
        (settings.controls_path, settings.create_controls, None),
    )
    for location, create, environment in stores:
        if not location:
            continue
        path = Path(location)
        if not path.exists():
            if create:
                continue
            raise TradingModeConfigError("Configured storage requires initialization")
        try:
            with sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True
            ) as conn:
                if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise TradingModeConfigError("Configured storage integrity failed")
                if environment:
                    from moomoo_mcp.services.execution_store import SCHEMA_VERSION

                    if conn.execute("SELECT environment FROM metadata").fetchall() != [
                        (environment,)
                    ] or conn.execute("PRAGMA user_version").fetchone()[0] not in {
                        1,
                        2,
                        SCHEMA_VERSION,
                    }:
                        raise TradingModeConfigError(
                            "Configured journal identity is invalid"
                        )
                    conn.execute(
                        "SELECT operation_id, account, state FROM operations LIMIT 0"
                    )
                else:
                    if conn.execute("SELECT version FROM metadata").fetchall() != [
                        (1,)
                    ]:
                        raise TradingModeConfigError(
                            "Configured controls schema is invalid"
                        )
                    conn.execute("SELECT request_id, canonical FROM prepared LIMIT 0")
                    conn.execute("SELECT environment, account FROM charges LIMIT 0")
        except sqlite3.Error as exc:
            raise TradingModeConfigError("Configured storage is unavailable") from exc


def main() -> None:
    """Offline deployment preflight; configuration stays on stdin, never in argv."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Validate MCP configuration offline")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--validate-storage", action="store_true")
    arguments = parser.parse_args()
    if not arguments.validate and not arguments.validate_storage:
        parser.error("select --validate or --validate-storage")
    try:
        raw = sys.stdin.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("Configuration exceeds the allowed size")
        values = json.loads(raw)
        if not isinstance(values, dict) or any(
            not isinstance(key, str) or not isinstance(value, (str, type(None)))
            for key, value in values.items()
        ):
            raise ValueError("Supply a JSON environment mapping")
        settings = load_settings(
            {key: value for key, value in values.items() if value is not None}
        )
        check_transport_authentication(settings)
        if arguments.validate_storage:
            validate_storage(settings)
    except (ValueError, TradingModeConfigError) as exc:
        print(f"Configuration validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("Configuration validation passed")


if __name__ == "__main__":
    main()
