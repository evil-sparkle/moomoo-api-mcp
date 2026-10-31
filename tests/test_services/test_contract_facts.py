"""All comparable fields preserve missingness, source conflicts and economics."""

from unittest.mock import MagicMock

import pandas as pd
import pytest
from moomoo import OpenQuoteContext

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.contract_facts import CONTRACT_FIELDS
from moomoo_mcp.services.instruments import InstrumentAdapter, InstrumentLookupError
from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.services.query_contracts import TradeQueryError
from tests.rate_limit_clock import FakeClock

CODE = "US.EXACT1261130C00100000"
OTHER = "US.EXACT261130C00105000"
# Deliberately different lot, deliverable and premium multiplier.
VALUES = {
    "is_option": (True, False),
    "option_type": ("CALL", "PUT"),
    "underlying": ("US.EXACT", "US.OTHER"),
    "expiry": ("2026-11-30", "2026-12-31"),
    "strike": (100.0, 105.0),
    "classification": ("DRVT", "STOCK"),
    "lot_size": (1.0, 10.0),
    "currency": ("USD", "HKD"),
    "exercise_style": ("AMERICAN", "EUROPEAN"),
    "settlement_mode": ("PM", "AM"),
    "settlement_cutoff": ("2026-11-30T16:00:00-05:00", "2026-11-30T15:00:00-05:00"),
    "deliverable_size": (150.0, 100.0),
    "deliverable_description": (
        "150 shares plus cash; components unverified",
        "100 shares",
    ),
    "monetary_multiplier": (25.0, 100.0),
    "standard_status": ("NON_STANDARD", "STANDARD"),
}


def full_row():
    return {"code": CODE} | {
        CONTRACT_FIELDS[name][0]: values[0] for name, values in VALUES.items()
    }


@pytest.fixture
def facts_runtime(monkeypatch):
    clock = FakeClock()
    quote = MagicMock(spec=OpenQuoteContext)
    dispatcher = BrokerRequestDispatcher(clock=clock, sleep=clock.sleep)
    service = MarketDataService(quote, dispatcher=dispatcher)
    service.instrument_adapter = InstrumentAdapter(
        lambda: service.quote_ctx, dispatcher=dispatcher, clock=clock
    )
    quote.get_market_snapshot.return_value = (0, pd.DataFrame([full_row()]))
    quote.get_stock_basicinfo.return_value = (0, pd.DataFrame([full_row()]))
    quote.get_option_chain.return_value = (0, pd.DataFrame())
    monkeypatch.setattr(
        "moomoo_mcp.services.instruments.utc_now_iso", lambda: "2026-10-31T14:00:00Z"
    )
    monkeypatch.setattr(
        "moomoo_mcp.services.market_data_service.utc_now_iso",
        lambda: "2026-10-31T13:59:00Z",
    )
    return service, quote, clock


def read_facts(service, codes=None):
    return service.get_instrument_facts(codes or [CODE])["data"][0]["facts"]


def test_every_required_field_known_with_field_level_provenance(facts_runtime):
    service, quote, _clock = facts_runtime
    facts = read_facts(service)
    assert set(facts) == set(VALUES)
    for name, values in VALUES.items():
        fact = facts[name]
        assert fact["state"] == "KNOWN", name
        assert fact["value"] == values[0], name
        assert {item["source_method"] for item in fact["evidence"]} == {
            "get_stock_basicinfo",
            "get_market_snapshot",
        }
        assert all(
            item["observed_at"] == "2026-10-31T14:00:00Z" for item in fact["evidence"]
        )
        assert all(
            item["source_field"] == CONTRACT_FIELDS[name][0]
            for item in fact["evidence"]
        )
        assert all(item["raw_value"] == values[0] for item in fact["evidence"])
    assert facts["lot_size"]["value"] == 1
    assert facts["deliverable_size"]["value"] == 150
    assert facts["monetary_multiplier"]["value"] == 25
    quote.get_option_chain.assert_not_called()


@pytest.mark.parametrize("name", VALUES)
@pytest.mark.parametrize("other_source", ["reference", "chain"])
def test_every_field_conflict_preserves_both_observations(
    facts_runtime, name, other_source
):
    service, quote, _clock = facts_runtime
    conflicting = full_row() | {CONTRACT_FIELDS[name][0]: VALUES[name][1]}
    if other_source == "reference":
        quote.get_stock_basicinfo.return_value = (0, pd.DataFrame([conflicting]))
        expected_method = "get_stock_basicinfo"
    else:
        quote.get_option_chain.return_value = (0, pd.DataFrame([conflicting]))
        service.get_option_chain("US.EXACT", "2026-11-30", "2026-11-30")
        expected_method = "get_option_chain"
    result = service.get_instrument_facts([CODE])
    fact = result["data"][0]["facts"][name]
    assert fact["state"] == "CONFLICT" and fact["value"] is None
    assert {item["value"] for item in fact["evidence"]} == set(VALUES[name])
    assert expected_method in {item["source_method"] for item in fact["evidence"]}
    assert result["data"][0]["compatibility_verified"] is False


