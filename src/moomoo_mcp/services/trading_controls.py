"""Durable operator decisions and conservative daily submission accounting."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import uuid4

from moomoo_mcp.services.instrument_rules import normalize_order_session


class TradingControlError(RuntimeError):
    """A control refused a mutation before the broker dispatch boundary."""


def _sum_exact(values: list[Decimal]) -> Decimal:
    """Preserve integer and fractional digits for arbitrary whole quantities."""
    integer_digits = max((max(value.adjusted() + 1, 1) for value in values), default=1)
    fractional_digits = max(
        (max(-int(value.as_tuple().exponent), 0) for value in values),
        default=0,
    )
    with localcontext() as context:
        context.prec = max(
            28,
            integer_digits + fractional_digits + len(str(len(values))) + 1,
        )
        return sum(values, Decimal(0))


def _number(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Prepared request numbers must be finite")
        rendered = format(number, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return {"number": "0" if rendered == "-0" else rendered}
    if isinstance(value, dict):
        return {key: _number(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_number(item) for item in value]
    return value


def canonical_intent(method: str, parameters: dict) -> str:
    """Bind defaults, routing and caller identity, excluding approval transport."""
    params = dict(parameters)
    params.pop("approval_id", None)
    if "modify_order_op" in params:
        params["modify_order_op"] = str(params["modify_order_op"]).strip().upper()
    if "acc_id" in params:
        value = str(params["acc_id"])
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Prepared account must be an exact decimal identifier")
        params["acc_id"] = str(int(value))
    if "session" in params:
        params["session"] = normalize_order_session(params["session"], trd_env="REAL")
    if params.get("session") in {None, "N/A", "NONE"} or (
        params.get("trd_env") == "SIMULATE" and params.get("session") == "RTH"
    ):
        params.pop("session", None)
    encoded = json.dumps(
        {"method": method, "parameters": _number(params)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode()) > 65536:
        raise ValueError("Prepared request exceeds 64 KiB")
    return encoded


def contract_view(facts) -> dict:
    """The order being reviewed, separate from changing market quotes."""
    value = _number(
        {
            "order_type": facts.order_type,
            "trd_side": facts.trd_side,
            "qty": facts.qty,
            "price": facts.price,
            "aux_price": facts.aux_price,
            "is_combo": facts.is_combo,
            "legs": [
                {
                    "code": leg.instrument.code,
                    "qty_ratio": leg.qty_ratio,
                    "monetary_multiplier": leg.instrument.monetary_multiplier,
                }
                for leg in facts.legs
            ],
        }
    )
    assert isinstance(value, dict)
    return value


class TradingControls:
    """One process owns this store; operator requests use the same service."""

    def __init__(
        self,
        path: str | Path,
        *,
        create: bool = False,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(path).absolute()
        self.clock = clock
        self._guard = threading.RLock()
        self._closed = False
        self._failed = False
        if not self.path.exists():
            if not create:
                raise TradingControlError("Trading controls require initialization")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with sqlite3.connect(self.path) as connection:
                connection.executescript("""
