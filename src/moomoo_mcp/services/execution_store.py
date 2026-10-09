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

SCHEMA_VERSION = 3
ASSUMED_ABSENT = "ASSUMED_NOT_PLACED_AFTER_RETRIES"
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
 modification_observed INTEGER NOT NULL DEFAULT 0,
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
PRAGMA user_version = 2;
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
                if version not in (1, 2, SCHEMA_VERSION):
                    raise ExecutionStoreError(
                        f"Unsupported journal schema {version}; "
                        f"expected {SCHEMA_VERSION}"
                    )
            with self._connection(verify=False) as conn:
                if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ExecutionStoreError("Journal integrity check failed")
                if conn.execute("SELECT environment FROM metadata").fetchall() != [
                    ("SIMULATE",)
                ]:
                    raise ExecutionStoreError("Journal environment is not SIMULATE")
                if version < SCHEMA_VERSION:
                    # Old acknowledgements carry no proof of broker visibility.
                    # Preserve them, conservatively unobserved, in an atomic upgrade.
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        if version == 1:
                            conn.execute(
                                "ALTER TABLE operations ADD COLUMN "
                                "modification_observed "
                                "INTEGER NOT NULL DEFAULT 0"
                            )
                        conn.execute("ALTER TABLE operations ADD COLUMN order_tag TEXT")
                        conn.execute(
                            "CREATE UNIQUE INDEX placement_tag ON operations(order_tag)"
                        )
                        conn.execute(
                            "ALTER TABLE operations ADD COLUMN recovery_disposition "
                            "TEXT"
                        )
                        conn.execute(
                            "ALTER TABLE operations ADD COLUMN next_recovery_at REAL "
                            "NOT NULL DEFAULT 0"
                        )
                        conn.execute("""
CREATE TABLE recovery_checks (
 id INTEGER PRIMARY KEY,
 operation_id TEXT NOT NULL REFERENCES operations(operation_id),
 started_at REAL NOT NULL, completed_at REAL NOT NULL,
 outcome TEXT NOT NULL, details TEXT NOT NULL
)
""")
                        conn.execute(
                            "CREATE INDEX recovery_check_operation ON "
                            "recovery_checks(operation_id)"
                        )
                        conn.execute("PRAGMA user_version = 3")
                        conn.execute("COMMIT")
                    except BaseException:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        raise

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
                    "state IN ('ADMITTED', 'UNKNOWN_OUTCOME') "
                    "AND recovery_disposition IS NULL"
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
        with self._guard:
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
                    and conn.execute("PRAGMA user_version").fetchone()[0]
                    != SCHEMA_VERSION
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
        with self._guard:
            self._closed = True
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None

    @staticmethod
    def _row(conn, operation_id: str) -> dict[str, Any] | None:
        cursor = conn.execute(
            "SELECT operations.*, (SELECT accounted_facts FROM recovery_audit "
            "WHERE recovery_audit.operation_id=operations.operation_id "
            "ORDER BY id DESC LIMIT 1) AS accounted_facts, "
            "EXISTS(SELECT 1 FROM review_requirements WHERE "
            "review_requirements.operation_id=operations.operation_id) "
            "AS recovery_pending "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        row = cursor.fetchone()
        result = (
            dict(zip((col[0] for col in cursor.description), row, strict=True))
            if row
            else None
        )
        if result is not None:
            cursor = conn.execute(
                "SELECT started_at,completed_at,outcome,details FROM recovery_checks "
                "WHERE operation_id=? ORDER BY id",
                (operation_id,),
            )
            result["recovery_checks"] = [
                dict(zip((col[0] for col in cursor.description), check, strict=True))
                for check in cursor.fetchall()
            ]
        return result

    def lookup(self, operation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            return self._row(conn, operation_id)

    def unobserved_modifications(self, account: int, order_id: str) -> list[dict]:
        """Acknowledgement alone does not establish the new broker order fields."""
        with self._connection() as conn:
            identifiers = conn.execute(
                "SELECT operation_id FROM operations WHERE account=? "
                "AND kind='MODIFY' AND state='ACKNOWLEDGED' "
                "AND modification_observed=0",
                (str(account),),
            ).fetchall()
            rows = [self._row(conn, identifier[0]) for identifier in identifiers]
            return [
                row
                for row in rows
                if row is not None
                and int(json.loads(row["request"])["order_id"]) == int(order_id)
            ]

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
                    "operations(operation_id,admission_epoch,account,kind,canonical,fingerprint,request,order_tag,state,disposition)"
                    " VALUES (?,?,?,?,?,?,?,?,'ADMITTED','PENDING_CHECKS')"
                ),
                (
                    operation_id,
                    epoch,
                    str(account),
                    kind,
                    canonical,
                    fingerprint_request(canonical),
                    json.dumps(request, allow_nan=False),
                    str(uuid4()) if kind == "PLACE" else None,
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
        observed_modifications: list[str] | None = None,
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
            if state == "ACKNOWLEDGED" and observed_modifications:
                # Retain the earlier guard until its acknowledged successor is
                # durable. A failed preparation or outcome write cannot retire it.
                conn.executemany(
                    "UPDATE operations SET modification_observed=1 "
                    "WHERE operation_id=? AND kind='MODIFY' AND state='ACKNOWLEDGED'",
                    [(identifier,) for identifier in observed_modifications],
                )

    def review(self) -> dict:
        # Every start reviews all rows plus independently durable requirements.
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO review_requirements SELECT "
                "operation_id, 'STARTUP_REVIEW' FROM operations WHERE "
                "state IN ('ADMITTED','UNKNOWN_OUTCOME') "
                "AND recovery_disposition IS NULL"
            )
        self._reviewed = True
        return self.health()

    def require_ready(self) -> None:
        health = self.health()
        if health["state"] != "READY":
            raise ExecutionConflict(
                f"Paper execution {health['state']}: "
                f"pending operations {health['pending_operation_ids']}; "
                "automatic recovery or healthy storage restart is required; "
                "see check_health/get_execution. No order was sent"
            )

    def recovery_rows(self, due_at: float) -> list[dict]:
        """Pending reviews and assumed placements, excluding in-flight work."""
        with self._connection() as conn:
            identifiers = conn.execute(
                "SELECT operation_id FROM operations WHERE next_recovery_at<=? "
                "AND (operation_id IN (SELECT operation_id FROM review_requirements) "
                "OR recovery_disposition=?) ORDER BY next_recovery_at,created_at",
                (due_at, ASSUMED_ABSENT),
            ).fetchall()
            return [row for item in identifiers if (row := self._row(conn, item[0]))]

    def block_late_order(self, operation_id: str) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO review_requirements VALUES (?, "
                "'LATE_ORDER_FOUND')",
                (operation_id,),
            )

    @staticmethod
    def recovery_context(row: dict) -> dict:
        checks = row["recovery_checks"]
        return {
            "operation_id": row["operation_id"],
            "order_tag": row["order_tag"],
            "disposition": row["recovery_disposition"],
            "pending": bool(row["recovery_pending"]),
            "broker_order_id": row["broker_order_id"],
            "broker_status": row["broker_status"],
            "accounted_facts": json.loads(row["accounted_facts"])
            if row.get("accounted_facts")
            else None,
            "successful_negative_checks": sum(
                c["outcome"] == "NOT_FOUND" for c in checks
            ),
            "required_negative_checks": 5,
            "last_check": {k: v for k, v in checks[-1].items() if k != "details"}
            if checks
            else None,
            "next_check_at": row["next_recovery_at"]
            if row["recovery_pending"] or row["recovery_disposition"] == ASSUMED_ABSENT
            else None,
            "original_operation_replay_allowed": False,
            "absence_proven": False,
        }

    def record_recovery(
        self,
        operation_id: str,
        *,
        observed_state: str,
        started_at: float,
        completed_at: float,
        outcome: str,
        details: dict,
        next_check_at: float,
        resolution: str | None = None,
        receipt: dict | None = None,
        accounted_facts: dict | None = None,
    ) -> None:
        """Check, disposition, audit and gate release form one durable transaction."""
        with self._transaction() as conn:
            row = self._row(conn, operation_id)
            if not row or row["state"] != observed_state:
                raise ExecutionConflict("Recovery observed stale operation state")
            conn.execute(
                "INSERT INTO "
                "recovery_checks(operation_id,started_at,completed_at,outcome,details) "
                "VALUES (?,?,?,?,?)",
                (
                    operation_id,
                    started_at,
                    completed_at,
                    outcome,
                    json.dumps(details, allow_nan=False),
                ),
            )
            conn.execute(
                "UPDATE operations SET "
                "evidence=?,next_recovery_at=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE operation_id=?",
                (
                    json.dumps(details.get("orders", []), allow_nan=False),
                    next_check_at,
                    operation_id,
                ),
            )
            if resolution is None:
                return
            if resolution == ASSUMED_ABSENT:
                count = conn.execute(
                    "SELECT COUNT(*) FROM recovery_checks WHERE operation_id=? AND "
                    "outcome='NOT_FOUND'",
                    (operation_id,),
                ).fetchone()[0]
                if (
                    row["kind"] != "PLACE"
                    or row["state"] not in {"ADMITTED", "UNKNOWN_OUTCOME"}
                    or not row["order_tag"]
                    or row["broker_order_id"]
                    or any(
                        json.loads(check["details"]).get("correlated_order_seen")
                        for check in row["recovery_checks"]
                    )
                    or count < 5
                    or row["recovery_disposition"] is not None
                ):
                    raise ExecutionConflict(
                        "Assumed absence requires five tagged placement negatives"
                    )
                state, disposition = "UNKNOWN_OUTCOME", "POSSIBLY_SENT"
            elif resolution in {
                "BROKER_CONFIRMED",
                "CURRENT_STATE_ACCOUNTED",
                "LATE_BROKER_ORDER_FOUND",
                "TERMINAL_ACCOUNTED",
            }:
                if not receipt or not accounted_facts:
                    raise ExecutionConflict(
                        "Broker recovery requires order and position evidence"
                    )
                state = (
                    "TERMINAL_ACCOUNTED"
                    if resolution == "TERMINAL_ACCOUNTED"
                    else "RECONCILED"
                )
                disposition = "BROKER_ACCOUNTED"
            else:
                raise ExecutionConflict("Unsupported recovery disposition")
            conn.execute(
                "INSERT INTO "
                "recovery_audit(operation_id,operator_id,resolution,reason,evidence_reference,"
                "accounted_facts,recovery_epoch,observed_state,previous_state) VALUES "
                "(?,?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    "system",
                    resolution,
                    details.get("reason", outcome),
                    "broker-order:" + str(receipt["order_id"])
                    if receipt
                    else "order-tag:" + row["order_tag"],
                    json.dumps(accounted_facts or {}, allow_nan=False),
                    self.epoch,
                    observed_state,
                    row["state"],
                ),
            )
            conn.execute(
                "UPDATE operations SET state=?,disposition=?,recovery_disposition=?,"
                "receipt=COALESCE(?,receipt),broker_order_id=COALESCE(?,broker_order_id),"
                "broker_status=COALESCE(?,broker_status),modification_observed=? "
                "WHERE operation_id=?",
                (
                    state,
                    disposition,
                    resolution,
                    json.dumps(receipt, allow_nan=False) if receipt else None,
                    str(receipt["order_id"]) if receipt else None,
                    str(receipt["order_status"]) if receipt else None,
                    int(row["kind"] == "MODIFY"),
                    operation_id,
                ),
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
            "pending_operation_ids": [],
            "recovery_updates": [],
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
                result["pending_operation_ids"] = sorted({r[0] for r in reviews})
                recent = conn.execute(
                    "SELECT operation_id FROM operations WHERE recovery_disposition "
                    "IS NOT NULL "
                    "ORDER BY updated_at DESC,rowid DESC LIMIT 20"
                ).fetchall()
                identifiers = list(
                    dict.fromkeys(
                        result["pending_operation_ids"] + [r[0] for r in recent]
                    )
                )
                result["recovery_updates"] = [
                    self.recovery_context(row)
                    for identifier in identifiers
                    if (row := self._row(conn, identifier))
                ]
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