@pytest.mark.parametrize(
    "placeholder", [None, "N/A", " n/a ", "UNKNOWN", "", float("nan"), float("inf"), 0]
)
def test_placeholders_never_produce_known_lifecycle_or_economics(
    facts_runtime, placeholder
):
    service, quote, _clock = facts_runtime
    row = {key: placeholder for keys in CONTRACT_FIELDS.values() for key in keys} | {
        "code": CODE
    }
    quote.get_market_snapshot.return_value = (0, pd.DataFrame([row]))
    quote.get_stock_basicinfo.return_value = (0, pd.DataFrame([row]))
    facts = read_facts(service)
    assert all(
        fact == {"state": "UNKNOWN", "value": None, "evidence": []}
        for fact in facts.values()
    )


def test_normalize_representations_not_contract_assumptions(facts_runtime):
    service, quote, _clock = facts_runtime
    row = full_row() | {
        "currency": " usd ",
        "option_type": "call",
        "option_contract_multiplier": "25.00",
        "option_contract_size": "150",
        "lot_size": "1",
        "option_strike_price": "100.000",
    }
    quote.get_market_snapshot.return_value = (0, pd.DataFrame([row]))
    facts = read_facts(service)
    assert all(fact["state"] == "KNOWN" for fact in facts.values())
    assert facts["currency"]["value"] == "USD"
    assert facts["currency"]["evidence"][1]["raw_value"] == " usd "


def test_aliases_conflict_instead_of_precedence_and_invalid_strike_is_unknown(
    facts_runtime,
):
    service, quote, _clock = facts_runtime
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "option_strike_price": 0}]),
    )
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "stock_type": "DRVT"}]),
    )
    assert read_facts(service)["strike"]["state"] == "UNKNOWN"
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "option_strike_price": 100, "strike_price": 101}]),
    )
    strike = read_facts(service)["strike"]
    assert strike["state"] == "CONFLICT" and strike["value"] is None
    assert {item["source_field"] for item in strike["evidence"]} == {
        "option_strike_price",
        "strike_price",
    }


def test_basicinfo_strike_price_alias_fills_missing_snapshot_strike(facts_runtime):
    service, quote, _clock = facts_runtime
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "option_valid": True, "option_strike_price": 0}]),
    )
    # The pinned SDK's basic-info unpacker uses strike_price, not
    # option_strike_price. It also has no standard/settlement/currency columns.
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": CODE,
                    "stock_type": "DRVT",
                    "strike_price": 100,
                    "strike_time": "2026-11-30",
                    "option_type": "CALL",
                    "stock_owner": "US.EXACT",
                    "lot_size": 1,
                }
            ]
        ),
    )
    facts = read_facts(service)
    assert facts["strike"]["value"] == 100
    assert facts["strike"]["evidence"][0]["source_method"] == "get_stock_basicinfo"
    assert facts["strike"]["evidence"][0]["source_field"] == "strike_price"
    assert facts["standard_status"]["state"] == "UNKNOWN"
    assert facts["settlement_mode"]["state"] == "UNKNOWN"
    quote.get_option_chain.assert_not_called()


def test_chain_enrichment_uses_only_observed_columns_and_original_age(facts_runtime):
    service, quote, clock = facts_runtime
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": CODE,
                    "option_valid": True,
                    "option_contract_multiplier": 25,
                    "option_contract_size": 150,
                }
            ]
        ),
    )
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "stock_type": "DRVT"}]),
    )
    chain = {
        "code": CODE,
        "stock_owner": "US.EXACT",
        "strike_time": "2026-11-30",
        "strike_price": 100,
        "option_type": "CALL",
        "lot_size": 1,
        "option_standard_type": "NON_STANDARD",
        "option_settlement_mode": "PM",
        "delta": 0.5,
        "bid_price": 2,
    }
    quote.get_option_chain.return_value = (0, pd.DataFrame([chain]))
    before = read_facts(service)
    assert before["standard_status"]["state"] == before["strike"]["state"] == "UNKNOWN"
    assert service.get_option_chain("US.EXACT") == [chain]
    clock.advance(1)
    quote.get_stock_basicinfo.return_value = (0, pd.DataFrame())
    result = service.get_instrument_facts([CODE, OTHER])
    row = result["data"][0]
    facts = row["facts"]
    assert row["chain_observation_present"] is True
    assert facts["standard_status"]["value"] == "NON_STANDARD"
    assert facts["strike"]["evidence"] == [
        {
            "source_method": "get_option_chain",
            "source_field": "strike_price",
            "value": 100.0,
            "raw_value": 100,
            "observed_at": "2026-10-31T13:59:00Z",
        }
    ]
    assert facts["lot_size"]["value"] == 1
    assert facts["deliverable_size"]["value"] == 150
    assert facts["monetary_multiplier"]["value"] == 25
    for missing in (
        "currency",
        "exercise_style",
        "deliverable_description",
        "settlement_cutoff",
    ):
        assert facts[missing]["state"] == "UNKNOWN"
    assert row["compatibility_verified"] is False
    assert result["coverage"]["missing"] == [OTHER]
    assert result["data"][1]["chain_observation_present"] is False
    assert "delta" not in service.instrument_adapter._chain_metadata[CODE].raw
    assert "bid_price" not in service.instrument_adapter._chain_metadata[CODE].raw
    quote.get_option_chain.assert_called_once()


