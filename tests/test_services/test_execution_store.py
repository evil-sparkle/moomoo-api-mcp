"""SQLite lifecycle acceptance checks using real files and process boundaries."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from moomoo_mcp.services.execution_store import (
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)


def admit(store, token="operation"):
    return store.admit(token, store.epoch, 123, "PLACE", "canonical", {"price": "1.1"})


def test_explicit_creation_review_pragmas_and_worker_connections(tmp_path):
    path = tmp_path / "journal.db"
    with pytest.raises(ExecutionStoreError):
        ExecutionStore(path)
    assert not path.exists()
    store = ExecutionStore(path, create=True, lock_wait_ms=31)
    try:
        assert store.health()["state"] == "REVIEW_PENDING"
        with pytest.raises(ExecutionConflict):
            admit(store)
        assert store.review()["state"] == "READY"
        with store._connection() as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert conn.execute("PRAGMA synchronous").fetchone() == (3,)
            assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert conn.execute("PRAGMA busy_timeout").fetchone() == (31,)
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda n: admit(store, f"op-{n}")[0], range(12)))
        assert len({row["operation_id"] for row in rows}) == 12
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
    finally:
        store.close()


def test_newer_schema_is_not_modified_and_corruption_fails_closed(tmp_path):
    path = tmp_path / "journal.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 999")
    before = path.read_bytes()
    with pytest.raises(ExecutionStoreError):
        ExecutionStore(path)
    assert path.read_bytes() == before
    path.write_bytes(b"this is not SQLite")
    with pytest.raises((ExecutionStoreError, sqlite3.DatabaseError)):
        ExecutionStore(path)


def test_write_lock_failure_is_bounded_and_latched(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True, lock_wait_ms=25)
    store.review()
    with sqlite3.connect(path, isolation_level=None) as lock:
        lock.execute("BEGIN IMMEDIATE")
        start = time.monotonic()
        with pytest.raises(ExecutionStoreError):
            admit(store)
        assert time.monotonic() - start < 2
        lock.execute("ROLLBACK")
    assert store.health()["blocking_reasons"] == ["STORAGE_FAILED"]
    with pytest.raises(ExecutionStoreError):
        store.lookup("operation")
    store.close()
    reopened = ExecutionStore(path)
    assert reopened.review()["state"] == "READY"
    reopened.close()


def test_second_process_cannot_acquire_store(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "from moomoo_mcp.services.execution_store import ExecutionStore; "
                "import sys; ExecutionStore(sys.argv[1])",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode != 0
        assert "Another process holds" in child.stderr
    finally:
        store.close()


@pytest.mark.parametrize("window", ["admitted", "marked", "broker_returned"])
def test_process_crash_windows_and_two_restarts_preserve_review(tmp_path, window):
    path = tmp_path / "journal.db"
    script = """
import os, sys
from pathlib import Path
from moomoo_mcp.services.execution_store import ExecutionStore
s = ExecutionStore(sys.argv[1], create=True)
s.review()
s.admit("crashed", s.epoch, 123, "PLACE", "canonical", {"price":"1.1"})
Path(sys.argv[1] + ".epoch").write_text(s.epoch)
if sys.argv[2] != "admitted":
    s.mark_dispatch("crashed", {"price":"1.1"})
if sys.argv[2] == "broker_returned":
    Path(sys.argv[1] + ".broker").write_text("one invocation returned")
os._exit(73)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(path), window], timeout=10
    )
    assert child.returncode == 73
    old_epoch = Path(str(path) + ".epoch").read_text()
    assert Path(str(path) + ".broker").exists() == (window == "broker_returned")
    for _ in range(2):
        store = ExecutionStore(path)
        try:
            row = store.lookup("crashed")
            assert row is not None
            assert row["state"] == (
                "ADMITTED" if window == "admitted" else "UNKNOWN_OUTCOME"
            )
            assert row["admission_epoch"] == old_epoch
            assert store.epoch != old_epoch
            assert store.review()["state"] != "READY"
            with pytest.raises(ExecutionConflict):
                admit(store, "fresh")
            with pytest.raises(ExecutionConflict):
                store.admit("missing", old_epoch, 123, "PLACE", "canonical", {})
        finally:
            store.close()


