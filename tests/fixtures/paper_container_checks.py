"""Paper and REAL probes in isolated containers, without a broker."""

from __future__ import annotations

import asyncio
import fcntl
import inspect
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from moomoo_mcp.services.execution_store import (
    ASSUMED_ABSENT,
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)

DATA = Path("/var/lib/moomoo-mcp/data")
DB = DATA / "execution.sqlite3"
REAL_DB = DATA / "real" / "execution.sqlite3"
DEVICE = Path("/home/opend/.com.moomoo.OpenD/fixture-marker")


def admit(store: ExecutionStore, token: str) -> None:
    store.admit(token, store.epoch, 123, "PLACE", '{"fixture":true}', {"qty": 1})


def seed() -> None:
    assert os.getuid() == 10001
    assert DATA.stat().st_uid == 10001
    store = ExecutionStore(DB, create=True)
    assert store.review()["state"] == "READY"
    admit(store, "before-backup")
    store.mark_dispatch("before-backup", {"qty": 1})
    store.outcome(
        "before-backup",
        "ACKNOWLEDGED",
        "ACKNOWLEDGED",
        receipt={"order_id": "fixture-1"},
    )
    # The original admitted row must become reviewable after the older restore.
    admit(store, "stale-admitted")
    (DATA / "old-epoch").write_text(store.epoch)
    DEVICE.write_text("authorization-volume-untouched")
    assert DB.stat().st_uid == 10001
    store.close()
    print("C01 seed persisted under UID 10001", flush=True)


def recreated() -> None:
    store = ExecutionStore(DB)
    row = store.lookup("before-backup")
    assert row is not None and row["broker_order_id"] == "fixture-1"
    assert DEVICE.read_text() == "authorization-volume-untouched"
    assert store.health()["recovery_review_outstanding"]
    assert store.review()["state"] != "READY"
    # Storage-only fixture: drive five completed negative rounds through the
    # durable policy interface. Broker evidence classification is tested separately.
    for number in range(5):
        store.record_recovery(
            "stale-admitted",
            observed_state="ADMITTED",
            started_at=number * 10,
            completed_at=number * 10 + 1,
            outcome="NOT_FOUND",
            details={"orders": []},
            next_check_at=number * 10 + 6,
            resolution="ASSUMED_NOT_PLACED_AFTER_RETRIES" if number == 4 else None,
        )
    assert store.health()["state"] == "READY"
    store.close()
    print("C01 recreated container retained rows and device marker", flush=True)


def real_service(store: ExecutionStore):
    """Use actual REAL execution against an entirely local broker double."""
    import pandas as pd

    from moomoo_mcp.services.trade_service import TradeService
    from moomoo_mcp.services.trading_policy import (
        InstrumentFacts,
        TradingMode,
        TradingPolicy,
    )

    def instrument_lookup(codes):
        if list(codes) != ["US.FIXTURE"]:
            raise ValueError("Container broker double only supports US.FIXTURE")
        return [
            InstrumentFacts(
                code="US.FIXTURE",
                classification="STOCK",
                lot_size=1,
                last_price=12.5,
            )
        ]

    service = TradeService(
        policy=TradingPolicy(TradingMode.REAL, real_acc_ids=frozenset({123})),
        trade_password="fixture-only",
        instrument_lookup=instrument_lookup,
        real_execution_store=store,
    )
    context = MagicMock()
    context.get_acc_list.return_value = (
        0,
        pd.DataFrame([{"acc_id": 123, "trd_env": "REAL", "trdmarket_auth": ["US"]}]),
    )
    for method in (
        "order_list_query",
        "history_order_list_query",
        "position_list_query",
    ):
        getattr(context, method).return_value = (0, pd.DataFrame())
    context.unlock_trade.return_value = (0, "")

    def place(**request):
        row = store.lookup("real-before-recreate")
        assert row is not None and row["state"] == "DISPATCHING"
        assert row["marker"] == 1
        return 0, pd.DataFrame(
            [request | {"order_id": "1234001", "order_status": "SUBMITTED"}]
        )

    context.place_order.side_effect = place
    service.trade_ctx = context
    return service, context


