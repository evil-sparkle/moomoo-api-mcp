"""Durable paper execution records. No connection or transaction leaves this module."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from moomoo_mcp.services.execution_identity import fingerprint_request

SCHEMA_VERSION = 1
TERMINAL = ("ACKNOWLEDGED", "RECONCILED", "REFUSED", "TERMINAL_ACCOUNTED")


class ExecutionStoreError(RuntimeError):
    """Storage cannot be trusted again until a healthy restart."""


class ExecutionConflict(ValueError):
    """An immutable operation token cannot be reassigned."""


class ExecutionStore:
    def __init__(
        self, path: str | Path, *, create: bool = False, lock_wait_ms: int = 5000
    ):
        self._path = Path(path).absolute()
        self._wait = lock_wait_ms
        self._failed: str | None = None
        self._guard = threading.RLock()
        self._lock_fd: int | None = None
        self.epoch = str(uuid4())
        self._reviewed = False
        self._closed = False
        if not 1 <= lock_wait_ms <= 60000:
            raise ValueError(
                "journal lock wait must be between 1 and 60000 milliseconds"
            )
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(
                self._path.parent / "execution.lock", os.O_CREAT | os.O_RDWR, 0o600
            )
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            exists = self._path.exists()
            if not exists and not create:
                raise ExecutionStoreError(
                    f"Missing journal: {self._path}; explicit initialization required"
                )
            if not exists:
                # O_EXCL prevents initialization racing an unexpected replacement.
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                with self._connection(verify=False) as conn:
                    conn.executescript("""
