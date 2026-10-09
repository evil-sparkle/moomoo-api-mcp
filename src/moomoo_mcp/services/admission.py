"""Prepare broker permits for synchronous service calls made by async MCP tools.

Preparation performs validation and account discovery, but never an order write.
The final per-SDK gate remains authoritative for all internal and direct calls.
"""

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from moomoo_mcp.services.broker_dispatch import QuotaRequest
from moomoo_mcp.services.execution_identity import validate_token
from moomoo_mcp.services.order_errors import not_sent
from moomoo_mcp.services.validation import (
    validate_choice,
    validate_date_range,
    validate_order_values,
    validate_required_order_fields,
)

if TYPE_CHECKING:
    from moomoo_mcp.services.market_data_service import MarketDataService
    from moomoo_mcp.services.paper_execution import PaperExecution
    from moomoo_mcp.services.trade_service import TradeService


@dataclass
class AdmissionPlan:
    requests: list[QuotaRequest] = field(default_factory=list)
    write_operation: str | None = None


def recover_paper_admission(
    service: "TradeService", method: str, params: dict[str, Any]
) -> dict | None:
    """Read a concurrent journal admission after a quota wait, without replay."""
    paper = service.paper
    if paper is None or str(params.get("trd_env", "")).strip().upper() != "SIMULATE":
        return None
    with not_sent(method):
        service.policy.check_write(method, "SIMULATE")
        kind, request_params = _paper_parameters(method, params)
        validate_token(params["operation_id"], params["admission_epoch"])
        paper._schema(kind, request_params)
        row = (
            paper._late_identity[params["operation_id"]]
            if params["operation_id"] in paper._late_failure
            else paper.store.lookup(params["operation_id"])
        )
        if row is not None:
            return paper._retry(row, kind, request_params, params["acc_id"])
    return None


def prepare_market_call(
    service: "MarketDataService", method: str, params: dict[str, Any]
) -> AdmissionPlan:
    from moomoo_mcp.services.market_data_service import (
        TRADING_DAY_MARKETS,
        validate_candle_filters,
    )

    operations = {
        "get_historical_klines": "request_history_kline",
        "get_historical_klines_page": "request_history_kline",
        "get_option_expiration_date": "get_option_expiration_date",
        "get_market_snapshot": "get_market_snapshot",
        "get_market_state": "get_market_state",
        "get_trading_days": "request_trading_days",
        "get_user_security_group": "get_user_security_group",
        "get_user_security": "get_user_security",
    }
    operation = operations.get(method)
    if operation is None:
        return AdmissionPlan()
    if service.quote_ctx is None:
        raise RuntimeError("Quote context not connected")
    if operation == "get_market_snapshot" and not params["codes"]:
        return AdmissionPlan()
    if operation == "request_history_kline":
        validate_candle_filters(params["ktype"], params["autype"])
    elif operation == "get_option_expiration_date":
        if not str(params["code"] or "").strip():
            raise ValueError("code must be a non-empty security code, e.g. 'US.AAPL'.")
    elif operation == "get_market_state":
        if not params["codes"]:
            raise ValueError("codes must contain at least one security code.")
        if not all(str(code or "").strip() for code in params["codes"]):
            raise ValueError("codes must not contain empty security codes.")
    elif operation == "request_trading_days":
        validate_choice("market", params["market"], TRADING_DAY_MARKETS)
        validate_date_range(params["start"], params["end"])
    request = service.dispatcher.request(operation, **params)
    return AdmissionPlan([request] if request is not None else [])


def _paper_parameters(method: str, params: dict[str, Any]) -> tuple[str, dict]:
    if method == "place_order":
        fields = (
            "code",
            "price",
            "qty",
            "trd_side",
            "order_type",
            "time_in_force",
            "adjust_limit",
            "aux_price",
            "trail_type",
            "trail_value",
            "trail_spread",
            "remark",
        )
        return "PLACE", {name: params[name] for name in fields}
    operation = params.get("modify_order_op", "CANCEL")
    if operation not in {"NORMAL", "CANCEL"}:
        raise ValueError(
            "Paper execution supports modifications and cancellations only"
        )
    result = {"order_id": params["order_id"]}
    if operation == "NORMAL":
        result["adjust_limit"] = params["adjust_limit"]
        for name in ("qty", "price"):
            if params[name] is not None:
                result[name] = params[name]
    elif method == "modify_order" and (
        params["qty"] is not None
        or params["price"] is not None
        or params["adjust_limit"] != 0
    ):
        raise ValueError("Paper cancellation does not accept a price or quantity patch")
    return "MODIFY" if operation == "NORMAL" else "CANCEL", result