def record_check(store, *, outcome="NOT_FOUND", resolution=None, number=1):
    row = store.lookup("operation")
    assert row is not None
    store.record_recovery(
        "operation",
        observed_state=row["state"],
        started_at=number * 10,
        completed_at=number * 10 + 1,
        outcome=outcome,
        details={"orders": []},
        next_check_at=number * 10 + 6,
        resolution=resolution,
        receipt={"order_id": "9", "order_status": "FILLED_ALL"}
        if resolution == "BROKER_CONFIRMED"
        else None,
        accounted_facts={"filled_quantity": 2}
        if resolution == "BROKER_CONFIRMED"
        else None,
    )


def test_recovery_commits_audit_and_all_review_removal_together(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    admit(store)
    store.mark_dispatch("operation", {})
    original = store.lookup("operation")
    assert original is not None
    tag = original["order_tag"]
    assert len(tag.encode("utf-8")) <= 64
    store.close()
    store = ExecutionStore(path)
    store.review()
    record_check(store, outcome="FOUND", resolution="BROKER_CONFIRMED")
    recovered = store.lookup("operation")
    assert recovered is not None
    assert recovered["state"] == "RECONCILED"
    assert recovered["order_tag"] == tag
    assert store.health()["state"] == "READY"
    store.close()
    store = ExecutionStore(path)
    assert store.review()["state"] == "READY"
    store.close()
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT operator_id, previous_state, accounted_facts FROM recovery_audit"
        ).fetchone()
    assert row[:2] == ("system", "UNKNOWN_OUTCOME")
    assert json.loads(row[2]) == {"filled_quantity": 2}


def test_absence_progress_and_monitoring_survive_restarts(tmp_path):
    from moomoo_mcp.services.execution_store import ASSUMED_ABSENT

    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    admit(store)
    store.mark_dispatch("operation", {})
    store.close()
    store = ExecutionStore(path)
    store.review()
    for number in range(1, 3):
        record_check(store, number=number)
    store.close()
    store = ExecutionStore(path)
    store.review()
    for number in range(3, 6):
        record_check(
            store, number=number, resolution=ASSUMED_ABSENT if number == 5 else None
        )
    row = store.lookup("operation")
    assert row is not None
    assert row["state"] == "UNKNOWN_OUTCOME"
    assert len(row["recovery_checks"]) == 5
    assert store.health()["state"] == "READY"
    store.close()
    store = ExecutionStore(path)
    assert store.review()["state"] == "READY"
    assert store.recovery_rows(55) == []
    assert len(store.recovery_rows(56)) == 1
    store.block_late_order("operation")
    assert store.health()["pending_operation_ids"] == ["operation"]
    record_check(store, number=6, outcome="FOUND", resolution="BROKER_CONFIRMED")
    assert store.health()["state"] == "READY"
    store.close()


def test_recovery_audit_failure_rolls_back_check_and_gate_release(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    admit(store)
    store.outcome(
        "operation", "UNKNOWN_OUTCOME", "POSSIBLY_SENT", reason="UNRESOLVED_OUTCOME"
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON recovery_audit "
            "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
        )
    with pytest.raises(ExecutionStoreError):
        record_check(store, outcome="FOUND", resolution="BROKER_CONFIRMED")
    store.close()
    store = ExecutionStore(path)
    assert store.review()["state"] == "JOURNAL_BLOCKED"
    recovered = store.lookup("operation")
    assert recovered is not None
    assert recovered["recovery_checks"] == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recovery_audit").fetchone() == (0,)
    store.close()


def test_restored_backup_refuses_unknown_retired_epoch(tmp_path):
    path = tmp_path / "journal.db"
    backup = tmp_path / "backup.db"
    store = ExecutionStore(path, create=True)
    store.review()
    old_epoch = store.epoch
    admit(store, "old")
    store.outcome("old", "ACKNOWLEDGED", "ACKNOWLEDGED", receipt={"order_id": "1"})
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    admit(store, "after-backup")
    store.close()
    os.replace(backup, path)
    store = ExecutionStore(path)
    try:
        store.review()
        assert store.lookup("old") is not None
        assert store.lookup("after-backup") is None
        with pytest.raises(ExecutionConflict):
            store.admit("after-backup", old_epoch, 123, "PLACE", "canonical", {})
    finally:
        store.close()


def test_runtime_deletion_is_not_recreated_and_concurrent_calls_fail(tmp_path):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    path.unlink()
    try:

        def attempt(n):
            with pytest.raises(ExecutionStoreError):
                admit(store, f"failed-{n}")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(attempt, range(8)))
        assert not path.exists()
        assert store.health()["blocking_reasons"] == ["STORAGE_FAILED"]
    finally:
        store.close()


