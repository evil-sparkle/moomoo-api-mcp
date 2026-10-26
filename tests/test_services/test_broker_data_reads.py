"""Coverage, unknowns, exact SDK mapping and read-only authorization."""

import asyncio
from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import OpenQuoteContext
from pydantic import ValidationError

from moomoo_mcp.client_authorization import ClientGrants, client_grants_context
from moomoo_mcp.server import mcp
from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.data_evidence import (
    MAX_DATA_ROWS,
    OptionChainFilter,
    capability_evidence,
)
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.query_contracts import TradeQueryError
from moomoo_mcp.services.quote_errors import QuotePermissionDeniedError
from moomoo_mcp.services.rate_limit import ProviderRateLimitError
from moomoo_mcp.tools.offload import run_blocking
from tests.mcp_helpers import call_mcp_tool
from tests.rate_limit_clock import FakeClock


@pytest.fixture
def broker():
    clock = FakeClock()
    context = MagicMock(spec=OpenQuoteContext)
    service = MarketDataService(
        context, dispatcher=BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)
    )
    context.get_market_snapshot.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": "US.EXAMPLE",
                    "option_valid": True,
                    "option_contract_multiplier": 10,
                    "option_contract_size": 150,
                    "option_area_type": "AMERICAN",
                    "option_settlement_mode": "N/A",
                    "bid_price": 1.1,
                    "ask_price": 1.3,
                    "bid_vol": 0,
                }
            ]
        ),
    )
    context.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": "US.EXAMPLE",
                    "stock_type": "DRVT",
                    "lot_size": 1,
                    "option_standard_type": "ADJUSTED",
                }
            ]
        ),
    )
    return service, context, clock


def test_snapshot_partial_preserves_zero_and_unknowns(broker):
    service, context, _ = broker
    result = service.get_market_snapshot_evidence(["US.EXAMPLE", "US.MISSING"])
    assert result["coverage"]["missing"] == ["US.MISSING"]
    assert result["coverage"]["complete_response"] is False
    assert result["data"][0]["bid_vol"] == 0
    assert "update_time" not in result["data"][0]
    assert result["delay_status"] == result["entitlement_status"] == "UNKNOWN"
    assert result["source_revision"] is None
    assert result["requested_at"] <= result["received_at"]
    context.subscribe.assert_not_called()


@pytest.mark.parametrize(
    "codes", [[], ["US.X"] * 2, ["US.X"] * 101, [" X"], ["X"], [True]]
)
def test_invalid_snapshot_scope_never_dispatches(broker, codes):
    service, context, _ = broker
    with pytest.raises(ValueError):
        service.get_market_snapshot_evidence(codes)
    context.get_market_snapshot.assert_not_called()


@pytest.mark.parametrize("rows", [[{"code": "US.OTHER"}], [{"code": "US.EXAMPLE"}] * 2])
def test_unexpected_duplicate_snapshots_fail(broker, rows):
    service, context, _ = broker
    context.get_market_snapshot.return_value = (0, pd.DataFrame(rows))
    with pytest.raises(TradeQueryError):
        service.get_market_snapshot_evidence(["US.EXAMPLE"])


def test_facts_reuse_cache_and_separate_economics(broker):
    service, context, _ = broker
    first = service.get_instrument_facts(["US.EXAMPLE"])
    facts = first["data"][0]["facts"]
    assert facts["monetary_multiplier"]["value"] == 10
    assert facts["deliverable_size"]["value"] == 150
    assert facts["lot_size"]["value"] == 1
    assert facts["standard_status"]["value"] == "ADJUSTED"
    for field in ("currency", "settlement_mode", "settlement_cutoff"):
        assert facts[field]["state"] == "UNKNOWN"
    context.get_market_snapshot.return_value[1].loc[0, "option_contract_multiplier"] = 0
    second = service.get_instrument_facts(["US.EXAMPLE"])
    assert second["data"][0]["facts"]["monetary_multiplier"]["value"] is None
    assert second["data"][0]["compatibility_verified"] is False
    assert context.get_market_snapshot.call_count == 2
    assert context.get_stock_basicinfo.call_count == 1


def test_fact_conflict_and_missing_reference_are_explicit(broker):
    service, context, _ = broker
    context.get_stock_basicinfo.return_value[1].loc[0, "option_contract_multiplier"] = (
        100
    )
    result = service.get_instrument_facts(["US.EXAMPLE", "US.MISSING"])
    assert result["data"][0]["facts"]["monetary_multiplier"]["state"] == "CONFLICT"
    assert result["data"][0]["facts"]["monetary_multiplier"]["value"] is None
    assert result["coverage"]["missing"] == ["US.MISSING"]