def prepare_trade_call(
    service: "TradeService", method: str, params: dict[str, Any]
) -> AdmissionPlan:
    reads = {
        "get_assets": "accinfo_query",
        "get_positions": "position_list_query",
        "get_orders": "order_list_query",
        "get_deals": "deal_list_query",
        "get_history_orders": "history_order_list_query",
        "get_history_deals": "history_deal_list_query",
        "get_max_tradable": "acctradinginfo_query",
        "get_cash_flow": "get_acc_cash_flow",
        "get_margin_ratio": "get_margin_ratio",
        "preview_combo_order": "comboorder_tradinginfo_query",
        "unlock_trade": "unlock_trade",
        "lock_trade": "unlock_trade",
    }
    writes = {"place_order", "place_combo_order", "modify_order", "cancel_order"}
    if method not in reads and method not in writes:
        return AdmissionPlan()
    plan = AdmissionPlan(write_operation=method if method in writes else None)
    boundary = not_sent(method) if plan.write_operation else nullcontext()
    with boundary:
        if method in writes:
            service.policy.check_write(method, params["trd_env"])
        if method == "unlock_trade":
            service.policy.check_unlock()
            if service.has_trade_credential:
                raise ValueError("Stored trade credentials require managed unlocking")
            if not params["password"] and not params["password_md5"]:
                raise ValueError("unlock_trade requires a password or password_md5")
        # A journal retry returns its stored outcome even while disconnected.
        if service.trade_ctx is None and (
            method not in writes or str(params["trd_env"]).strip().upper() != "SIMULATE"
        ):
            raise RuntimeError("Trade context not connected")
        if method in {"get_orders", "get_history_orders"}:
            service._convert_status_filter(params["status_filter_list"])
        account = None
        if method in writes:
            environment = str(params["trd_env"]).strip().upper()
            paper = service.paper if environment == "SIMULATE" else None
            if environment == "SIMULATE":
                if method == "place_combo_order":
                    raise ValueError(
                        "Combo orders are outside version 1 paper execution scope"
                    )
                if paper is None:
                    raise ValueError("Persistent paper journal is not configured")
                kind, request_params = _paper_parameters(method, params)
                validate_token(params["operation_id"], params["admission_epoch"])
                paper._schema(kind, request_params)
                operation_id = params["operation_id"]
                if operation_id in paper._late_failure or paper.store.lookup(
                    operation_id
                ):
                    # Existing journal tokens return stored outcomes without new
                    # quota or replay; the service still validates the identity.
                    return plan
                if params["admission_epoch"] != paper.store.epoch:
                    raise ValueError(
                        "Unknown non-current-epoch token cannot be accounted for"
                    )
                paper.ready()
                account = paper._account(params["acc_id"])
                plan.requests.append(QuotaRequest("get_market_snapshot"))
                if kind != "PLACE":
                    plan.requests.append(QuotaRequest("order_list_query", account))
            else:
                market = None
                exposing = method in {"place_order", "place_combo_order"} or (
                    method == "modify_order"
                    and str(params["modify_order_op"]).strip().upper()
                    in {"NORMAL", "ENABLE"}
                )
                if method == "place_order":
                    if isinstance(params["price"], str):
                        raise ValueError("REAL price must be numeric")
                    validate_order_values(
                        method,
                        order_type=params["order_type"],
                        qty=params["qty"],
                        price=params["price"],
                        aux_price=params["aux_price"],
                        trail_value=params["trail_value"],
                        trail_spread=params["trail_spread"],
                    )
                    validate_required_order_fields(
                        method,
                        order_type=params["order_type"],
                        aux_price=params["aux_price"],
                        trail_type=params["trail_type"],
                        trail_value=params["trail_value"],
                    )
                    market = service._get_market_from_code(params["code"])
                elif method == "place_combo_order":
                    validate_order_values(
                        method,
                        order_type=params["order_type"],
                        qty=params["qty"],
                        combo_price=params["price"],
                    )
                    legs = service._build_combo_legs(params["combo_legs"])
                    market = service._get_market_from_code(legs[0].code)
                if exposing:
                    service._check_execution_halt(method, environment)
                account = service._resolve_account(
                    environment, market, params["acc_id"]
                )
                if exposing and service.policy.notional_cap_configured:
                    plan.requests.append(QuotaRequest("get_market_snapshot"))
                if method == "modify_order" and exposing:
                    plan.requests.append(QuotaRequest("order_list_query", account))
                if service._uses_jit_unlock(environment):
                    plan.requests.append(QuotaRequest("unlock_trade", weight=2))
            params["acc_id"] = account
            operation = (
                method
                if method in {"place_order", "place_combo_order"}
                else "modify_order"
            )
            plan.requests.append(QuotaRequest(operation, account))
        else:
            operation = reads[method]
            if operation == "comboorder_tradinginfo_query":
                legs = service._build_combo_legs(params["combo_legs"])
                account = service._resolve_account(
                    params["trd_env"],
                    service._get_market_from_code(legs[0].code),
                    params["acc_id"],
                )
                params["acc_id"] = account
            elif "trd_env" in params:
                environment, account = service.resolve_read_account(
                    params["trd_env"], params["acc_id"]
                )
                params["trd_env"], params["acc_id"] = environment, account
            request = service.dispatcher.request(operation, **params)
            if request is not None:
                plan.requests.append(request)
    return plan


def prepare_paper_call(
    paper: "PaperExecution", method: str, params: dict[str, Any]
) -> AdmissionPlan:
    if method != "reconcile":
        return AdmissionPlan()
    row = paper.store.lookup(params["operation_id"])
    if row is None:
        raise ValueError("Operation is not owned by this journal")
    if paper._late_failure or not any(
        item["operation_id"] == row["operation_id"]
        for item in paper.store.recovery_rows(paper.recovery.clock())
    ):
        # Stored outcomes and checks that are not due need no broker capacity.
        return AdmissionPlan()
    account = int(row["account"])
    return AdmissionPlan(
        [
            QuotaRequest("order_list_query", account),
            QuotaRequest("history_order_list_query", account),
            QuotaRequest("position_list_query", account),
        ]
    )
