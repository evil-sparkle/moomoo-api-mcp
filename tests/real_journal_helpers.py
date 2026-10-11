"""Actual REAL journals and caller identities for pre-journal regression tests.

The legacy suites focus on validation, routing, gateway locking and quotas.
They now execute through the durable REAL path, with this helper acting as the
caller that supplies each new operation's identity. Dedicated REAL journal
tests use explicit identities and unwrapped services instead.
"""

import inspect
from functools import wraps
from itertools import count

import pytest

from moomoo_mcp.services.execution_store import ExecutionStore
from moomoo_mcp.services.real_execution import RealExecution
from moomoo_mcp.services.trade_service import TradeService

MUTATIONS = ("place_order", "place_combo_order", "modify_order", "cancel_order")


@pytest.fixture(autouse=True)
def real_journal_legacy_calls(monkeypatch, tmp_path):
    """Give opted-in legacy tests durable storage and distinct caller tokens."""
    stores = []
    executors = []
    operations = count()
    original_init = TradeService.__init__
    original_prepare = TradeService.prepare_admission

    def supply_identity(service, arguments):
        if str(arguments.get("trd_env", "SIMULATE")).strip().upper() != "REAL":
            return
        if arguments.get("operation_id") is None:
            arguments["operation_id"] = f"legacy-operation-{next(operations)}"
        if arguments.get("admission_epoch") is None:
            arguments["admission_epoch"] = service.real.store.epoch

    @wraps(original_init)
    def initialize(service, *args, **kwargs):
        original_init(service, *args, **kwargs)
        if service.real is None:
            store = ExecutionStore(
                tmp_path / "real-journals" / str(len(stores)) / "real.sqlite3",
                create=True,
                environment="REAL",
            )
            stores.append(store)
            service.real = RealExecution(service, store)
            executors.append(service.real)

    @wraps(original_prepare)
    def prepare(service, method, arguments):
        if method in MUTATIONS:
            supply_identity(service, arguments)
        return original_prepare(service, method, arguments)

    def caller_wrapper(method):
        @wraps(method)
        def call(service, *args, **kwargs):
            arguments = inspect.signature(method).bind(service, *args, **kwargs)
            arguments.apply_defaults()
            supply_identity(service, arguments.arguments)
            return method(**arguments.arguments)

        return call

    monkeypatch.setattr(TradeService, "__init__", initialize)
    monkeypatch.setattr(TradeService, "prepare_admission", prepare)
    for name in MUTATIONS:
        monkeypatch.setattr(
            TradeService, name, caller_wrapper(getattr(TradeService, name))
        )
    yield
    for executor in executors:
        executor.recovery.stop()
    for store in stores:
        store.close()