CREATE TABLE metadata (version INTEGER NOT NULL);
INSERT INTO metadata VALUES (1);
CREATE TABLE prepared (
 request_id TEXT PRIMARY KEY, canonical TEXT NOT NULL, parameters TEXT NOT NULL,
 review TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 status TEXT NOT NULL, operator_id TEXT, reason TEXT, decided_at REAL,
 consumed_at REAL
);
CREATE TABLE charges (
 environment TEXT NOT NULL, account TEXT NOT NULL, operation_id TEXT NOT NULL,
 canonical TEXT NOT NULL, day TEXT NOT NULL, currency TEXT, amount TEXT NOT NULL,
 approval_id TEXT, created_at REAL NOT NULL,
 PRIMARY KEY (environment, account, operation_id)
);
CREATE INDEX daily_charge ON charges(day, environment, account, currency);
""")
        try:
            with sqlite3.connect(self.path) as connection:
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise TradingControlError("Trading controls integrity check failed")
                if connection.execute("SELECT version FROM metadata").fetchall() != [
                    (1,)
                ]:
                    raise TradingControlError("Trading controls schema is unsupported")
                connection.execute(
                    "SELECT request_id, canonical, parameters, review, created_at, "
                    "expires_at, status, operator_id, reason, decided_at, consumed_at "
                    "FROM prepared LIMIT 0"
                )
                connection.execute(
                    "SELECT environment, account, operation_id, canonical, day, "
                    "currency, amount, approval_id, created_at FROM charges LIMIT 0"
                )
        except sqlite3.Error as exc:
            raise TradingControlError(
                "Trading controls storage is unavailable"
            ) from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._guard:
            if self._closed or self._failed:
                raise TradingControlError("Trading controls storage is blocked")
            connection = None
            try:
                connection = sqlite3.connect(self.path, timeout=5)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except sqlite3.Error as exc:
                self._failed = True
                raise TradingControlError(
                    "Trading controls storage is blocked"
                ) from exc
            finally:
                if connection is not None:
                    connection.close()

    def close(self) -> None:
        with self._guard:
            self._closed = True

    def health(self) -> dict:
        return {"state": "BLOCKED" if self._closed or self._failed else "READY"}

    @staticmethod
    def _view(row: sqlite3.Row) -> dict:
        parameters = json.loads(row["parameters"])
        method = parameters.pop("method")
        return {
            "request_id": row["request_id"],
            "method": method,
            "parameters": parameters,
            "review": json.loads(row["review"]),
            "fingerprint": hashlib.sha256(row["canonical"].encode()).hexdigest(),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "status": row["status"],
            "operator_id": row["operator_id"],
            "reason": row["reason"],
            "decided_at": row["decided_at"],
            "consumed_at": row["consumed_at"],
        }

    def prepare(
        self, method: str, parameters: dict, review: dict, *, ttl_seconds: int = 300
    ) -> dict:
        if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 900:
            raise ValueError("Approval expiry must be between 1 and 900 seconds")
        canonical = canonical_intent(method, parameters)
        payload = json.dumps({"method": method, **parameters}, allow_nan=False)
        evidence = json.dumps(review, allow_nan=False)
        if len(evidence.encode()) > 65536:
            raise ValueError("Prepared review exceeds 64 KiB")
        now = self.clock()
        identifier = str(uuid4())
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO prepared VALUES (?, ?, ?, ?, ?, ?, 'PENDING', "
                "NULL, NULL, NULL, NULL)",
                (identifier, canonical, payload, evidence, now, now + ttl_seconds),
            )
            row = connection.execute(
                "SELECT * FROM prepared WHERE request_id=?", (identifier,)
            ).fetchone()
            assert row is not None
            return self._view(row)

    def get(self, request_id: str) -> dict:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM prepared WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Prepared request does not exist")
            result = self._view(row)
        if (
            result["status"] in {"PENDING", "APPROVED"}
            and self.clock() >= result["expires_at"]
        ):
            result["status"] = "EXPIRED"
        return result

    def decide(
        self, request_id: str, decision: str, *, operator_id: str, reason: str
    ) -> dict:
        if decision not in {"APPROVED", "REJECTED"}:
            raise ValueError("Decision must be APPROVED or REJECTED")
        if not operator_id or len(operator_id) > 128:
            raise ValueError("Operator identity is required and bounded")
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("A decision reason of at most 2000 characters is required")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM prepared WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Prepared request does not exist")
            if row["status"] != "PENDING" or self.clock() >= row["expires_at"]:
                raise TradingControlError("Prepared decision is final or expired")
            connection.execute(
                "UPDATE prepared SET status=?, operator_id=?, reason=?, decided_at=? "
                "WHERE request_id=?",
                (decision, operator_id, reason, self.clock(), request_id),
            )
            updated = connection.execute(
                "SELECT * FROM prepared WHERE request_id=?", (request_id,)
            ).fetchone()
            assert updated is not None
            return self._view(updated)

    def _approved(
        self,
        connection: sqlite3.Connection,
        approval_id: str | None,
        canonical: str,
        contract: dict | None = None,
        *,
        verify_contract: bool = False,
    ) -> sqlite3.Row:
        if not approval_id:
            raise TradingControlError("A trusted operator approval is required")
        row = connection.execute(
            "SELECT * FROM prepared WHERE request_id=?", (approval_id,)
        ).fetchone()
        if row is None or row["canonical"] != canonical:
            raise TradingControlError("Approval does not match the exact request")
        if row["status"] != "APPROVED" or self.clock() >= row["expires_at"]:
            raise TradingControlError(
                "Approval is missing, rejected, consumed or expired"
            )
        expected = json.loads(row["review"]).get("contract")
        if verify_contract and expected != contract:
            raise TradingControlError("Approved target contract changed; prepare again")
        return row

    def check_approval(
        self, method: str, parameters: dict, approval_id: str | None
    ) -> None:
        with self._transaction() as connection:
            self._approved(
                connection, approval_id, canonical_intent(method, parameters)
            )

    def consume(
        self,
        method: str,
        parameters: dict,
        *,
        approval_id: str | None,
        require_approval: bool,
        currency: str | None,
        amount: Decimal,
        caps: Mapping[str, float],
        contract: dict | None = None,
    ) -> None:
        canonical = canonical_intent(method, parameters)
        environment = parameters["trd_env"]
        account = str(int(str(parameters["acc_id"])))
        operation = parameters["operation_id"]
        if not operation or not parameters.get("admission_epoch"):
            raise TradingControlError(
                "Trading controls require durable operation identity"
            )
        if not amount.is_finite() or amount < 0:
            raise TradingControlError("Daily budget valuation is invalid")
        with self._transaction() as connection:
            now = self.clock()
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            existing = connection.execute(
                "SELECT canonical FROM charges WHERE environment=? AND account=? "
                "AND operation_id=?",
                (environment, account, operation),
            ).fetchone()
            if existing is not None:
                if existing["canonical"] != canonical:
                    raise TradingControlError(
                        "Daily budget operation identity conflict"
                    )
                raise TradingControlError(
                    "Trading control reservation is already consumed"
                )
            if require_approval:
                self._approved(
                    connection, approval_id, canonical, contract, verify_contract=True
                )
            if caps and amount > 0:
                if currency is None or currency not in caps:
                    raise TradingControlError(
                        "Daily budget currency cannot be established or is uncapped"
                    )
                used = _sum_exact(
                    [
                        Decimal(row[0])
                        for row in connection.execute(
                            "SELECT amount FROM charges WHERE day=? AND environment=? "
                            "AND account=? AND currency=?",
                            (day, environment, account, currency),
                        )
                    ],
                )
                charged = _sum_exact([used, amount])
                if charged > Decimal(str(caps[currency])):
                    raise TradingControlError(
                        f"Daily submission budget exceeded: {charged} {currency}"
                    )
            connection.execute(
                "INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    environment,
                    account,
                    operation,
                    canonical,
                    day,
                    currency,
                    str(amount),
                    approval_id,
                    now,
                ),
            )
            if require_approval:
                connection.execute(
                    "UPDATE prepared SET status='CONSUMED', consumed_at=? "
                    "WHERE request_id=?",
                    (now, approval_id),
                )

    def confirm_reservation(
        self,
        method: str,
        parameters: dict,
        *,
        approval_id: str | None,
        require_approval: bool,
        contract: dict | None = None,
    ) -> None:
        """Recheck the reservation after unlock, immediately before journal dispatch."""
        canonical = canonical_intent(method, parameters)
        with self._transaction() as connection:
            charge = connection.execute(
                "SELECT * FROM charges WHERE environment=? AND account=? "
                "AND operation_id=?",
                (
                    parameters["trd_env"],
                    str(int(str(parameters["acc_id"]))),
                    parameters["operation_id"],
                ),
            ).fetchone()
            if charge is None or charge["canonical"] != canonical:
                raise TradingControlError("Trading control reservation is unavailable")
            day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
            if charge["day"] != day:
                raise TradingControlError(
                    "Daily reservation expired at the UTC day boundary"
                )
            if require_approval:
                row = connection.execute(
                    "SELECT * FROM prepared WHERE request_id=?",
                    (approval_id,),
                ).fetchone()
                if (
                    row is None
                    or row["canonical"] != canonical
                    or row["status"] != "CONSUMED"
                    or charge["approval_id"] != approval_id
                    or self.clock() >= row["expires_at"]
                ):
                    raise TradingControlError("Approval expired before broker dispatch")
                if json.loads(row["review"]).get("contract") != contract:
                    raise TradingControlError(
                        "Approved target contract changed; prepare again"
                    )

    def usage(self, environment: str, account: str) -> dict:
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT currency, amount FROM charges WHERE day=? "
                "AND environment=? AND account=?",
                (day, environment, account),
            ).fetchall()
        totals: dict[str, Decimal] = {}
        for row in rows:
            if row["currency"]:
                totals[row["currency"]] = _sum_exact(
                    [
                        totals.get(row["currency"], Decimal(0)),
                        Decimal(row["amount"]),
                    ]
                )
        return {
            "day": day,
            "timezone": "UTC",
            "amounts": {key: str(value) for key, value in totals.items()},
        }
