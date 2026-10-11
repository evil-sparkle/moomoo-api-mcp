"""Documented support never substitutes for deployment or broker permission."""

from typing import cast
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import RET_ERROR, RET_OK

from moomoo_mcp.services.paper_execution import PaperExecution
from moomoo_mcp.services.real_execution import RealExecution
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import (
    InstrumentFacts,
    TradingMode,
    TradingPolicy,
)

ACCOUNT = 987654321098765431


def mock_context(service: TradeService) -> MagicMock:
    return cast(MagicMock, service.trade_ctx)


def configured_service(
    *,
    environment="SIMULATE",
    mode=TradingMode.SIMULATE,
    code="US.AAPL",
    classification="STOCK",
    account_overrides=None,
):
    service = TradeService(
        policy=TradingPolicy(mode, real_acc_ids=frozenset({ACCOUNT})),
        trade_password="test-only-trade-password" if environment == "REAL" else None,
    )
    account = {
        "acc_id": ACCOUNT,
        "trd_env": environment,
        "trdmarket_auth": [code.partition(".")[0]],
        "acc_status": "ACTIVE",
        "sim_acc_type": "STOCK_AND_OPTION",
        "security_firm": "FUTUSG",
    }
    account.update(account_overrides or {})
    service.trade_ctx = MagicMock()
    service.trade_ctx.get_acc_list.return_value = RET_OK, pd.DataFrame([account])
    service.instrument_lookup = MagicMock(
        return_value=[InstrumentFacts(code=code, classification=classification)]
    )
    executor = MagicMock(
        spec=PaperExecution if environment == "SIMULATE" else RealExecution
    )
    executor.health.return_value = {"state": "READY"}
    if environment == "SIMULATE":
        executor.allowlist = frozenset({ACCOUNT})
        service.paper = executor
    else:
        service.real = executor
    return service


def test_paper_broker_support_is_separate_from_executor_and_no_mutation():
    service = configured_service()

    result = service.get_trading_capabilities("US.AAPL", "SIMULATE", str(ACCOUNT))

    assert result["acc_id"] == ACCOUNT
    assert result["supported"]["order_types"] == ["NORMAL", "MARKET"]
    assert result["supported"]["sessions"] == ["RTH", "ETH"]
    assert result["allowed"]["order_types"] == ["NORMAL"]
    assert result["allowed"]["sessions"] == ["RTH"]
    assert result["allowed"]["time_in_force"] == ["DAY"]
    assert result["allowed"]["writes"] is True
    assert result["ready"]["state"] == "READY"
    assert result["ready"]["broker_acceptance_verified"] is False
    for feature in ("deal_events", "deal_queries", "order_fees"):
        assert result["supported"][feature] is False
    mock_context(service).unlock_trade.assert_not_called()
    mock_context(service).place_order.assert_not_called()
    mock_context(service).modify_order.assert_not_called()


def test_read_only_reports_broker_support_without_write_permission():
    service = configured_service(mode=TradingMode.READ_ONLY)
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["supported"]["placement"] is True
    assert result["allowed"]["writes"] is False
    assert "TRADING_MODE_DISALLOWS_WRITE" in result["ready"]["reasons"]


@pytest.mark.parametrize(
    ("code", "classification", "expected"),
    [
        ("US.AAPL", "STOCK", "MARKET"),
        ("HK.00700", "STOCK", "AUCTION_LIMIT"),
        ("HK.HSI261230C20000", "DRVT", "STOP_LIMIT"),
        ("HKCC.600000", "STOCK", "LIMIT_IF_TOUCHED"),
    ],
)
def test_documented_product_registry(code, classification, expected):
    service = configured_service(
        environment="REAL",
        mode=TradingMode.REAL,
        code=code,
        classification=classification,
    )
    result = service.get_trading_capabilities(code, "REAL")
    assert expected in result["supported"]["order_types"]
    if classification == "DRVT" or code.startswith("HKCC."):
        assert "MARKET" not in result["supported"]["order_types"]
    assert result["ready"]["state"] == "READY"


def test_unverified_product_remains_unknown():
    service = configured_service(
        environment="REAL", mode=TradingMode.REAL, code="JP.8058"
    )
    result = service.get_trading_capabilities("JP.8058", "REAL")
    assert result["supported"]["order_types"] is None
    assert result["supported"]["placement"] is None
    assert result["ready"]["state"] == "UNKNOWN"
    assert "PRODUCT_SUPPORT_UNKNOWN" in result["ready"]["reasons"]


def test_unverified_paper_market_does_not_inherit_generic_order_support():
    service = configured_service(code="JP.8058")
    result = service.get_trading_capabilities("JP.8058", "SIMULATE")
    assert result["supported"]["order_types"] is None
    assert result["supported"]["placement"] is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"trdmarket_auth": ["HK"]}, "MARKET_NOT_AUTHORIZED"),
        ({"acc_status": "DISABLED"}, "ACCOUNT_DISABLED"),
    ],
)
def test_known_account_restrictions_block_readiness(overrides, reason):
    service = configured_service(account_overrides=overrides)
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["supported"]["placement"] is False
    assert result["ready"]["state"] == "BLOCKED"
    assert reason in result["ready"]["reasons"]