BEGIN IMMEDIATE;
CREATE TABLE metadata (environment TEXT NOT NULL CHECK (environment = 'SIMULATE'));
INSERT INTO metadata VALUES ('SIMULATE');
CREATE TABLE epochs (epoch TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE operations (
 operation_id TEXT PRIMARY KEY, admission_epoch TEXT NOT NULL REFERENCES epochs(epoch),
 account TEXT NOT NULL CHECK (account != '0'), kind TEXT NOT NULL,
 canonical TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL,
 state TEXT NOT NULL, disposition TEXT NOT NULL,
 merged_request TEXT, receipt TEXT, broker_order_id TEXT, broker_status TEXT,
 evidence TEXT, error TEXT, marker INTEGER NOT NULL DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE review_requirements (
 operation_id TEXT NOT NULL REFERENCES operations(operation_id), reason TEXT NOT NULL,
 PRIMARY KEY (operation_id, reason)
);
CREATE TABLE recovery_audit (
 id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
 operator_id TEXT NOT NULL, resolution TEXT NOT NULL, reason TEXT NOT NULL,
 evidence_reference TEXT NOT NULL, accounted_facts TEXT NOT NULL,
 recovery_epoch TEXT NOT NULL,
 observed_state TEXT NOT NULL, previous_state TEXT NOT NULL,
 timestamp TEXT DEFAULT CURRENT_TIMESTAMP
);
PRAGMA user_version = 1;
COMMIT;
""")
                directory = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            # Probe a private, quiescent copy, including recovery sidecars. Even a
            # read-only SQLite connection can modify an existing WAL shared index.
            # The executor lock excludes supported writers during this snapshot.
            with tempfile.TemporaryDirectory(prefix="moomoo-schema-") as directory:
                probe = Path(directory) / "execution.db"
                shutil.copyfile(self._path, probe)
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = Path(str(self._path) + suffix)
                    if sidecar.exists():
                        shutil.copyfile(sidecar, Path(str(probe) + suffix))
                connection = sqlite3.connect(probe)
                try:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    connection.close()
                if version != SCHEMA_VERSION:
                    raise ExecutionStoreError(
                        f"Unsupported journal schema {version}; "
                        f"expected {SCHEMA_VERSION}"
                    )
            with self._connection() as conn:
                if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ExecutionStoreError("Journal integrity check failed")
                if conn.execute("SELECT environment FROM metadata").fetchall() != [
                    ("SIMULATE",)
                ]:
                    raise ExecutionStoreError("Journal environment is not SIMULATE")
            with self._transaction() as conn:
                conn.execute("INSERT INTO epochs(epoch) VALUES (?)", (self.epoch,))
                conn.execute(
                    "INSERT OR IGNORE INTO review_requirements SELECT "
                    "operation_id, 'RECOVERED_DISPATCH' FROM operations WHERE"
                    " state = 'DISPATCHING'"
                )
                conn.execute(
                    "UPDATE operations SET state='UNKNOWN_OUTCOME', "
                    "disposition='POSSIBLY_SENT', error='Recovered dispatch "
                    "without durable outcome' WHERE state='DISPATCHING'"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO review_requirements SELECT "
                    "operation_id, 'STARTUP_REVIEW' FROM operations WHERE "
                    "state IN ('ADMITTED', 'UNKNOWN_OUTCOME')"
                )
        except BaseException as exc:
            self.close()
            if isinstance(exc, BlockingIOError):
                raise ExecutionStoreError(
                    "Another process holds the execution store"
                ) from exc
            raise

    @contextmanager
    def _connection(self, *, verify: bool = True):
        if self._closed:
            raise ExecutionStoreError("Execution store is closed")
        if self._failed:
            raise ExecutionStoreError(self._failed)
        conn = None
        try:
            # mode=rw: deletion during runtime must never recreate an empty DB.
            conn = sqlite3.connect(
                self._path.as_uri() + "?mode=rw",
                uri=True,
                isolation_level=None,
                timeout=self._wait / 1000,
            )
            conn.execute(f"PRAGMA busy_timeout = {self._wait}")
            if (
                verify
                and conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            ):
                raise sqlite3.DatabaseError("Journal schema changed during runtime")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("PRAGMA synchronous = EXTRA")
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        except (sqlite3.Error, OSError) as exc:
            self._failed = (
                f"STORAGE_FAILED: {exc}; "
                "restart with healthy storage and review recovery"
            )
            raise ExecutionStoreError(self._failed) from exc
        finally:
            if conn is not None:
                conn.close()

    @contextmanager
    def _transaction(self):
        with self._guard, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._closed = True
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    @staticmethod
    def _row(conn, operation_id: str) -> dict[str, Any] | None:
        cursor = conn.execute(
            "SELECT operations.*, (SELECT accounted_facts FROM recovery_audit "
            "WHERE recovery_audit.operation_id=operations.operation_id "
            "ORDER BY id DESC LIMIT 1) AS accounted_facts "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        row = cursor.fetchone()
        return (
            dict(zip((col[0] for col in cursor.description), row, strict=True))
            if row
            else None
        )

    def lookup(self, operation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            return self._row(conn, operation_id)

    def admit(
        self,
        operation_id: str,
        epoch: str,
        account: int,
        kind: str,
        canonical: str,
        request: dict,
    ) -> tuple[dict, bool]:
        with self._transaction() as conn:
            existing = self._row(conn, operation_id)
            if existing:
                return existing, False
            if epoch != self.epoch:
                raise ExecutionConflict(
                    "Unknown non-current-epoch token cannot be accounted for;"
                    " do not refresh it"
                )
            if (
                not self._reviewed
                or conn.execute("SELECT 1 FROM review_requirements LIMIT 1").fetchone()
            ):
                raise ExecutionConflict(
                    "Recovery review is outstanding or paper execution is blocked"
                )
            conn.execute(
                (
                    "INSERT INTO "
                    "operations(operation_id,admission_epoch,account,kind,canonical,fingerprint,request,state,disposition)"
                    " VALUES (?,?,?,?,?,?,?,'ADMITTED','PENDING_CHECKS')"
                ),
                (
                    operation_id,
                    epoch,
                    str(account),
                    kind,
                    canonical,
                    fingerprint_request(canonical),
                    json.dumps(request, allow_nan=False),
                ),
            )
            row = self._row(conn, operation_id)
            assert row is not None
            return row, True

    def mark_dispatch(self, operation_id: str, merged: dict) -> None:
        with self._transaction() as conn:
            cursor = conn.execute(
                (
                    "UPDATE operations SET "
                    "state='DISPATCHING',disposition='IN_FLIGHT',marker=1,merged_request=?"
                    " WHERE operation_id=? AND state='ADMITTED'"
                ),
                (json.dumps(merged, allow_nan=False), operation_id),
            )
            if cursor.rowcount != 1:
                raise ExecutionConflict("Operation is not awaiting dispatch")

    def outcome(
        self,
        operation_id: str,
        state: str,
        disposition: str,
        *,
        receipt: dict | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> None:
        encoded = json.dumps(receipt, allow_nan=False) if receipt is not None else None
        with self._transaction() as conn:
            conn.execute(
                (
                    "UPDATE operations SET "
                    "state=?,disposition=?,receipt=?,broker_order_id=COALESCE(?,broker_order_id),broker_status=COALESCE(?,broker_status),error=?,updated_at=CURRENT_TIMESTAMP"
                    " WHERE operation_id=?"
                ),
                (
                    state,
                    disposition,
                    encoded,
                    str(receipt["order_id"])
                    if receipt and receipt.get("order_id")
                    else None,
                    str(receipt["order_status"])
                    if receipt and receipt.get("order_status")
                    else None,
                    error,
                    operation_id,
                ),
            )
            if reason:
                conn.execute(
                    "INSERT OR IGNORE INTO review_requirements VALUES (?,?)",
                    (operation_id, reason),
                )

    def review(self) -> dict:
        # Every start reviews all rows plus independently durable requirements.
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO review_requirements SELECT "
                "operation_id, 'STARTUP_REVIEW' FROM operations WHERE "
                "state IN ('ADMITTED','UNKNOWN_OUTCOME')"
            )
        self._reviewed = True
        return self.health()

    def require_ready(self) -> None:
        health = self.health()
        if health["state"] != "READY":
            raise ExecutionConflict(
                f"Paper execution {health['state']}: "
                "recovery review or unresolved operations; no order was sent"
            )

    def record_evidence(
        self, operation_id: str, observations: list[dict], *, reconcile: bool = False
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE operations SET evidence=? WHERE operation_id=?",
                (json.dumps(observations, allow_nan=False), operation_id),
            )
            if reconcile:
                row = self._row(conn, operation_id)
                if (
                    row is None
                    or row["state"] != "UNKNOWN_OUTCOME"
                    or row["kind"] != "PLACE"
                ):
                    raise ExecutionConflict(
                        "Only an uncertain placement with reliable identity can "
                        "reconcile"
                    )
                conn.execute(
                    (
                        "UPDATE operations SET "
                        "state='RECONCILED',disposition='RECONCILED',broker_status=?"
                        " WHERE operation_id=?"
                    ),
                    (str(observations[0].get("order_status", "")), operation_id),
                )
                conn.execute(
                    (
                        "DELETE FROM review_requirements WHERE operation_id=? AND"
                        " reason IN ('UNRESOLVED_OUTCOME','STARTUP_REVIEW')"
                    ),
                    (operation_id,),
                )

    def acknowledge(
        self,
        operation_id: str,
        *,
        operator_id: str,
        recovery_epoch: str,
        observed_state: str,
        reason: str,
        evidence_reference: str,
        accounted_facts: dict,
    ) -> None:
        with self._transaction() as conn:
            row = self._row(conn, operation_id)
            if (
                not row
                or recovery_epoch != self.epoch
                or observed_state != row["state"]
            ):
                raise ExecutionConflict(
                    "Stale recovery epoch or observed operation state"
                )
            if not conn.execute(
                "SELECT 1 FROM review_requirements WHERE operation_id=?",
                (operation_id,),
            ).fetchone():
                raise ExecutionConflict(
                    "Operation has no outstanding recovery requirement"
                )
            conn.execute(
                (
                    "INSERT INTO "
                    "recovery_audit(operation_id,operator_id,resolution,reason,evidence_reference,accounted_facts,recovery_epoch,observed_state,previous_state)"
                    " VALUES (?,?,'TERMINAL_ACCOUNTED',?,?,?,?,?,?)"
                ),
                (
                    operation_id,
                    operator_id,
                    reason,
                    evidence_reference,
                    json.dumps(accounted_facts, allow_nan=False),
                    recovery_epoch,
                    observed_state,
                    row["state"],
                ),
            )
            conn.execute(
                (
                    "UPDATE operations SET "
                    "state='TERMINAL_ACCOUNTED',disposition='OPERATOR_ACCOUNTED'"
                    " WHERE operation_id=?"
                ),
                (operation_id,),
            )
            conn.execute(
                "DELETE FROM review_requirements WHERE operation_id=?", (operation_id,)
            )

    def health(self) -> dict:
        result: dict[str, Any] = {
            "state": "REVIEW_PENDING",
            "schema_version": SCHEMA_VERSION,
            "admission_epoch": self.epoch,
            "recovery_epoch": self.epoch,
            "in_flight": 0,
            "awaiting_review": 0,
            "blocking": 0,
            "blocking_reasons": [],
            "recovery_review_outstanding": not self._reviewed,
        }
        try:
            if not os.access(self._path, os.W_OK) or not os.access(
                self._path.parent, os.W_OK | os.X_OK
            ):
                self._failed = (
                    "STORAGE_FAILED: journal file or directory is not writable"
                )
                raise ExecutionStoreError(self._failed)
            with self._connection() as conn:
                # Probe writability with a bounded transaction, without changing rows.
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE metadata SET environment=environment")
                conn.execute("ROLLBACK")
                result["in_flight"] = conn.execute(
                    "SELECT COUNT(*) FROM operations WHERE state='DISPATCHING'"
                ).fetchone()[0]
                reviews = conn.execute(
                    "SELECT operation_id,reason FROM review_requirements"
                ).fetchall()
                result["awaiting_review"] = len(
                    {r[0] for r in reviews if r[1] == "STARTUP_REVIEW"}
                )
                blocks = [
                    (op, reason) for op, reason in reviews if reason != "STARTUP_REVIEW"
                ]
                result["blocking"] = len({r[0] for r in blocks})
                result["blocking_reasons"] = sorted({r[1] for r in blocks})
                result["recovery_review_outstanding"] = not self._reviewed or bool(
                    reviews
                )
                result["state"] = (
                    "JOURNAL_BLOCKED"
                    if blocks
                    else "REVIEW_PENDING"
                    if result["recovery_review_outstanding"]
                    else "READY"
                )
        except ExecutionStoreError as exc:
            result.update(
                state="JOURNAL_BLOCKED",
                storage_error=str(exc),
                blocking_reasons=["STORAGE_FAILED"],
            )
        return result
