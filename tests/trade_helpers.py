"""Explicit synthetic services and caller identities for trading regressions."""

from uuid import uuid4

import pytest

from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.trade_service import TradeService
from moomoo_mcp.services.trading_policy import InstrumentFacts, TradingMode


def stock_facts(codes):
    return [
        InstrumentFacts(
            code=code,
            classification="STOCK",
            monetary_multiplier=1,
            last_price=150,
            lot_size=1,
        )
        for code in codes
    ]


@pytest.fixture
def trade_service_factory(tmp_path):
    """Build real services with temporary REAL storage and synthetic stock facts.

    Explicit instrument lookups are preserved, including None in refusal tests.
    Caller identities remain the responsibility of each mutation request.
    """
    stores = []
    services = []

    def build(*, policy, **kwargs):
        if policy.mode is TradingMode.REAL and "real_execution_store" not in kwargs:
            store = ExecutionStore(
                tmp_path / "real-journals" / str(len(stores)) / "real.sqlite3",
                create=True,
                environment="REAL",
            )
            stores.append(store)
            kwargs["real_execution_store"] = store
        if not policy.notional_cap_configured:
            kwargs.setdefault("instrument_lookup", stock_facts)
        service = TradeService(policy=policy, **kwargs)
        services.append(service)
        return service

    yield build
    for service in services:
        service.close()
    for store in stores:
        store.close()


def real_request(service, operation_id=None):
    """Supply a new caller identity explicitly; reuse the result for a retry."""
    if service.real is None:
        return {}
    return {
        "operation_id": operation_id
        if operation_id is not None
        else "test-operation-" + uuid4().hex,
        "admission_epoch": service.real.store.epoch,
    }