def real_place(service, operation_id: str, epoch: str):
    return service.place_order(
        code="US.FIXTURE",
        price=12.5,
        qty=1,
        trd_side="BUY",
        trd_env="REAL",
        acc_id=123,
        operation_id=operation_id,
        admission_epoch=epoch,
    )


def real_seed() -> None:
    from moomoo_mcp.services.real_execution import ROUTING, canonical_real_request

    assert os.getuid() == 10001
    store = ExecutionStore(REAL_DB, create=True, environment="REAL")
    service, context = real_service(store)
    try:
        result = real_place(service, "real-before-recreate", store.epoch)
        assert result["state"] == "ACKNOWLEDGED"
        assert result["order_id"] == "1234001"
        context.place_order.assert_called_once()
        assert [
            call.kwargs["is_unlock"] for call in context.unlock_trade.call_args_list
        ] == [True, False]

        # Model process loss after the marker but before a durable outcome.
        # This second operation never calls any SDK mutation in the fixture.
        arguments = inspect.signature(service.place_order).bind(
            code="US.FIXTURE",
            price=12.5,
            qty=1,
            trd_side="BUY",
            trd_env="REAL",
            acc_id=123,
            operation_id="real-crashed",
            admission_epoch=store.epoch,
        )
        arguments.apply_defaults()
        request = {
            name: value
            for name, value in arguments.arguments.items()
            if name not in ROUTING
        }
        row, admitted = store.admit(
            "real-crashed",
            store.epoch,
            123,
            "PLACE",
            canonical_real_request(123, "PLACE", request),
            request,
        )
        assert admitted
        store.mark_dispatch("real-crashed", request | {"remark": row["order_tag"]})
        (REAL_DB.parent / "old-epoch").write_text(store.epoch)
        assert REAL_DB.stat().st_uid == 10001
        assert REAL_DB.parent.stat().st_uid == 10001
    finally:
        service.close()
    print("R01 REAL acknowledgement and dispatch marker persisted", flush=True)


def real_recreated() -> None:
    from moomoo_mcp.services.order_errors import OrderNotSentError

    store = ExecutionStore(REAL_DB, environment="REAL")
    service, context = real_service(store)
    try:
        epoch = (REAL_DB.parent / "old-epoch").read_text()
        assert epoch != store.epoch
        acknowledged = store.lookup("real-before-recreate")
        pending = store.lookup("real-crashed")
        assert acknowledged is not None and acknowledged["state"] == "ACKNOWLEDGED"
        assert pending is not None and pending["state"] == "UNKNOWN_OUTCOME"
        assert pending["marker"] == 1
        assert store.health()["state"] == "JOURNAL_BLOCKED"

        service.trade_ctx = None
        duplicate = real_place(service, "real-before-recreate", epoch)
        assert duplicate["state"] == "ACKNOWLEDGED"
        assert duplicate["order_id"] == "1234001"
        crashed_duplicate = real_place(service, "real-crashed", epoch)
        assert crashed_duplicate["state"] == "UNKNOWN_OUTCOME"
        assert context.method_calls == []
        service.trade_ctx = context

        try:
            real_place(service, "real-new-after-recreate", store.epoch)
        except OrderNotSentError as exc:
            assert "JOURNAL_BLOCKED" in str(exc)
        else:
            raise AssertionError("Unresolved REAL dispatch did not gate new work")
        assert context.method_calls == []

        assert service.real is not None
        clock = [time.time()]
        service.real.recovery.clock = lambda: clock[0]
        for _ in range(6):
            service.real.recovery.run_once()
            clock[0] += 60
        pending = store.lookup("real-crashed")
        assert pending is not None and pending["state"] == "UNKNOWN_OUTCOME"
        assert len(pending["recovery_checks"]) == 6
        assert all(
            check["outcome"] == "NOT_FOUND" for check in pending["recovery_checks"]
        )
        assert pending["recovery_disposition"] is None
        assert store.health()["state"] == "JOURNAL_BLOCKED"
        try:
            store.record_recovery(
                "real-crashed",
                observed_state="UNKNOWN_OUTCOME",
                started_at=clock[0],
                completed_at=clock[0] + 1,
                outcome="NOT_FOUND",
                details={"orders": [], "correlated_order_seen": False},
                next_check_at=clock[0] + 60,
                resolution=ASSUMED_ABSENT,
            )
        except ExecutionConflict as exc:
            assert "REAL recovery cannot assume absence" in str(exc)
        else:
            raise AssertionError("REAL storage accepted assumed absence")
        assert store.health()["state"] == "JOURNAL_BLOCKED"
        assert context.order_list_query.call_count == 6
        assert context.history_order_list_query.call_count == 6
        for method in ("order_list_query", "history_order_list_query"):
            assert all(
                call.kwargs["trd_env"] == "REAL"
                for call in getattr(context, method).call_args_list
            )
        context.place_order.assert_not_called()
        context.place_combo_order.assert_not_called()
        context.modify_order.assert_not_called()
        context.unlock_trade.assert_not_called()
        assert DEVICE.read_text() == "authorization-volume-untouched"
    finally:
        service.close()
    print(
        "R01 recreated REAL journal retained identity; six negatives stayed blocked",
        flush=True,
    )