@pytest.mark.parametrize("used,remain", [(0, 0), (20, 0), (None, None)])
def test_quota_zero_missing_and_no_private_universe(broker, used, remain):
    service, context, _ = broker
    context.get_history_kl_quota.return_value = (
        0,
        (used, remain, [{"code": "PRIVATE.UNIVERSE"}]),
    )
    result = service.get_history_kl_quota()
    assert result["data"] == [{"used_quota": used, "remain_quota": remain}]
    context.get_history_kl_quota.assert_called_once_with(get_detail=False)
    assert "PRIVATE.UNIVERSE" not in str(result)


def test_quota_is_not_a_reservation_or_subscription(broker):
    service, context, _ = broker
    context.get_history_kl_quota.side_effect = [(0, (1, 9, [])), (0, (10, 0, []))]
    assert service.get_history_kl_quota()["data"][0]["remain_quota"] == 9
    assert service.get_history_kl_quota()["data"][0]["remain_quota"] == 0
    context.query_subscription.assert_not_called()


def test_failures_never_become_empty_data(broker):
    service, context, _ = broker
    context.get_rehab.return_value = (-1, "Quote permission denied")
    with pytest.raises(QuotePermissionDeniedError):
        service.get_rehab("US.EXAMPLE")
    context.get_history_kl_quota = None
    with pytest.raises(TradeQueryError) as failure:
        service.get_history_kl_quota()
    assert failure.value.code == "CAPABILITY_UNAVAILABLE"


def test_earnings_timing_and_truncation(broker):
    service, context, _ = broker
    context.get_earnings_calendar.return_value = (
        0,
        pd.DataFrame(
            [
                {"security": "US.X", "pub_type": "BEFORE", "eps_predict": 0},
                {"security": "US.Y", "pub_type": "AFTER"},
                {"security": "US.Z", "pub_type": "N/A"},
            ]
        ),
    )
    result = service.get_earnings_calendar("US", "2026-10-26", "2026-10-27", 2)
    assert result["coverage"]["truncated"] is True
    assert result["coverage"]["total_returned"] == 3
    assert [r["pub_type"] for r in result["data"]] == ["BEFORE", "AFTER"]
    assert result["coverage"]["provider_history_complete"] is None
    with pytest.raises(ValueError):
        service.get_earnings_calendar("US", "2026-10-01", "2026-10-31")
    assert context.get_earnings_calendar.call_count == 1


def test_missing_and_empty_dividends_differ(broker):
    service, context, _ = broker
    context.get_corporate_actions_dividends.return_value = (0, {})
    with pytest.raises(TradeQueryError):
        service.get_corporate_actions_dividends("US.EXAMPLE")
    context.get_corporate_actions_dividends.return_value = (0, {"dividend_list": []})
    assert service.get_corporate_actions_dividends("US.EXAMPLE")["data"] == []


def test_adjustment_factors_and_capacity(broker):
    service, context, _ = broker
    context.get_rehab.return_value = (
        0,
        pd.DataFrame(
            [{"ex_div_date": "2026-10-26", "split_ratio": 0.2, "per_cash_div": 0}]
        ),
    )
    assert service.get_rehab("US.EXAMPLE")["data"][0]["split_ratio"] == 0.2
    context.get_rehab.return_value = (0, pd.DataFrame([{}] * (MAX_DATA_ROWS + 1)))
    with pytest.raises(TradeQueryError) as failure:
        service.get_rehab("US.EXAMPLE")
    assert failure.value.code == "SNAPSHOT_LIMIT"


def test_volatility_scaling_gaps_order_and_lookback(broker):
    service, context, _ = broker
    context.get_option_volatility.return_value = (
        0,
        pd.DataFrame(
            [
                {"timestamp": 3, "implied_volatility": 25, "history_volatility": 30},
                {
                    "timestamp": 1,
                    "implied_volatility": float("nan"),
                    "history_volatility": float("inf"),
                },
            ]
        ),
    )
    result = service.get_option_volatility("US.EXAMPLE", "WEEK", 10)
    assert result["data"][0]["implied_volatility"] == 25
    assert result["data"][1]["implied_volatility"] is None
    assert result["data"][1]["history_volatility"] is None
    assert result["scope"]["hv_days"] == 10
    context.get_option_volatility.assert_called_once_with(
        code="US.EXAMPLE", query_time_period=1, hv_time_period=10
    )
    context.get_market_snapshot.return_value[1].loc[0, "option_valid"] = False
    with pytest.raises(ValueError):
        service.get_option_volatility("US.EXAMPLE")
    assert context.get_option_volatility.call_count == 1


@pytest.mark.parametrize(
    "filters",
    [
        {"delta_min": 0.8, "delta_max": 0.1},
        {"vol_min": -1},
        {"theta_min": float("nan")},
        {"unexpected": 1},
        {"delta_max": True},
    ],
)
def test_invalid_filters_fail(filters):
    with pytest.raises(ValidationError):
        OptionChainFilter.model_validate(filters)