def test_chain_lot_and_standard_status_do_not_supply_missing_economics(facts_runtime):
    service, quote, _clock = facts_runtime
    quote.get_market_snapshot.return_value = (
        0,
        pd.DataFrame(
            [{"code": CODE, "option_valid": True, "option_contract_multiplier": 0}]
        ),
    )
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "stock_type": "DRVT"}]),
    )
    quote.get_option_chain.return_value = (
        0,
        pd.DataFrame(
            [
                {
                    "code": CODE,
                    "lot_size": 150,
                    "option_standard_type": "STANDARD",
                    "option_settlement_mode": "AM",
                }
            ]
        ),
    )
    service.get_option_chain("US.EXACT")
    facts = read_facts(service)
    assert facts["standard_status"]["value"] == "STANDARD"
    assert facts["lot_size"]["value"] == 150
    for missing in (
        "deliverable_size",
        "deliverable_description",
        "monetary_multiplier",
        "currency",
        "settlement_cutoff",
    ):
        assert facts[missing]["state"] == "UNKNOWN"
    with pytest.raises(
        InstrumentLookupError, match="no usable option_contract_multiplier"
    ):
        service.instrument_adapter([CODE])


def test_reference_without_classification_retains_other_observations_not_valuation(
    facts_runtime,
):
    service, quote, _clock = facts_runtime
    quote.get_market_snapshot.return_value = (0, pd.DataFrame())
    quote.get_stock_basicinfo.return_value = (
        0,
        pd.DataFrame([{"code": CODE, "currency": "USD", "lot_size": 1}]),
    )
    result = service.get_instrument_facts([CODE])
    assert result["data"][0]["facts"]["currency"]["value"] == "USD"
    assert result["data"][0]["facts"]["classification"]["state"] == "UNKNOWN"
    assert result["coverage"]["missing"] == [CODE]
    assert service.instrument_adapter._metadata == {}


def test_chain_cache_expires_evicts_and_invalidates_on_reconnect(facts_runtime):
    service, quote, clock = facts_runtime
    adapter = service.instrument_adapter
    adapter._metadata_capacity = 1
    quote.get_option_chain.return_value = (0, pd.DataFrame([full_row()]))
    service.get_option_chain("US.EXACT")
    observed = adapter._observed_chain_facts(quote, [CODE])
    assert set(observed) == {CODE}
    clock.advance(300)
    assert not adapter._observed_chain_facts(quote, [CODE])
    service.get_option_chain("US.EXACT")
    quote.get_option_chain.return_value = (
        0,
        pd.DataFrame([full_row() | {"code": OTHER}]),
    )
    service.get_option_chain("US.EXACT")
    assert set(adapter._chain_metadata) == {OTHER}
    new_quote = MagicMock(spec=OpenQuoteContext)
    service.quote_ctx = new_quote
    assert not adapter._observed_chain_facts(new_quote, [OTHER])
    # A late response from the retired context cannot erase/repopulate new data.
    adapter.remember_option_chain(quote, [full_row()], "old")
    assert not adapter._chain_metadata
    adapter._metadata_ttl = 0
    adapter.remember_option_chain(new_quote, [full_row()], "disabled")
    assert not adapter._chain_metadata


@pytest.mark.parametrize("rows", [[{"code": CODE}] * 2, [{"code": "US.OTHER"}]])
def test_duplicate_or_unexpected_reference_rows_are_not_silently_selected(
    facts_runtime, rows
):
    service, quote, _clock = facts_runtime
    quote.get_stock_basicinfo.return_value = (0, pd.DataFrame(rows))
    with pytest.raises(InstrumentLookupError, match="duplicate/unexpected"):
        service.get_instrument_facts([CODE])


@pytest.mark.parametrize("rows", [[{"code": CODE}] * 2, [{}], [{"code": "not-exact"}]])
def test_invalid_discovery_cannot_poison_fact_cache(facts_runtime, rows):
    service, quote, _clock = facts_runtime
    quote.get_option_chain.return_value = (0, pd.DataFrame(rows))
    with pytest.raises(TradeQueryError) as failure:
        service.get_option_chain("US.EXACT")
    assert failure.value.code == "INVALID_RESPONSE"
    assert not service.instrument_adapter._chain_metadata