def test_unknown_account_permission_is_not_inferred():
    service = configured_service(account_overrides={"trdmarket_auth": None})
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["supported"]["placement"] is None
    assert result["ready"]["state"] == "UNKNOWN"


def test_missing_discovery_reports_missing_evidence():
    service = configured_service()
    mock_context(service).get_acc_list.return_value = RET_ERROR, "Gateway unavailable"
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE", str(ACCOUNT))
    assert result["ready"]["state"] == "BLOCKED"
    assert "ACCOUNT_DISCOVERY_UNAVAILABLE" in result["ready"]["reasons"]
    assert result["supported"]["placement"] is None


def test_zero_account_is_never_arbitrarily_selected():
    service = configured_service()
    mock_context(service).get_acc_list.return_value = (
        RET_OK,
        pd.DataFrame(
            [
                {"acc_id": ACCOUNT, "trd_env": "SIMULATE"},
                {"acc_id": ACCOUNT + 1, "trd_env": "SIMULATE"},
            ]
        ),
    )
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["acc_id"] is None
    assert "ACCOUNT_AMBIGUOUS" in result["ready"]["reasons"]


def test_real_read_inspection_does_not_hide_unallowlisted_account():
    service = configured_service(environment="REAL", mode=TradingMode.REAL)
    service.policy = TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123}))
    result = service.get_trading_capabilities("US.AAPL", "REAL", str(ACCOUNT))
    assert result["supported"]["placement"] is True
    assert result["allowed"]["account_allowlisted"] is False
    assert result["allowed"]["writes"] is False


def test_environment_owns_its_independent_journal():
    service = configured_service(environment="REAL", mode=TradingMode.REAL)
    cast(MagicMock, service.real).health.return_value = {"state": "JOURNAL_BLOCKED"}
    service.paper = MagicMock(spec=PaperExecution)
    service.paper.health.return_value = {"state": "READY"}
    result = service.get_trading_capabilities("US.AAPL", "REAL")
    assert result["ready"]["journal_state"] == "JOURNAL_BLOCKED"
    assert result["ready"]["state"] == "BLOCKED"
    service.paper.health.assert_not_called()


def test_real_halt_and_unobserved_manual_unlock_are_reported():
    service = configured_service(environment="REAL", mode=TradingMode.REAL)
    service.trade_password = None
    result = service.get_trading_capabilities("US.AAPL", "REAL")
    assert result["ready"]["state"] == "UNKNOWN"
    assert result["ready"]["unlock_strategy"] == "OPERATOR_MANAGED"
    service._execution.record_relock_failure("test relock failure")
    result = service.get_trading_capabilities("US.AAPL", "REAL")
    assert result["ready"]["state"] == "BLOCKED"
    assert result["ready"]["execution_halted"] is True


def test_canadian_fees_and_paper_option_executor_are_restricted():
    service = configured_service(
        environment="REAL",
        mode=TradingMode.REAL,
        account_overrides={"security_firm": "FUTUCA"},
    )
    assert (
        service.get_trading_capabilities("US.AAPL", "REAL")["supported"]["order_fees"]
        is False
    )
    paper = configured_service(code="US.AAPL261230C200000", classification="DRVT")
    result = paper.get_trading_capabilities("US.AAPL261230C200000", "SIMULATE")
    assert result["supported"]["order_types"] == ["NORMAL", "MARKET"]
    assert result["allowed"]["writes"] is False
    assert "OUTSIDE_PAPER_EXECUTOR_SCOPE" in result["ready"]["reasons"]


def test_optional_symbols_approval_and_controls_are_visible():
    service = configured_service()
    service.policy = TradingPolicy(
        TradingMode.SIMULATE,
        symbol_allowlist=frozenset({"US.MSFT"}),
        require_approval=True,
        max_daily_notional={"USD": 100},
    )
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["allowed"]["require_approval"] is True
    assert result["allowed"]["max_daily_notional"] == {"USD": 100}
    assert result["allowed"]["writes"] is False
    assert "SYMBOL_NOT_ALLOWLISTED" in result["ready"]["reasons"]
    assert "TRADING_CONTROLS_UNAVAILABLE" in result["ready"]["reasons"]


def test_controls_health_remains_separate_from_permission():
    service = configured_service()
    service.policy = TradingPolicy(TradingMode.SIMULATE, require_approval=True)
    controls = MagicMock()
    controls.health.return_value = {"state": "BLOCKED"}
    service.trading_controls = controls
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["allowed"]["writes"] is True
    assert result["ready"]["state"] == "BLOCKED"
    assert result["ready"]["controls_state"] == "BLOCKED"
    controls.health.return_value = {"state": "READY"}
    result = service.get_trading_capabilities("US.AAPL", "SIMULATE")
    assert result["ready"]["state"] == "READY"
    assert result["ready"]["broker_acceptance_verified"] is False
