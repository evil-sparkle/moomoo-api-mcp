"""Independent REAL configuration, process startup and storage ownership checks."""

import fcntl
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import moomoo_mcp.server as server
from moomoo_mcp.services.execution_store import ExecutionStore, ExecutionStoreError
from moomoo_mcp.services.trading_policy import TradingModeConfigError
from moomoo_mcp.settings import load_settings


def real_environment(tmp_path, *, paper=False, create=True):
    environment = {
        "MOOMOO_TRADING_MODE": "REAL",
        "MOOMOO_REAL_ACC_IDS": "456",
        "MOOMOO_REAL_JOURNAL_PATH": str(tmp_path / "real" / "execution.db"),
        "MOOMOO_CREATE_REAL_JOURNAL": "1" if create else "0",
        "MCP_AUTH_TOKEN": "synthetic-journal-test-only",
    }
    if paper:
        environment.update(
            {
                "MOOMOO_SIMULATED_ACC_IDS": "123",
                "MOOMOO_JOURNAL_PATH": str(tmp_path / "paper" / "execution.db"),
                "MOOMOO_CREATE_JOURNAL": "1" if create else "0",
            }
        )
    return environment


@pytest.mark.parametrize("path", [None, "", "   "])
def test_real_requires_explicit_storage_before_touching_disk(tmp_path, path):
    environment = real_environment(tmp_path)
    if path is None:
        del environment["MOOMOO_REAL_JOURNAL_PATH"]
    else:
        environment["MOOMOO_REAL_JOURNAL_PATH"] = path
    with pytest.raises(TradingModeConfigError) as refusal:
        load_settings(environment)
    assert "MOOMOO_REAL_JOURNAL_PATH" in str(refusal.value)
    assert not (tmp_path / "real").exists()


@pytest.mark.parametrize("value", ["yes", "2", "true", "-1"])
def test_real_initialization_flag_requires_explicit_binary_value(tmp_path, value):
    environment = real_environment(tmp_path)
    environment["MOOMOO_CREATE_REAL_JOURNAL"] = value
    with pytest.raises(TradingModeConfigError) as refusal:
        load_settings(environment)
    assert "MOOMOO_CREATE_REAL_JOURNAL" in str(refusal.value)


def test_real_creation_defaults_off_and_preserves_broad_market_scope(tmp_path):
    environment = real_environment(tmp_path)
    del environment["MOOMOO_CREATE_REAL_JOURNAL"]
    environment["MOOMOO_TRADING_MARKET"] = "HK"
    settings = load_settings(environment)
    assert not settings.create_real_journal
    assert settings.trading_market == "HK"
    assert settings.journal_path is None


@pytest.mark.parametrize("mode", ["READ_ONLY", "SIMULATE"])
def test_other_modes_ignore_real_storage_and_creation(tmp_path, mode):
    environment = real_environment(tmp_path, paper=True)
    environment["MOOMOO_TRADING_MODE"] = mode
    environment["MOOMOO_CREATE_REAL_JOURNAL"] = "ignored-invalid"
    settings = load_settings(environment)
    assert settings.real_journal_path is None
    assert not settings.create_real_journal
    assert not (tmp_path / "real").exists()


@pytest.mark.parametrize("alias", [False, True])
def test_real_and_paper_cannot_share_directory_even_through_alias(tmp_path, alias):
    environment = real_environment(tmp_path, paper=True)
    paper_parent = tmp_path / "paper"
    paper_parent.mkdir()
    real_parent = paper_parent
    if alias:
        real_parent = tmp_path / "linked-real"
        real_parent.symlink_to(paper_parent, target_is_directory=True)
    environment["MOOMOO_REAL_JOURNAL_PATH"] = str(real_parent / "different.db")
    with pytest.raises(TradingModeConfigError) as refusal:
        load_settings(environment)
    assert "distinct directories" in str(refusal.value)


@pytest.fixture(autouse=True)
def fresh_real_process(monkeypatch):
    monkeypatch.setattr(server, "_services", None)
    monkeypatch.setattr(server, "_prepared_journal", None)
    monkeypatch.setattr(server.atexit, "register", MagicMock())
    yield
    server.close_services()


