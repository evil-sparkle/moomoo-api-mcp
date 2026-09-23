"""Storage is ready before listening; broker connections remain request-lazy."""

import fcntl
import os
import sqlite3
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest

import moomoo_mcp.server as server
from moomoo_mcp.services.execution_store import ExecutionStore, ExecutionStoreError
from moomoo_mcp.settings import load_settings


@pytest.fixture(autouse=True)
def isolated_process_services(monkeypatch):
    monkeypatch.setattr(server, "_services", None)
    monkeypatch.setattr(server, "_prepared_journal", None)
    monkeypatch.setattr(server.atexit, "register", MagicMock())
    yield
    server.close_services()


def journal_settings(tmp_path, *, create=True):
    return load_settings(
        {
            "MOOMOO_TRADING_MODE": "SIMULATE",
            "MOOMOO_SIMULATED_ACC_IDS": "123",
            "MOOMOO_JOURNAL_PATH": str(tmp_path / "paper.sqlite3"),
            "MOOMOO_CREATE_JOURNAL": "1" if create else "0",
            "MCP_TRANSPORT": "streamable-http",
            "MCP_AUTH_TOKEN": "test-agent-token",
        }
    )


def assert_lock_released(tmp_path):
    descriptor = os.open(tmp_path / "execution.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


def test_startup_prepares_real_sqlite_without_connecting_and_preserves_epoch(tmp_path):
    settings = journal_settings(tmp_path)
    assert settings.journal_path is not None
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server.MoomooService, "connect") as connect_quote,
        patch.object(server.TradeService, "connect") as connect_trade,
        patch.object(server, "create_streamable_http_app"),
        patch("uvicorn.run") as transport,
    ):
        server.main()
        transport.assert_called_once()
        connect_quote.assert_not_called()
        connect_trade.assert_not_called()
        assert server._services is None
        assert server._prepared_journal is not None
        prepared_settings, prepared_store = server._prepared_journal
        assert prepared_settings is settings
        prepared_epoch = prepared_store.epoch
        with pytest.raises(ExecutionStoreError) as competing:
            ExecutionStore(settings.journal_path)
        assert "Another process" in str(competing.value)

        # The first request consumes the prepared configuration and open store.
        with patch.object(
            server, "load_settings", side_effect=AssertionError("must not reload")
        ):
            context = server.get_services()
            assert server.get_services() is context
        connect_quote.assert_called_once()
        connect_trade.assert_called_once()
        assert server._prepared_journal is None
        assert context.trade_service.paper is not None
        assert context.trade_service.paper.store is prepared_store
        assert context.trade_service.paper.store.epoch == prepared_epoch
        with closing(sqlite3.connect(settings.journal_path)) as connection:
            assert connection.execute("SELECT epoch FROM epochs").fetchall() == [
                (prepared_epoch,)
            ]

    server.close_services()
    server.close_services()
    assert_lock_released(tmp_path)


def test_shutdown_releases_prepared_store_without_any_request(tmp_path):
    settings = journal_settings(tmp_path)
    assert settings.journal_path is not None
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server, "MoomooService") as quote_class,
        patch.object(server, "TradeService") as trade_class,
        patch.object(server, "create_streamable_http_app"),
        patch("uvicorn.run"),
    ):
        server.main()
        quote_class.assert_not_called()
        trade_class.assert_not_called()
        assert server._prepared_journal is not None
        prepared_store = server._prepared_journal[1]
        server.close_services()
        server.close_services()
        assert server._prepared_journal is None
        assert server._services is None
        with pytest.raises(ExecutionStoreError) as closed:
            prepared_store.lookup("operation")
        assert "closed" in str(closed.value)
    assert_lock_released(tmp_path)
    reopened = ExecutionStore(settings.journal_path)
    reopened.close()


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_invalid_storage_refuses_before_transport_or_broker_start(tmp_path, state):
    path = tmp_path / "paper.sqlite3"
    corrupt_contents = b"not a SQLite journal"
    if state == "corrupt":
        path.write_bytes(corrupt_contents)
    settings = journal_settings(tmp_path, create=False)
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server, "MoomooService") as quote_class,
        patch.object(server, "TradeService") as trade_class,
        patch.object(server, "create_streamable_http_app") as app_factory,
        patch("uvicorn.run") as transport,
        pytest.raises((ExecutionStoreError, sqlite3.DatabaseError)) as error,
    ):
        server.main()

    if state == "missing":
        assert "Missing journal" in str(error.value)
        assert not path.exists()
    else:
        assert path.read_bytes() == corrupt_contents
    app_factory.assert_not_called()
    transport.assert_not_called()
    quote_class.assert_not_called()
    trade_class.assert_not_called()
    assert server._prepared_journal is None
    assert server._services is None
    assert_lock_released(tmp_path)


@pytest.mark.parametrize("constructor", ["MoomooService", "TradeService"])
def test_constructor_failure_releases_consumed_prepared_store(tmp_path, constructor):
    settings = journal_settings(tmp_path)
    assert settings.journal_path is not None
    with (
        patch.object(server, "load_settings", return_value=settings),
        patch.object(server, "create_streamable_http_app"),
        patch("uvicorn.run"),
    ):
        server.main()
    assert server._prepared_journal is not None
    prepared_store = server._prepared_journal[1]
    try:
        with (
            patch.object(server, constructor, side_effect=RuntimeError("constructor")),
            pytest.raises(RuntimeError) as error,
        ):
            server.get_services()
        assert str(error.value) == "constructor"
        assert server._prepared_journal is None
        assert server._services is None
        assert_lock_released(tmp_path)
        with pytest.raises(ExecutionStoreError) as closed:
            prepared_store.lookup("operation")
        assert "closed" in str(closed.value)
    finally:
        # Also release resources if the regression is present and an assertion fails.
        prepared_store.close()
