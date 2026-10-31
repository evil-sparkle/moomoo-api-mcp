"""Per-request client grants, intersected with the server's trading policy."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

CLIENT_PERMISSIONS = frozenset(
    {
        "market_read",
        "real_read",
        "simulate_read",
        "real_write",
        "simulate_write",
        "gateway_control",
    }
)
DEFAULT_RESTRICTED_PERMISSIONS = frozenset(
    {"market_read", "real_read", "simulate_read", "simulate_write"}
)


class ClientAuthorizationError(PermissionError):
    """The authenticated client's grant does not permit this request."""


@dataclass(frozen=True)
class ClientGrants:
    permissions: frozenset[str] = DEFAULT_RESTRICTED_PERMISSIONS
    real_acc_ids: frozenset[int] | None = None
    simulate_acc_ids: frozenset[int] | None = None

    def __post_init__(self) -> None:
        if not self.permissions <= CLIENT_PERMISSIONS:
            raise ValueError("Client grants contain an unknown permission")
        for accounts in (self.real_acc_ids, self.simulate_acc_ids):
            if accounts is not None and any(
                isinstance(account, bool)
                or not isinstance(account, int)
                or account <= 0
                for account in accounts
            ):
                raise ValueError("Client account grants require positive exact IDs")


_client_grants: ContextVar[ClientGrants | None] = ContextVar(
    "moomoo_client_grants", default=None
)


def current_client_grants() -> ClientGrants | None:
    """None is the trusted process or legacy full-policy MCP credential."""
    return _client_grants.get()


@contextmanager
def client_grants_context(grants: ClientGrants | None) -> Iterator[None]:
    token = _client_grants.set(grants)
    try:
        yield
    finally:
        _client_grants.reset(token)


def permission_allowed(permission: str) -> bool:
    grants = current_client_grants()
    return grants is None or permission in grants.permissions


def check_permission(permission: str) -> None:
    if not permission_allowed(permission):
        raise ClientAuthorizationError(
            "Client permission does not allow this operation"
        )


def account_access_allowed(
    trd_env: str, acc_id: int | str, *, write: bool = False
) -> bool:
    grants = current_client_grants()
    if grants is None:
        return True
    environment = str(trd_env).strip().upper()
    if environment not in {"REAL", "SIMULATE"}:
        return False
    permission = environment.lower() + ("_write" if write else "_read")
    if permission not in grants.permissions:
        return False
    accounts = grants.real_acc_ids if environment == "REAL" else grants.simulate_acc_ids
    if accounts is None:
        return True
    value = str(acc_id)
    return value.isascii() and value.isdecimal() and int(value) in accounts


def check_account_access(
    trd_env: str, acc_id: int | str, *, write: bool = False
) -> None:
    if not account_access_allowed(trd_env, acc_id, write=write):
        raise ClientAuthorizationError("Client permission does not allow this account")


def filter_readable_accounts(accounts: list[dict]) -> list[dict]:
    return [
        account
        for account in accounts
        if account_access_allowed(account.get("trd_env", ""), account.get("acc_id", ""))
    ]


def client_authorization_status() -> dict:
    grants = current_client_grants()
    return {
        "restricted": grants is not None,
        "permissions": sorted(grants.permissions if grants else CLIENT_PERMISSIONS),
    }


MARKET_TOOLS = frozenset(
    {
        "get_market_snapshot_evidence",
        "get_instrument_facts",
        "get_history_kl_quota",
        "get_rehab",
        "get_corporate_actions_dividends",
        "get_earnings_calendar",
        "get_option_volatility",
        "get_stock_quote",
        "get_historical_klines",
        "get_historical_klines_page",
        "get_option_expiration_date",
        "get_option_chain",
        "get_market_snapshot",
        "get_order_book",
        "get_market_state",
        "get_trading_days",
        "get_subscriptions",
        "get_user_security_group",
        "get_user_security",
        "get_margin_ratio",
    }
)
ACCOUNT_TOOLS = frozenset(
    {
        "get_account_summary",
        "get_assets",
        "get_positions",
        "get_max_tradable",
        "get_cash_flow",
        "get_trading_capabilities",
        "get_trade_events",
        "preview_order",
        "preview_combo_order",
        "get_orders",
        "get_deals",
        "get_history_orders",
        "get_history_deals",
        "get_order",
        "get_order_fees",
        "get_orders_page",
        "get_deals_page",
        "get_history_orders_page",
        "get_history_deals_page",
        "get_execution",
        "reconcile_execution",
    }
)
MUTATION_TOOLS = frozenset(
    {"place_order", "place_combo_order", "modify_order", "cancel_order"}
)