def test_connections_are_worker_owned_and_closed_per_unit(tmp_path, monkeypatch):
    import threading

    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    original = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.owner = threading.get_ident()
            self.closed = False
            connections.append(self)

        def execute(self, *args, **kwargs):
            assert self.owner == threading.get_ident()
            assert not self.closed
            return super().execute(*args, **kwargs)

        def close(self):
            assert self.owner == threading.get_ident()
            self.closed = True
            return super().close()

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=TrackedConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: admit(store, f"thread-{n}"), range(16)))
        assert len(connections) == 16
        assert all(conn.closed for conn in connections)
    finally:
        store.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_health_detects_directory_that_cannot_create_rollback_journal(tmp_path):
    directory = tmp_path / "storage"
    directory.mkdir()
    path = directory / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    directory.chmod(0o500)
    try:
        assert store.health()["state"] == "JOURNAL_BLOCKED"
        assert store.health()["blocking_reasons"] == ["STORAGE_FAILED"]
    finally:
        directory.chmod(0o700)
        store.close()


def test_newer_wal_schema_does_not_checkpoint_or_rewrite_files(tmp_path):
    path = tmp_path / "journal.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA user_version=999")
        conn.execute("CREATE TABLE future_schema (value TEXT)")
        conn.execute("INSERT INTO future_schema VALUES ('future data')")
        conn.commit()
        files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
        before = {file: file.read_bytes() for file in files}
        with pytest.raises(ExecutionStoreError):
            ExecutionStore(path)
        assert {file: file.read_bytes() for file in files} == before


@pytest.mark.parametrize("version", [1, 2])
def test_migration_preserves_acknowledged_modification(tmp_path, version):
    path = tmp_path / "journal.db"
    store = ExecutionStore(path, create=True)
    store.review()
    old_epoch = store.epoch
    store.admit("modify", old_epoch, 123, "MODIFY", "{}", {"order_id": "77", "qty": 50})
    store.mark_dispatch("modify", {"order_id": "77", "qty": 50, "price": "50"})
    store.outcome("modify", "ACKNOWLEDGED", "ACKNOWLEDGED", receipt={"order_id": "77"})
    store.close()
    with sqlite3.connect(path) as conn:
        if version == 1:
            conn.execute("ALTER TABLE operations DROP COLUMN modification_observed")
        conn.execute("DROP INDEX placement_tag")
        for column in ("order_tag", "recovery_disposition", "next_recovery_at"):
            conn.execute(f"ALTER TABLE operations DROP COLUMN {column}")
        conn.execute("DROP TABLE recovery_checks")
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute(
            "INSERT INTO recovery_audit(operation_id,operator_id,resolution,reason,"
            "evidence_reference,accounted_facts,recovery_epoch,observed_state,"
            "previous_state) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "modify",
                "operator",
                "TERMINAL_ACCOUNTED",
                "legacy audit",
                "broker-order:77",
                '{"filled_quantity":2}',
                old_epoch,
                "UNKNOWN_OUTCOME",
                "UNKNOWN_OUTCOME",
            ),
        )
    migrated = ExecutionStore(path)
    try:
        assert migrated.review()["state"] == "READY"
        pending = migrated.unobserved_modifications(123, "77")
        assert len(pending) == 1
        assert pending[0]["admission_epoch"] == old_epoch
        assert pending[0]["state"] == "ACKNOWLEDGED"
        assert pending[0]["modification_observed"] == 0
        assert pending[0]["order_tag"] is None
        assert json.loads(pending[0]["accounted_facts"]) == {"filled_quantity": 2}
        assert migrated.unobserved_modifications(456, "77") == []
        assert migrated.unobserved_modifications(123, "88") == []
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone() == (3,)
            assert conn.execute(
                "SELECT operator_id FROM recovery_audit"
            ).fetchone() == ("operator",)
    finally:
        migrated.close()