def hold() -> None:
    store = ExecutionStore(DB)
    store.review()
    # Save an older version with one nonterminal row, then advance live storage.
    admit(store, "old-pending")
    backup_started = Event()

    def write_during_backup():
        assert backup_started.wait(10), "Backup never started"
        store.outcome(
            "before-backup",
            "ACKNOWLEDGED",
            "ACKNOWLEDGED",
            receipt={"order_id": "fixture-1", "concurrent_backup_write": True},
        )

    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        sqlite3.connect(DB) as source,
        sqlite3.connect(DATA / "backup.sqlite3") as destination,
    ):
        writer = pool.submit(write_during_backup)

        def progress(_status, remaining, _total):
            if not backup_started.is_set():
                assert remaining > 0, "Fixture must exercise a multipage backup"
                backup_started.set()
                writer.result(timeout=10)

        source.backup(destination, pages=1, progress=progress)
        writer.result(timeout=10)
        assert destination.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    row = store.lookup("before-backup")
    assert row is not None and "concurrent_backup_write" in row["receipt"]
    store.mark_dispatch("old-pending", {"qty": 1})
    store.outcome(
        "old-pending", "ACKNOWLEDGED", "ACKNOWLEDGED", receipt={"order_id": "fixture-2"}
    )
    admit(store, "after-backup")
    store.mark_dispatch("after-backup", {"qty": 1})
    store.outcome(
        "after-backup",
        "ACKNOWLEDGED",
        "ACKNOWLEDGED",
        receipt={"order_id": "fixture-3"},
    )
    (DATA / "retired-epoch").write_text(store.epoch)
    (DATA / "holder-ready").touch()
    print(
        "C02 holder ready; C03 online backup completed with live executor", flush=True
    )
    try:
        while not (DATA / "holder-stop").exists():
            time.sleep(0.1)
    finally:
        store.close()


def contender() -> None:
    deadline = time.monotonic() + 60
    while not (DATA / "holder-ready").exists():
        if time.monotonic() > deadline:
            raise AssertionError("Holder did not acquire lock")
        time.sleep(0.1)
    try:
        ExecutionStore(DB)
    except ExecutionStoreError as exc:
        assert "Another process" in str(exc)
    else:
        raise AssertionError("Second container acquired executor lock")
    with sqlite3.connect(DB) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute(
            "SELECT broker_order_id FROM operations WHERE operation_id='after-backup'"
        ).fetchone() == ("fixture-3",)
    (DATA / "holder-stop").touch()
    print("C02 competing container failed closed, active DB intact", flush=True)


def restore() -> None:
    shutil.copyfile(DATA / "backup.sqlite3", DB)
    store = ExecutionStore(DB)
    assert store.health()["recovery_review_outstanding"]
    assert store.lookup("after-backup") is None
    try:
        store.admit(
            "after-backup", (DATA / "retired-epoch").read_text(), 123, "PLACE", "{}", {}
        )
    except ExecutionConflict:
        pass
    else:
        raise AssertionError("Restored missing token was admitted")
    assert store.review()["state"] != "READY"
    try:
        admit(store, "new-after-restore")
    except ExecutionConflict:
        pass
    else:
        raise AssertionError("Restored unresolved row did not gate new work")
    row = store.lookup("old-pending")
    assert row is not None and row["state"] == "ADMITTED"
    assert DEVICE.read_text() == "authorization-volume-untouched"
    store.close()
    print("C03 older restore refused retired token and required review", flush=True)