def test_chain_filters_and_legacy_kwargs(broker):
    service, context, _ = broker
    context.get_option_chain.return_value = (0, pd.DataFrame([{"code": "US.EXAMPLE"}]))
    service.get_option_chain("US.X", "2026-10-26", "2026-10-26")
    assert set(context.get_option_chain.call_args.kwargs) == {
        "code",
        "start",
        "end",
        "option_type",
    }
    filters = OptionChainFilter(
        delta_min=-0.5, theta_max=-0.01, implied_volatility_max=80, open_interest_min=10
    )
    service.get_option_chain("US.X", option_cond_type="WITHIN", data_filter=filters)
    kwargs = context.get_option_chain.call_args.kwargs
    assert kwargs["option_cond_type"] == "WITHIN"
    assert kwargs["data_filter"].delta_min == -0.5
    assert kwargs["data_filter"].implied_volatility_max == 80


@pytest.mark.parametrize("field", list(OptionChainFilter.model_fields))
def test_each_selected_filter_maps_to_the_exact_sdk_field(broker, field):
    service, context, _ = broker
    context.get_option_chain.return_value = (0, pd.DataFrame())
    value = 1 if field.startswith(("open_interest_", "vol_")) else 0.1
    filters = OptionChainFilter.model_validate({field: value})
    service.get_option_chain("US.X", data_filter=filters)
    assert (
        getattr(context.get_option_chain.call_args.kwargs["data_filter"], field)
        == value
    )


def test_missing_sdk_filter_support_is_not_silent(broker, monkeypatch):
    from moomoo_mcp.services import market_data_service

    service, context, _ = broker
    monkeypatch.setattr(market_data_service, "OptionDataFilter", lambda: None)
    with pytest.raises(RuntimeError):
        service.get_option_chain("US.X", data_filter=OptionChainFilter(delta_min=0.1))
    context.get_option_chain.assert_not_called()


def test_chain_timeout_is_not_an_argument_or_permission_verdict(broker):
    service, context, _ = broker
    context.get_option_chain.return_value = (-1, "Get Option Chain request timed out.")
    with pytest.raises(RuntimeError) as failure:
        service.get_option_chain("US.SPY", "2026-10-30", "2026-10-30")
    assert not isinstance(failure.value, (ValueError, QuotePermissionDeniedError))
    assert "timed out" in str(failure.value)
    assert context.get_option_chain.call_count == 1


@pytest.mark.asyncio
async def test_reads_share_snapshot_admission(broker):
    service, context, clock = broker
    for _ in range(60):
        await run_blocking(service.get_market_snapshot_evidence, ["US.EXAMPLE"])
    pending = asyncio.create_task(
        run_blocking(service.get_market_snapshot_evidence, ["US.EXAMPLE"])
    )
    await clock.wait_for_sleepers()
    clock.advance(5.1)
    with pytest.raises(ProviderRateLimitError):
        await pending
    assert context.get_market_snapshot.call_count == 60


@pytest.mark.asyncio
async def test_market_profile_schema_and_evidence():
    with client_grants_context(ClientGrants(permissions=frozenset({"market_read"}))):
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        assert "get_order_fees" not in tools
        assert "place_order" not in tools
        for name in ("get_instrument_facts", "get_rehab", "get_option_volatility"):
            annotations = tools[name].annotations
            assert annotations is not None and annotations.readOnlyHint is True
        capabilities = capability_evidence(set(tools))
    assert all(
        item["live_verification"] == "UNKNOWN" for item in capabilities["operations"]
    )
    native = next(
        item
        for item in capabilities["operations"]
        if item["method"] == "get_option_quote"
    )
    assert native["sdk_method_present"] is True
    assert native["implementation"] == "deferred"
    assert native["served_schema_present"] is False


@pytest.mark.asyncio
async def test_evidence_crosses_mcp_and_requires_market_grant(broker, mcp_app_context):
    service, context, _ = broker
    mcp_app_context.market_data_service = service
    with client_grants_context(ClientGrants(permissions=frozenset({"market_read"}))):
        result = await call_mcp_tool(
            mcp_app_context, "get_market_snapshot_evidence", {"codes": ["US.EXAMPLE"]}
        )
    assert result.structured["coverage"]["missing"] == []
    assert result.structured["data"][0]["bid_price"] == 1.1
    context.get_market_snapshot.assert_called_once()
    context.get_market_snapshot.reset_mock()
    with client_grants_context(ClientGrants(permissions=frozenset({"simulate_read"}))):
        refused = await mcp._call_tool_with_errors(
            "get_market_snapshot_evidence", {"codes": ["US.EXAMPLE"]}
        )
    assert refused.isError is True
    assert refused.structuredContent["error_code"] == "CLIENT_PERMISSION_DENIED"
    context.get_market_snapshot.assert_not_called()
