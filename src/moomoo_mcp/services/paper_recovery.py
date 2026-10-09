"""Read-only, process-owned recovery of durable paper execution records."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from moomoo_mcp.services.execution_store import ASSUMED_ABSENT, ExecutionStoreError
from moomoo_mcp.tools.serialization import serialize_identifiers

if TYPE_CHECKING:
    from moomoo_mcp.services.paper_execution import PaperExecution

logger = logging.getLogger(__name__)
NEGATIVE_GAPS = (5, 5, 10, 20)
MONITOR_INTERVAL = 60
TERMINAL_BROKER = frozenset(
    {"FILLED_ALL", "CANCELLED_ALL", "CANCELLED_PART", "FAILED", "DELETED", "REJECTED"}
)
ACTIVE_BROKER = frozenset(
    {
        "UNSUBMITTED",
        "WAITING_SUBMIT",
        "SUBMITTING",
        "SUBMIT_FAILED",
        "SUBMITTED",
        "FILLED_PART",
        "CANCELLING_PART",
        "CANCELLING_ALL",
        "DISABLED",
    }
)


class RecoveryEvidenceError(ValueError):
    """Incomplete, conflicting or ambiguous evidence is not a negative check."""


def number(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise RecoveryEvidenceError("Missing or invalid numeric broker evidence")
    try:
        result = Decimal(str(value))
    except ArithmeticError as exc:
        raise RecoveryEvidenceError("Invalid numeric broker evidence") from exc
    if not result.is_finite():
        raise RecoveryEvidenceError("Nonfinite broker evidence")
    return result


class PaperRecovery:
    def __init__(self, paper: PaperExecution, clock: Callable[[], float] = time.time):
        self.paper = paper
        self.clock = clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="paper-recovery", daemon=True
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.run_once()
            except Exception:
                logger.exception("Paper recovery remains pending")
            self._wake.wait(timeout=1)

    def run_once(self) -> None:
        if self.paper._late_failure:
            return  # A local storage failure must be recovered after restart.
        for row in self.paper.store.recovery_rows(self.clock()):
            if self._stop.is_set():
                return
            self.check(row["operation_id"])

    def _observations(self, row: dict) -> list[dict]:
        service = self.paper.service
        account = int(row["account"])
        # Include a date on either side of UTC admission/current dates to cover
        # broker-market timezone boundaries. History retains its provider limits.
        start = date.fromisoformat(row["created_at"].split(" ")[0]) - timedelta(days=1)
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        current = service.get_orders(
            trd_env="SIMULATE", acc_id=account, refresh_cache=True
        )
        history = service.get_history_orders(
            trd_env="SIMULATE",
            acc_id=account,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        if not isinstance(current, list) or not isinstance(history, list):
            raise RecoveryEvidenceError("Order queries did not return complete lists")
        observations = serialize_identifiers(current + history)
        for order in observations:
            if not isinstance(order, dict):
                raise RecoveryEvidenceError("Malformed broker order")
            identity = order.get("order_id")
            if (
                not isinstance(identity, str)
                or not identity.isascii()
                or not identity.isdecimal()
                or int(identity) <= 0
            ):
                raise RecoveryEvidenceError("Missing broker order identity")
            if order.get("trd_env", "SIMULATE") != "SIMULATE" or str(
                order.get("acc_id", account)
            ) != str(account):
                raise RecoveryEvidenceError("Broker query account/environment mismatch")
        return observations

    @staticmethod
    def _matches(row: dict, observations: list[dict]) -> list[dict]:
        request = json.loads(row["request"])
        target = (
            row["broker_order_id"] if row["kind"] == "PLACE" else request["order_id"]
        )
        return [
            order
            for order in observations
            if (target and order["order_id"] == str(target))
            or (row["order_tag"] and order.get("remark") == row["order_tag"])
        ]

    def _account(self, row: dict, matches: list[dict]) -> tuple[dict, dict, str] | None:
        if len({order["order_id"] for order in matches}) != 1:
            raise RecoveryEvidenceError("Correlation identifies multiple broker orders")
        final = matches[0]
        for order in matches:
            if row["order_tag"] and (
                order.get("remark") not in {None, "", row["order_tag"]}
                or (
                    not row["broker_order_id"]
                    and order.get("remark") != row["order_tag"]
                )
            ):
                raise RecoveryEvidenceError(
                    "Broker remark disagrees with persisted tag"
                )
            for key in ("order_status", "code", "trd_side", "order_type"):
                if order.get(key) != final.get(key) or final.get(key) is None:
                    raise RecoveryEvidenceError(
                        "Broker observations disagree or lack required fields"
                    )
            for key in ("qty", "price", "dealt_qty", "dealt_avg_price"):
                if number(order.get(key)) != number(final.get(key)):
                    raise RecoveryEvidenceError("Broker observations disagree")
        if row["broker_order_id"] and final["order_id"] != row["broker_order_id"]:
            raise RecoveryEvidenceError("Tag disagrees with recorded broker identity")
        request = json.loads(row["request"])
        expected = json.loads(row["merged_request"] or row["request"])
        status = final["order_status"]
        qty, price = number(final.get("qty")), number(final.get("price"))
        filled, average = (
            number(final.get("dealt_qty")),
            number(final.get("dealt_avg_price")),
        )
        if (
            status not in TERMINAL_BROKER | ACTIVE_BROKER
            or qty <= 0
            or qty != int(qty)
            or price <= 0
            or filled < 0
            or filled > qty
            or average < 0
        ):
            raise RecoveryEvidenceError("Invalid broker status or fill evidence")
        if (
            not str(final["code"]).startswith("US.")
            or final["trd_side"] not in {"BUY", "SELL"}
            or final["order_type"] != "NORMAL"
        ):
            raise RecoveryEvidenceError("Broker target outside paper scope")
        if row["kind"] == "PLACE":
            if (
                final["code"] != request["code"]
                or final["trd_side"] != request["trd_side"]
                or qty != number(expected["qty"])
                or price != number(expected["price"])
            ):
                raise RecoveryEvidenceError(
                    "Correlated placement disagrees with request"
                )
            resolution = "BROKER_CONFIRMED"
        elif status in TERMINAL_BROKER:
            resolution = "TERMINAL_ACCOUNTED"
        elif (
            row["kind"] == "MODIFY"
            and qty == number(expected["qty"])
            and price == number(expected["price"])
        ):
            resolution = "CURRENT_STATE_ACCOUNTED"
        else:
            return None  # Locating a live mutation target does not account for it.
        positions = self.paper.service.get_positions(
            code=final["code"],
            trd_env="SIMULATE",
            acc_id=int(row["account"]),
            refresh_cache=True,
        )
        if not isinstance(positions, list):
            raise RecoveryEvidenceError("Malformed position query")
        position = Decimal(0)
        for item in positions:
            if not isinstance(item, dict) or item.get("code") != final["code"]:
                raise RecoveryEvidenceError("Position query scope mismatch")
            if item.get("trd_env", "SIMULATE") != "SIMULATE" or str(
                item.get("acc_id", row["account"])
            ) != str(row["account"]):
                raise RecoveryEvidenceError(
                    "Position query account/environment mismatch"
                )
            position += number(item.get("qty"))
        facts = {
            "code": final["code"],
            "acc_id": row["account"],
            "trd_env": "SIMULATE",
            "broker_status": status,
            "filled_quantity": str(filled),
            "average_fill_price": str(average),
            "remaining_executable_quantity": "0"
            if status in TERMINAL_BROKER
            else str(qty - filled),
            "current_symbol_position": str(position),
            "mutation_success_proven": row["kind"] == "PLACE",
        }
        return final, facts, resolution

    def check(self, operation_id: str) -> dict:
        with self.paper.lock:
            row = self.paper.store.lookup(operation_id)
            if row is None:
                raise ValueError("Operation is not owned by this journal")
            if self.paper._late_failure:
                raise ExecutionStoreError(
                    "OUTCOME_NOT_STORED: restart with healthy storage"
                )
            due = self.paper.store.recovery_rows(self.clock())
            if not any(item["operation_id"] == operation_id for item in due):
                return self.paper.result(row)
            started = self.clock()
            resolution = receipt = facts = None
            details: dict = {"orders": []}
            late = row["recovery_disposition"] == ASSUMED_ABSENT
            delay = 5
            try:
                matches = self._matches(row, self._observations(row))
                details["correlated_order_seen"] = bool(matches)
                if matches:
                    if late:
                        self.paper.store.block_late_order(operation_id)
                    # Invalid provider values must not prevent persisting an error.
                    json.dumps(matches, allow_nan=False)
                    details["orders"] = matches
                    accounted = self._account(row, matches)
                    if accounted:
                        receipt, facts, resolution = accounted
                        if late:
                            resolution = "LATE_BROKER_ORDER_FOUND"
                        outcome, delay = "FOUND", MONITOR_INTERVAL
                    else:
                        outcome = "TARGET_PENDING"
                elif (
                    row["kind"] == "PLACE"
                    and row["order_tag"]
                    and not row["broker_order_id"]
                    and row["state"] in {"ADMITTED", "UNKNOWN_OUTCOME"}
                    and not any(
                        json.loads(c["details"]).get("correlated_order_seen")
                        for c in row["recovery_checks"]
                    )
                ):
                    outcome = "MONITOR_NOT_FOUND" if late else "NOT_FOUND"
                    negatives = (
                        sum(c["outcome"] == "NOT_FOUND" for c in row["recovery_checks"])
                        + 1
                    )
                    if late:
                        delay = MONITOR_INTERVAL
                    elif negatives >= 5:
                        resolution, delay = ASSUMED_ABSENT, MONITOR_INTERVAL
                    else:
                        delay = NEGATIVE_GAPS[negatives - 1]
                else:
                    outcome = "IDENTITY_NOT_FOUND"
                details["reason"] = outcome
            except ExecutionStoreError:
                raise
            except Exception as exc:
                outcome = "ERROR"
                details["reason"] = str(exc)
            completed = self.clock()
            if self._stop.is_set():
                return self.paper.result(row)
            self.paper.store.record_recovery(
                operation_id,
                observed_state=row["state"],
                started_at=started,
                completed_at=completed,
                outcome=outcome,
                details=details,
                next_check_at=completed + delay,
                resolution=resolution,
                receipt=receipt,
                accounted_facts=facts,
            )
            result = self.paper.store.lookup(operation_id)
            assert result is not None
            return self.paper.result(result)