def offline_copy(source: Path, destination: Path) -> None:
    """Conservative documented procedure: lock and reject rollback sidecars."""
    with (source.parent / "execution.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if Path(str(source) + "-journal").exists():
            raise RuntimeError("Recover database first; database-only copy refused")
        shutil.copyfile(source, destination)


def dirty() -> None:
    # A committed original page plus a forcibly spilled, uncommitted replacement
    # leaves a real hot rollback journal when the child exits without cleanup.
    dirty_db = DATA / "dirty.sqlite3"
    with sqlite3.connect(dirty_db) as conn:
        conn.execute("CREATE TABLE probe (payload BLOB)")
        conn.execute("INSERT INTO probe VALUES (?)", (b"a" * 2_000_000,))
    code = """
import fcntl, os, sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1])
lock = (p.parent / 'execution.lock').open('a+b')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
c = sqlite3.connect(p)
c.execute('PRAGMA cache_size=10')
c.execute('BEGIN IMMEDIATE')
c.execute('UPDATE probe SET payload=?', (b'b' * 2_000_000,))
os._exit(17)
"""
    result = subprocess.run([sys.executable, "-c", code, str(dirty_db)], check=False)
    assert result.returncode == 17
    sidecar = Path(str(dirty_db) + "-journal")
    assert sidecar.is_file() and sidecar.stat().st_size > 512
    assert sidecar.read_bytes()[:8] != bytes(8), "rollback journal was not hot"
    try:
        offline_copy(dirty_db, DATA / "unsupported-copy.sqlite3")
    except RuntimeError as exc:
        assert "Recover database first" in str(exc)
    else:
        raise AssertionError("Dirty database-only copy was allowed")
    assert not (DATA / "unsupported-copy.sqlite3").exists()
    # Opening normally recovers using its sibling rollback journal.
    with sqlite3.connect(dirty_db) as conn:
        assert conn.execute("SELECT substr(payload, 1, 1) FROM probe").fetchone() == (
            b"a",
        )
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    offline_copy(dirty_db, DATA / "recovered-copy.sqlite3")
    print(
        "C04 unheld crash lock did not authorize dirty database-only backup", flush=True
    )


def read_only() -> None:
    import pandas as pd

    from moomoo_mcp import server
    from moomoo_mcp.settings import load_settings
    from moomoo_mcp.tools.market_data import get_stock_quote

    assert os.getuid() == 10001
    assert not DB.exists()
    settings = load_settings({"MOOMOO_TRADING_MODE": "READ_ONLY"})
    quote = MagicMock()
    quote.subscribe.return_value = (0, "")
    quote.get_stock_quote.return_value = (
        0,
        pd.DataFrame([{"code": "US.FIXTURE", "last_price": 12.5}]),
    )

    def connect(service):
        service.quote_ctx = quote

    async def info(_message):
        pass

    async def run():
        with (
            patch.object(server, "load_settings", return_value=settings),
            patch.object(
                server,
                "ExecutionStore",
                side_effect=AssertionError("READ_ONLY opened journal"),
            ),
            patch.object(server.MoomooService, "connect", connect),
            patch.object(server.TradeService, "connect", return_value=None),
        ):
            try:
                async with server.app_lifespan(server.mcp) as services:
                    assert "get_stock_quote" in [
                        tool.name for tool in await server.mcp.list_tools()
                    ]
                    ctx = SimpleNamespace(
                        request_context=SimpleNamespace(lifespan_context=services),
                        info=info,
                    )
                    result = await get_stock_quote(ctx, ["US.FIXTURE"])
                    assert result == [{"code": "US.FIXTURE", "last_price": 12.5}]
            finally:
                server.close_services()
        assert not DB.exists()
        assert not (DATA / "execution.lock").exists()

    asyncio.run(run())
    print(
        "C04 READ_ONLY started; quote tool read succeeded without journal volume",
        flush=True,
    )


if __name__ == "__main__":
    actions = {
        "seed": seed,
        "recreated": recreated,
        "real-seed": real_seed,
        "real-recreated": real_recreated,
        "hold": hold,
        "contender": contender,
        "restore": restore,
        "dirty": dirty,
        "read-only": read_only,
    }
    actions[sys.argv[1]]()
