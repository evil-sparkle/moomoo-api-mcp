"""Conservative documented support, deployment policy and advisory readiness."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from moomoo_mcp.client_authorization import account_access_allowed, permission_allowed
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.health import sanitize_error
from moomoo_mcp.services.trading_policy import TradingMode
from moomoo_mcp.services.validation import validate_choice

if TYPE_CHECKING:
    from moomoo_mcp.services.trade_service import TradeService

SOURCES = [
    "https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html",
    "https://openapi.moomoo.com/moomoo-api-doc/en/qa/trade.html",
    "https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html",
    "https://openapi.moomoo.com/moomoo-api-doc/en/trade/order-fee-query.html",
]
US_TYPES = (
    "NORMAL",
    "MARKET",
    "STOP",
    "STOP_LIMIT",
    "MARKET_IF_TOUCHED",
    "LIMIT_IF_TOUCHED",
    "TRAILING_STOP",
    "TRAILING_STOP_LIMIT",
)
LIMIT_TYPES = ("NORMAL", "STOP_LIMIT", "LIMIT_IF_TOUCHED", "TRAILING_STOP_LIMIT")
HK_TYPES = US_TYPES + (
    "ABSOLUTE_LIMIT",
    "AUCTION",
    "AUCTION_LIMIT",
    "SPECIAL_LIMIT",
    "SPECIAL_LIMIT_ALL",
)


def _documented_types(
    market: str, classification: str | None, environment: str, account: dict
) -> list[str] | None:
    if classification not in {"STOCK", "ETF", "DRVT"}:
        return None
    if environment == "SIMULATE":
        if market not in {"US", "HK", "CN"} or (
            classification == "DRVT" and market not in {"US", "HK"}
        ):
            return None
        simulated_type = account.get("sim_acc_type")
        required = "OPTION" if classification == "DRVT" else "STOCK"
        if simulated_type not in {required, "STOCK_AND_OPTION"}:
            return None
        return ["NORMAL", "MARKET"]
    if market == "US":
        return list(US_TYPES)
    if market == "HK":
        return list(LIMIT_TYPES if classification == "DRVT" else HK_TYPES)
    if market == "HKCC" and classification in {"STOCK", "ETF"}:
        return list(LIMIT_TYPES)
    return None


def inspect_trading_capabilities(
    service: TradeService, code: str, trd_env: str, acc_id: int | str = "0"
) -> dict[str, Any]:
    """Inspect without approval, unlock, order dispatch or broker acceptance claims."""
    environment = validate_choice("trd_env", trd_env, ("REAL", "SIMULATE"))
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty market-prefixed security code")
    normalized_code = code.strip()
    market, separator, symbol = normalized_code.partition(".")
    if not separator or not market or not symbol:
        raise ValueError("code must have a market prefix, for example US.AAPL")
    market = market.upper()
    normalized_code = market + "." + symbol
    requested_id = service._exact_account_id(acc_id, allow_zero=True)
    reasons: list[str] = []
    unknown: list[str] = []
    evidence_errors: dict[str, str] = {}
    account: dict[str, Any] | None = None
    try:
        if requested_id:
            candidates = [
                row
                for row in service.get_accounts(trd_env=environment)
                if service._exact_account_id(row.get("acc_id"), allow_zero=False)
                == requested_id
            ]
        else:
            candidates = service._eligible_accounts(environment, market)
            if environment == "SIMULATE" and service.paper is not None:
                candidates = [
                    row
                    for row in candidates
                    if service._exact_account_id(row.get("acc_id"), allow_zero=False)
                    in service.paper.allowlist
                ]
            if not candidates:
                # Still explain an observed account's refusal when no write
                # candidate exists; never replace an eligible account with it.
                candidates = service.get_accounts(trd_env=environment)
            candidates = [
                row
                for row in candidates
                if account_access_allowed(environment, row.get("acc_id", ""))
            ]
        if len(candidates) == 1:
            account = candidates[0]
        else:
            reasons.append("ACCOUNT_AMBIGUOUS" if candidates else "ACCOUNT_UNAVAILABLE")
    except Exception as exc:  # noqa: BLE001 - unavailable evidence is advisory
        reasons.append("ACCOUNT_DISCOVERY_UNAVAILABLE")
        evidence_errors["account"] = sanitize_error(exc)

    resolved_id = (
        service._exact_account_id(account.get("acc_id"), allow_zero=False)
        if account
        else requested_id or None
    )
    classification = None
    if service.instrument_lookup is None:
        unknown.append("INSTRUMENT_CLASSIFICATION_UNKNOWN")
    else:
        try:
            facts = list(service.instrument_lookup([normalized_code]))
            matching = [fact for fact in facts if fact.code == normalized_code]
            if len(matching) == 1:
                classification = matching[0].classification
            if classification is None:
                unknown.append("INSTRUMENT_CLASSIFICATION_UNKNOWN")
        except Exception as exc:  # noqa: BLE001 - no unsupported inference
            unknown.append("INSTRUMENT_CLASSIFICATION_UNKNOWN")
            evidence_errors["instrument"] = sanitize_error(exc)

    authorization = None
    active = None
    if account:
        permissions = account.get("trdmarket_auth", account.get("market_auth"))
        if isinstance(permissions, (list, tuple, set, frozenset)):
            authorization = market in permissions
        if account.get("acc_status") in {"ACTIVE", "DISABLED"}:
            active = account["acc_status"] == "ACTIVE"
    if authorization is False:
        reasons.append("MARKET_NOT_AUTHORIZED")
    elif authorization is None:
        unknown.append("MARKET_AUTHORIZATION_UNKNOWN")
    if active is False:
        reasons.append("ACCOUNT_DISABLED")
    elif active is None:
        unknown.append("ACCOUNT_STATUS_UNKNOWN")

    order_types = _documented_types(market, classification, environment, account or {})
    if order_types is None:
        unknown.append("PRODUCT_SUPPORT_UNKNOWN")
    sessions = None
    if market == "US" and classification in {"STOCK", "ETF"}:
        sessions = ["RTH"]
        if environment == "REAL":
            sessions += ["ETH", "OVERNIGHT", "ALL"]
        elif account and account.get("sim_acc_type") == "STOCK_AND_OPTION":
            sessions.append("ETH")

    policy = service.policy
    mode_allowed = policy.mode is TradingMode.REAL or (
        policy.mode is TradingMode.SIMULATE and environment == "SIMULATE"
    )
    policy_reasons = []
    if not mode_allowed:
        policy_reasons.append("TRADING_MODE_DISALLOWS_WRITE")
    account_allowlisted: bool | None = None
    if resolved_id is not None:
        if environment == "REAL":
            account_allowlisted = resolved_id in policy.real_acc_ids
        else:
            account_allowlisted = (
                resolved_id in service.paper.allowlist if service.paper else False
            )
        if not account_allowlisted:
            policy_reasons.append("ACCOUNT_NOT_ALLOWLISTED")
    symbol_allowlist = getattr(policy, "symbol_allowlist", frozenset())
    if symbol_allowlist and normalized_code not in symbol_allowlist:
        policy_reasons.append("SYMBOL_NOT_ALLOWLISTED")
    executor_scope = environment != "SIMULATE" or (
        market == "US" and classification in {"STOCK", "ETF"}
    )
    if not executor_scope:
        policy_reasons.append("OUTSIDE_PAPER_EXECUTOR_SCOPE")
    writes_allowed = mode_allowed and account_allowlisted is True and executor_scope
    client_writes_allowed = permission_allowed(environment.lower() + "_write") and (
        resolved_id is None
        or account_access_allowed(environment, resolved_id, write=True)
    )
    if not client_writes_allowed:
        policy_reasons.append("CLIENT_WRITE_NOT_AUTHORIZED")
        writes_allowed = False
    if "SYMBOL_NOT_ALLOWLISTED" in policy_reasons:
        writes_allowed = False

    executor = service.paper if environment == "SIMULATE" else service.real
    journal_state = "DISABLED"
    if executor is not None:
        try:
            journal_state = executor.health().get("state", "UNKNOWN")
        except Exception as exc:  # noqa: BLE001 - a failed probe blocks readiness
            journal_state = "UNKNOWN"
            evidence_errors["journal"] = sanitize_error(exc)
    if journal_state != "READY":
        reasons.append("EXECUTION_JOURNAL_" + journal_state)
    execution_halted = (
        environment == "REAL" and service.execution_state["execution_halted"]
    )
    if execution_halted:
        reasons.append("EXECUTION_HALTED")
    if environment == "REAL" and not service.has_trade_credential:
        unknown.append("GATEWAY_UNLOCK_NOT_OBSERVED")
    require_approval = bool(getattr(policy, "require_approval", False))
    daily_caps = dict(getattr(policy, "max_daily_notional", {}))
    controls_required = require_approval or bool(daily_caps)
    controls_state = "DISABLED"
    operator_paused = False
    controls = getattr(service, "trading_controls", None)
    if controls is None:
        if controls_required:
            reasons.append("TRADING_CONTROLS_UNAVAILABLE")
    else:
        try:
            controls_state = controls.health().get("state", "UNKNOWN")
            pause = controls.pause_state(environment, str(resolved_id or "0"))
            operator_paused = pause.get("paused") is True
        except Exception as exc:  # noqa: BLE001 - unavailable controls block
            controls_state = "UNKNOWN"
            evidence_errors["controls"] = sanitize_error(exc)
        if controls_state != "READY":
            reasons.append("TRADING_CONTROLS_" + controls_state)
        if operator_paused:
            reasons.append("OPERATOR_PAUSED")

    blocked = reasons + policy_reasons
    firm = account.get("security_firm") if account else None
    fees = (
        False
        if environment == "SIMULATE" or firm == "FUTUCA"
        else (True if firm and firm != "N/A" else None)
    )
    return {
        "code": normalized_code,
        "classification": classification,
        "trd_env": environment,
        "acc_id": resolved_id,
        "checked_at": utc_now_iso(),
        "supported": {
            "placement": False
            if authorization is False or active is False
            else (
                True if order_types is not None and authorization and active else None
            ),
            "order_types": order_types,
            "time_in_force": ["DAY"] if environment == "SIMULATE" else ["DAY", "GTC"],
            "sessions": sessions,
            "session_conditions": {
                "ALL_OVERNIGHT": "US equities, NORMAL limit orders, DAY/GTC only",
                "paper_ETH": "US margin paper STOCK_AND_OPTION accounts only",
            },
            "order_events": True,
            "deal_events": environment == "REAL",
            "deal_queries": environment == "REAL",
            "order_fees": fees,
        },
        "allowed": {
            "writes": writes_allowed,
            "client_writes": client_writes_allowed,
            "trading_mode": policy.mode.value,
            "account_allowlisted": account_allowlisted,
            "order_types": ["NORMAL"] if environment == "SIMULATE" else order_types,
            "time_in_force": ["DAY"] if environment == "SIMULATE" else ["DAY", "GTC"],
            "sessions": ["RTH"] if environment == "SIMULATE" else sessions,
            "symbol_allowlist": sorted(symbol_allowlist),
            "max_order_qty": policy.max_order_qty,
            "max_order_notional": dict(policy.max_order_notional),
            "max_daily_notional": daily_caps,
            "require_approval": require_approval,
            "reasons": policy_reasons,
        },
        "ready": {
            "state": "BLOCKED" if blocked else "UNKNOWN" if unknown else "READY",
            "reasons": blocked + unknown,
            "journal_state": journal_state,
            "execution_halted": execution_halted,
            "unlock_strategy": "NOT_REQUIRED"
            if environment == "SIMULATE"
            else (
                "JUST_IN_TIME" if service.has_trade_credential else "OPERATOR_MANAGED"
            ),
            "controls_required": controls_required,
            "controls_state": controls_state,
            "operator_paused": operator_paused,
            "broker_acceptance_verified": False,
        },
        "submission_policy": {
            "local_rth_submission_time_gate": False,
            "session_controls": "EXECUTION_ELIGIBILITY",
            "broker_acceptance": "UNKNOWN",
            "day_validity": "BROKER_DEPENDENT",
            "fills": "UNKNOWN",
            "local_scheduling": False,
        },
        "evidence_errors": evidence_errors,
        "limitations": [
            "Support is documented and conditional on account/product entitlement.",
            "Readiness is advisory; order details and approval are checked again.",
            "RTH controls execution eligibility; authorized outside-RTH submission "
            "has no local market-open gate. Broker acceptance, DAY validity and "
            "fills remain unknown. No future submission is scheduled.",
            "Market authority does not establish short selling or option permissions.",
            "Callbacks are ephemeral observations, never a recovery ledger.",
        ],
        "sources": list(SOURCES),
    }