def tool_visible(name: str) -> bool:
    if current_client_grants() is None:
        return True
    if name == "check_health":
        return True
    if name in MARKET_TOOLS:
        return permission_allowed("market_read")
    if name in {"unlock_trade", "lock_trade", "unsubscribe_market_data"}:
        return permission_allowed("gateway_control")
    if name in MUTATION_TOOLS or name == "prepare_trade_request":
        return permission_allowed("real_write") or permission_allowed("simulate_write")
    if name in ACCOUNT_TOOLS or name in {"get_accounts", "get_prepared_trade"}:
        return permission_allowed("real_read") or permission_allowed("simulate_read")
    return False


def authorize_tool(
    name: str, arguments: Mapping[str, Any], *, default_environment: str | None = None
) -> None:
    """Explicitly classify tools; future tools receive no restricted grant."""
    if current_client_grants() is None:
        return
    if name in MARKET_TOOLS:
        check_permission("market_read")
        return
    if name in {"unlock_trade", "lock_trade", "unsubscribe_market_data"}:
        check_permission("gateway_control")
        return
    if name == "get_accounts":
        environment = arguments.get("trd_env")
        if environment is not None:
            normalized = str(environment).strip().upper()
            if normalized not in {"REAL", "SIMULATE"}:
                raise ClientAuthorizationError("Client request has no authorized scope")
            check_permission(normalized.lower() + "_read")
        elif not permission_allowed("real_read") and not permission_allowed(
            "simulate_read"
        ):
            raise ClientAuthorizationError("Client cannot read trading accounts")
        return
    if name == "get_prepared_trade":
        if not permission_allowed("real_read") and not permission_allowed(
            "simulate_read"
        ):
            raise ClientAuthorizationError("Client cannot read prepared trades")
        # Stored intent still checks its resolved account before disclosure.
        return
    if name == "check_health":
        # Account discovery, journal health and stored intent enforce scope after
        # resolving their account identities. No tool here grants approval.
        return
    write = name in MUTATION_TOOLS
    if name == "prepare_trade_request":
        if arguments.get("method") not in MUTATION_TOOLS:
            raise ClientAuthorizationError("Client cannot prepare this operation")
        parameters = arguments.get("parameters")
        if not isinstance(parameters, dict):
            raise ClientAuthorizationError("Client request has no authorized scope")
        arguments = parameters
        write = True
    elif not write and name not in ACCOUNT_TOOLS:
        raise ClientAuthorizationError("Client has no grant for this tool")
    environment = (
        str(arguments.get("trd_env", default_environment) or "").strip().upper()
    )
    if environment not in {"REAL", "SIMULATE"}:
        raise ClientAuthorizationError(
            "Client request must name an allowed environment"
        )
    check_permission(environment.lower() + ("_write" if write else "_read"))
    account = arguments.get("acc_id", "0")
    if str(account) != "0":
        check_account_access(environment, account, write=write)


def visible_journal_health(health: dict, trd_env: str) -> dict:
    """Keep readiness while withholding cross-account journal evidence."""
    grants = current_client_grants()
    if grants is None:
        return health
    environment = trd_env.strip().upper()
    permission = environment.lower() + "_read"
    accounts = grants.real_acc_ids if environment == "REAL" else grants.simulate_acc_ids
    if permission not in grants.permissions:
        return {"state": "NOT_AUTHORIZED"}
    if accounts is None:
        return health
    # Health aggregates IDs without account identities. Avoid guessing which
    # journal entries belong to a client's narrower account allowlist.
    return {
        key: value
        for key, value in health.items()
        if key in {"state", "admission_epoch", "blocking_reasons"}
    } | {"details": "ACCOUNT_SCOPED_DETAILS_OMITTED"}


def visible_journal_error(error: str | None, trd_env: str) -> str | None:
    """Remove global pending IDs retained in older operation error records."""
    grants = current_client_grants()
    if grants is None or not error:
        return error
    environment = trd_env.strip().upper()
    accounts = grants.real_acc_ids if environment == "REAL" else grants.simulate_acc_ids
    if accounts is None and environment.lower() + "_read" in grants.permissions:
        return error
    prefix, marker, details = error.partition(": pending operations ")
    if not marker or environment + " execution " not in prefix:
        return error
    guidance = "; automatic recovery or healthy storage restart is required;"
    _, separator, following = details.rpartition(guidance)
    if separator:
        return prefix + ": " + guidance[2:] + following
    return (
        prefix + ": automatic recovery or healthy storage restart is required; "
        "see check_health/get_execution. No order was sent"
    )
