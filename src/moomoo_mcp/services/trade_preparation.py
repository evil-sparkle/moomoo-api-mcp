"""Read-only previews and the shared prepared-request mutation guard."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING

from moomoo import Session

from moomoo_mcp.services.execution_identity import validate_token
from moomoo_mcp.services.instrument_rules import (
    instrument_rule_summary,
    normalize_order_session,
    validate_instrument_order,
    whole_quantity,
)
from moomoo_mcp.services.trading_controls import TradingControlError, contract_view
from moomoo_mcp.services.trading_policy import OrderFacts, TradingMode
from moomoo_mcp.services.validation import (
    validate_order_values,
    validate_required_order_fields,
)

if TYPE_CHECKING:
    from moomoo_mcp.services.trade_service import TradeService

MUTATIONS = frozenset(
    {"place_order", "place_combo_order", "modify_order", "cancel_order"}
)


def bound_parameters(service: TradeService, method: str, parameters: dict) -> dict:
    if method not in MUTATIONS:
        raise ValueError("Preparation supports individual trading mutations only")
    if "approval_id" in parameters:
        raise ValueError("A new prepared request cannot supply approval_id")
    bound = inspect.signature(getattr(service, method)).bind(**parameters)
    bound.apply_defaults()
    result = deepcopy(dict(bound.arguments))
    result.pop("approval_id", None)
    result["trd_env"] = str(result["trd_env"]).strip().upper()
    if method == "modify_order":
        result["modify_order_op"] = str(result["modify_order_op"]).strip().upper()
    if method == "place_order":
        selected = normalize_order_session(
            result.get("session", Session.NONE), trd_env=result["trd_env"]
        )
        result["session"] = selected if result["trd_env"] == "REAL" else Session.NONE
    return result


def _single_facts(
    service: TradeService, parameters: dict, *, operation: str
) -> OrderFacts:
    price = parameters["price"]
    numeric = float(price)
    qty = whole_quantity(operation, parameters["qty"])
    validate_order_values(
        operation,
        order_type=parameters["order_type"],
        qty=qty,
        price=numeric,
        aux_price=parameters.get("aux_price"),
        trail_value=parameters.get("trail_value"),
        trail_spread=parameters.get("trail_spread"),
    )
    validate_required_order_fields(
        operation,
        order_type=parameters["order_type"],
        aux_price=parameters.get("aux_price"),
        trail_type=parameters.get("trail_type"),
        trail_value=parameters.get("trail_value"),
    )
    facts = service._single_leg_facts(
        operation,
        code=parameters["code"],
        order_type=parameters["order_type"],
        trd_side=parameters["trd_side"],
        qty=qty,
        price=numeric,
        aux_price=parameters.get("aux_price"),
        session=parameters.get("session", Session.NONE),
    )
    validate_instrument_order(
        operation,
        instrument=facts.legs[0].instrument,
        qty=qty,
        order_type=parameters["order_type"],
        price=price,
        aux_price=parameters.get("aux_price"),
    )
    if parameters["trd_side"] not in {"BUY", "SELL"}:
        raise ValueError("trd_side must be BUY or SELL")
    if parameters.get("time_in_force", "DAY") not in {"DAY", "GTC"}:
        raise ValueError("Only DAY and GTC are supported for these stock orders")
    service.policy.assess_order(operation, facts)
    return facts


def single_preview(service: TradeService, parameters: dict) -> dict:
    """Maximum quantities are observations, not guaranteed execution/margin."""
    environment, account = service.resolve_read_account(
        parameters["trd_env"], parameters.get("acc_id", "0")
    )
    parameters = parameters | {"trd_env": environment, "acc_id": str(account)}
    parameters["session"] = normalize_order_session(
        parameters.get("session", Session.NONE), trd_env=environment
    )
    facts = _single_facts(service, parameters, operation="preview_order")
    valuation = None
    valuation_error = None
    try:
        valuation = service.policy.estimate_order("preview_order", facts)
    except ValueError as exc:
        valuation_error = str(exc)
    except RuntimeError as exc:
        valuation_error = str(exc)
    maximum = service.get_max_tradable(
        order_type=parameters["order_type"],
        code=parameters["code"],
        price=float(parameters["price"]),
        adjust_limit=parameters.get("adjust_limit", 0),
        trd_env=environment,
        acc_id=str(account),
        session=parameters["session"],
    )
    return {
        "acc_id": str(account),
        "trd_env": environment,
        "intent": parameters,
        "contract": contract_view(facts),
        "instrument_rules": instrument_rule_summary(
            facts.legs[0].instrument, parameters["price"]
        ),
        "valuation": valuation,
        "valuation_error": valuation_error,
        "maximum_quantities": maximum,
        "writes_allowed_by_mode": service.policy.mode is not TradingMode.READ_ONLY
        and (environment == "SIMULATE" or service.policy.mode is TradingMode.REAL),
        "limitations": [
            "Read-only observations do not guarantee acceptance or fills.",
            "Derivative margin fields are not a universal stock "
            "account-impact simulation.",
            "Actual order fees are available after submission for supported "
            "REAL accounts.",
        ],
    }


def prepare_request(
    service: TradeService, method: str, parameters: dict, *, ttl_seconds: int
) -> dict:
    controls = service.trading_controls
    if controls is None:
        raise TradingControlError("Durable trading controls are not configured")
    request = bound_parameters(service, method, parameters)
    validate_preparation(service, method, request)
    environment = request["trd_env"]

    market = None
    if method == "place_order":
        market = service._get_market_from_code(request["code"])
    elif method == "place_combo_order":
        legs = service._build_combo_legs(request["combo_legs"])
        market = service._get_market_from_code(legs[0].code)
    account = service._resolve_account(environment, market, request["acc_id"])
    request["acc_id"] = str(account)
    return _prepare_review(service, method, request, ttl_seconds=ttl_seconds)


def validate_preparation(service: TradeService, method: str, request: dict) -> None:
    """Validate local controls, identity and executor scope before broker reads."""
    controls = service.trading_controls
    if controls is None or controls.health()["state"] != "READY":
        raise TradingControlError("Durable trading controls are unavailable")
    environment = request["trd_env"]
    service.policy.check_write(method, environment)
    validate_token(request.get("operation_id"), request.get("admission_epoch"))
    executor = service.real if environment == "REAL" else service.paper
    if executor is None or request["admission_epoch"] != executor.store.epoch:
        raise TradingControlError(
            "Preparation requires the current environment journal epoch"
        )
    if executor.store.lookup(request["operation_id"]) is not None:
        raise TradingControlError(
            "Operation is already admitted; inspect or retry its journal result"
        )
    if environment == "REAL":
        assert service.real is not None
        service.real._validate_values(method, request)
    else:
        assert service.paper is not None
        from moomoo_mcp.services.admission import _paper_parameters

        kind, paper_request = _paper_parameters(method, request)
        service.paper._schema(kind, paper_request)


def _prepare_review(
    service: TradeService,
    method: str,
    request: dict,
    *,
    ttl_seconds: int,
) -> dict:
    controls = service.trading_controls
    assert controls is not None
    environment = request["trd_env"]
    account = int(request["acc_id"])
    review: dict = {}
    if method == "place_order":
        if environment == "SIMULATE":
            assert service.paper is not None
            from moomoo_mcp.services.admission import _paper_parameters

            kind, paper_request = _paper_parameters(method, request)
            service.paper._schema(kind, paper_request)
        review = single_preview(service, request)
    elif method == "place_combo_order":
        if environment != "REAL":
            raise TradingControlError("Paper combo execution is unsupported")
        if request["order_type"] not in {"NORMAL", "MARKET"}:
            raise ValueError("Combo submissions support NORMAL and MARKET only")
        if request["time_in_force"] not in {"DAY", "GTC"}:
            raise ValueError("Combo submissions support DAY and GTC only")
        facts = service._combo_facts(
            "prepare combo",
            service._build_combo_legs(request["combo_legs"]),
            price=request["price"],
            qty=request["qty"],
            order_type=request["order_type"],
        )
        service.policy.assess_order("prepare combo", facts)
        preview_params = {
            key: request[key]
            for key in ("combo_legs", "price", "qty", "order_type", "trd_env", "acc_id")
        }
        review = {
            "contract": contract_view(facts),
            "preview": service.preview_combo_order(**preview_params),
        }
    else:
        target = service._fetch_order(
            "prepare mutation", request["order_id"], environment, account
        )
        review["target"] = {
            key: target.get(key)
            for key in (
                "order_id",
                "code",
                "trd_side",
                "order_type",
                "qty",
                "price",
                "order_status",
            )
        }
        if method == "modify_order" and request["modify_order_op"] in {
            "NORMAL",
            "ENABLE",
        }:
            effective = request | {
                "code": target["code"],
                "trd_side": target["trd_side"],
                "order_type": target["order_type"],
                "qty": request["qty"] if request["qty"] is not None else target["qty"],
                "price": request["price"]
                if request["price"] is not None
                else target["price"],
                "aux_price": service._optional_float(target.get("aux_price")),
            }
            review["contract"] = contract_view(
                _single_facts(service, effective, operation="prepare modification")
            )
    return controls.prepare(method, request, review, ttl_seconds=ttl_seconds)


@contextmanager
def mutation_context(
    service: TradeService,
    method: str,
    parameters: dict,
    approval_id: str | None,
) -> Iterator[None]:
    """The outer request survives internal account fixing and correlation tags."""
    previous = getattr(service._control_local, "operation", None)
    if previous is not None:
        yield
        return
    service._control_local.operation = {
        "method": method,
        "parameters": deepcopy(parameters),
        "approval_id": approval_id,
        "contract": None,
        "valuation": None,
    }
    try:
        yield
    finally:
        del service._control_local.operation


def assess_control_facts(
    service: TradeService, operation: str, facts: OrderFacts
) -> None:
    service.policy.assess_order(operation, facts)
    active = getattr(service._control_local, "operation", None)
    if active is not None:
        active["contract"] = contract_view(facts)
        if service.policy.max_daily_notional:
            active["valuation"] = service.policy.estimate_order(operation, facts)


def check_dispatch_controls(
    service: TradeService, account: int, environment: str
) -> None:
    """Commit the conservative charge and decision consumption before unlock."""
    policy = service.policy
    if not policy.require_approval and not policy.max_daily_notional:
        return
    controls = service.trading_controls
    active = getattr(service._control_local, "operation", None)
    if controls is None or active is None:
        raise TradingControlError("Trading controls have no active prepared execution")
    parameters = dict(active["parameters"])
    supplied = str(parameters["acc_id"])
    if policy.require_approval and supplied != str(account):
        raise TradingControlError(
            "Approved execution requires the explicit prepared account"
        )
    parameters["acc_id"] = str(account)
    parameters["trd_env"] = environment
    valuation = active["valuation"]
    adds_exposure = active["method"] in {"place_order", "place_combo_order"} or (
        active["method"] == "modify_order"
        and str(parameters["modify_order_op"]).strip().upper() in {"NORMAL", "ENABLE"}
    )
    if policy.max_daily_notional and adds_exposure and valuation is None:
        raise TradingControlError("Daily budget requires a fresh full-order valuation")
    controls.consume(
        active["method"],
        parameters,
        approval_id=active["approval_id"],
        require_approval=policy.require_approval,
        currency=valuation["currency"] if valuation else None,
        amount=Decimal(valuation["amount"]) if valuation else Decimal(0),
        caps=policy.max_daily_notional,
        contract=active["contract"],
    )
    active["reserved_parameters"] = parameters


def confirm_dispatch_controls(service: TradeService) -> None:
    policy = service.policy
    if not policy.require_approval and not policy.max_daily_notional:
        return
    active = getattr(service._control_local, "operation", None)
    controls = service.trading_controls
    if active is None or controls is None or "reserved_parameters" not in active:
        raise TradingControlError("Trading controls have no committed reservation")
    controls.confirm_reservation(
        active["method"],
        active["reserved_parameters"],
        approval_id=active["approval_id"],
        require_approval=policy.require_approval,
        contract=active["contract"],
    )