def assert_directory_lock_released(directory):
    descriptor = os.open(directory / "execution.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


def test_both_journals_and_recovery_workers_exist_before_http(tmp_path):
    settings = load_settings(real_environment(tmp_path, paper=True))

    def assert_ready(*_args, **_kwargs):
        context = server._services
        assert context is not None
        service = context.trade_service
        assert service.paper is not None and service.real is not None
        assert service.paper.store.environment == "SIMULATE"
        assert service.real.store.environment == "REAL"
        assert service.paper.store.epoch != service.real.store.epoch
        assert service.paper.recovery._thread is not None
        assert service.real.recovery._thread is not None
        assert service.paper.recovery._thread.is_alive()
        assert service.real.recovery._thread.is_alive()
        assert server._prepared_journal is None

    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server.MoomooService, "connect"),
        patch.object(server.TradeService, "connect"),
        patch.object(server, "create_streamable_http_app"),
        patch("uvicorn.run", side_effect=assert_ready) as transport,
    ):
        server.main()
        transport.assert_called_once()
    context = server._services
    assert context is not None
    assert context.trade_service.paper is not None
    assert context.trade_service.real is not None
    workers = (
        context.trade_service.paper.recovery._thread,
        context.trade_service.real.recovery._thread,
    )
    assert all(worker is not None for worker in workers)
    server.close_services()
    assert all(worker is not None and not worker.is_alive() for worker in workers)
    assert_directory_lock_released(tmp_path / "paper")
    assert_directory_lock_released(tmp_path / "real")


@pytest.mark.parametrize("failure", ["corrupt", "missing", "wrong_environment"])
def test_real_storage_failure_releases_prepared_paper_before_broker_or_http(
    tmp_path, failure
):
    environment = real_environment(tmp_path, paper=True, create=False)
    paper_path = tmp_path / "paper" / "execution.db"
    ExecutionStore(paper_path, create=True).close()
    real_path = tmp_path / "real" / "execution.db"
    real_path.parent.mkdir()
    original = b"not a database"
    if failure == "corrupt":
        real_path.write_bytes(original)
    elif failure == "wrong_environment":
        ExecutionStore(real_path, create=True).close()
    settings = load_settings(environment)
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server, "MoomooService") as quote,
        patch.object(server, "TradeService") as trade,
        patch.object(server, "create_streamable_http_app") as app,
        patch("uvicorn.run") as transport,
        pytest.raises((ExecutionStoreError, sqlite3.DatabaseError)),
    ):
        server.main()
    quote.assert_not_called()
    trade.assert_not_called()
    app.assert_not_called()
    transport.assert_not_called()
    assert server._prepared_journal is None
    assert_directory_lock_released(paper_path.parent)
    assert_directory_lock_released(real_path.parent)
    if failure == "corrupt":
        assert real_path.read_bytes() == original
    elif failure == "missing":
        assert not real_path.exists()
    reopened = ExecutionStore(paper_path)
    reopened.close()


@pytest.mark.parametrize("failure", ["MoomooService", "TradeService", "transport"])
def test_partial_real_startup_releases_both_stores(tmp_path, failure):
    settings = load_settings(real_environment(tmp_path, paper=True))
    stores = []

    def open_store(*args, **kwargs):
        store = ExecutionStore(*args, **kwargs)
        stores.append(store)
        return store

    target = "create_streamable_http_app" if failure == "transport" else failure
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server, "ExecutionStore", side_effect=open_store),
        patch.object(server.MoomooService, "connect"),
        patch.object(server.TradeService, "connect"),
        patch.object(server, target, side_effect=RuntimeError("synthetic startup")),
        patch("uvicorn.run") as transport,
        pytest.raises(RuntimeError),
    ):
        server.main()
    transport.assert_not_called()
    assert server._prepared_journal is None
    assert server._services is None
    assert len(stores) == 2
    for store in stores:
        with pytest.raises(ExecutionStoreError) as closed:
            store.lookup("synthetic")
        assert "closed" in str(closed.value)
    assert_directory_lock_released(tmp_path / "paper")
    assert_directory_lock_released(tmp_path / "real")
