"""C01-C04 probes executed inside isolated containers, without a broker."""

from __future__ import annotations

import asyncio
import fcntl
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
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)

DATA = Path("/var/lib/moomoo-mcp/data")
DB = DATA / "execution.sqlite3"
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
    store.acknowledge(
        "stale-admitted",
        operator_id="fixture-operator",
        recovery_epoch=store.epoch,
        observed_state="ADMITTED",
        reason="fixture confirms no dispatch",
        evidence_reference="fixture://seed",
        accounted_facts={"dispatch": "not_sent"},
    )
    assert store.health()["state"] == "READY"
    store.close()
    print("C01 recreated container retained rows and device marker", flush=True)


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
        "hold": hold,
        "contender": contender,
        "restore": restore,
        "dirty": dirty,
        "read-only": read_only,
    }
    actions[sys.argv[1]]()
