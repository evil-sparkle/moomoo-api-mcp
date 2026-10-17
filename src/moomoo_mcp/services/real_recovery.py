"""Read-only REAL accounting; an unsuccessful search never establishes absence."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from moomoo_mcp.services.execution_store import ExecutionStoreError
from moomoo_mcp.services.paper_recovery import (
    ACTIVE_BROKER,
    TERMINAL_BROKER,
    RecoveryEvidenceError,
    number,
)
from moomoo_mcp.services.recovery import RecoveryWorker
from moomoo_mcp.tools.serialization import serialize_identifiers

if TYPE_CHECKING:
    from moomoo_mcp.services.real_execution import RealExecution

logger = logging.getLogger(__name__)
CHECK_INTERVAL = 30


class RealRecovery(RecoveryWorker):
    def __init__(
        self, execution: RealExecution, clock: Callable[[], float] = time.time
    ):
        self.execution = execution
        self.clock = clock
        super().__init__("real-recovery", logger)

    def run_once(self) -> None:
        if self.execution._late_failure:
            return
        for row in self.execution.store.recovery_rows(self.clock()):
            if self._stop.is_set():
                return
            self.check(row["operation_id"])

    def _observations(self, row: dict) -> list[dict]:
        service = self.execution.service
        account = int(row["account"])
        start = date.fromisoformat(row["created_at"].split(" ")[0]) - timedelta(days=1)
        end = datetime.now(timezone.utc).date() + timedelta(days=1)
        current = service.get_orders(trd_env="REAL", acc_id=account, refresh_cache=True)
        history = service.get_history_orders(
            trd_env="REAL", acc_id=account, start=start.isoformat(), end=end.isoformat()
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
            if order.get("trd_env", "REAL") != "REAL" or str(
                order.get("acc_id", account)
            ) != str(account):
                raise RecoveryEvidenceError("Broker query account/environment mismatch")
        return observations

    @staticmethod
    def _matches(row: dict, observations: list[dict]) -> list[dict]:
        request = json.loads(row["request"])
        target = (
            row["broker_order_id"]
            if row["kind"] in {"PLACE", "PLACE_COMBO"}
            else request["order_id"]
        )
        return [
            order
            for order in observations
            if (target and order["order_id"] == str(target))
            or (row["order_tag"] and order.get("remark") == row["order_tag"])
        ]

    @staticmethod
    def _leg_identity(legs: object) -> list[dict]:
        if not isinstance(legs, list) or not legs:
            raise RecoveryEvidenceError("Missing REAL combo leg evidence")
        result = []
        for leg in legs:
            if (
                not isinstance(leg, dict)
                or not isinstance(leg.get("code"), str)
                or not leg["code"].strip()
            ):
                raise RecoveryEvidenceError("Invalid REAL combo leg evidence")
            side = leg.get("trd_side")
            ratio = number(leg.get("qty_ratio"))
            if (
                not isinstance(side, str)
                or side.strip().upper() not in {"BUY", "SELL"}
                or ratio <= 0
                or ratio != int(ratio)
            ):
                raise RecoveryEvidenceError("Invalid REAL combo leg evidence")
            position_id = leg.get("position_id")
            if position_id is not None:
                if isinstance(position_id, bool):
                    raise RecoveryEvidenceError("Invalid REAL combo position identity")
                if isinstance(position_id, int):
                    position_id = str(position_id)
                elif isinstance(position_id, str) and position_id.strip().isdecimal():
                    position_id = str(int(position_id.strip()))
                else:
                    raise RecoveryEvidenceError("Invalid REAL combo position identity")
            result.append(
                {
                    "code": leg["code"].strip(),
                    "trd_side": side.strip().upper(),
                    "qty_ratio": str(int(ratio)),
                    "position_id": position_id,
                }
            )
        return result

    def _positions(self, row: dict, codes: list[str]) -> dict[str, str]:
        result = {}
        for code in dict.fromkeys(codes):
            positions = self.execution.service.get_positions(
                code=code,
                trd_env="REAL",
                acc_id=int(row["account"]),
                refresh_cache=True,
            )
            if not isinstance(positions, list):
                raise RecoveryEvidenceError("Malformed position query")
            quantity = Decimal(0)
            for item in positions:
                if not isinstance(item, dict) or item.get("code") != code:
                    raise RecoveryEvidenceError("Position query scope mismatch")
                if (
                    item.get("trd_env", "REAL") != "REAL"
                    or str(item.get("acc_id", row["account"])) != row["account"]
                ):
                    raise RecoveryEvidenceError(
                        "Position query account/environment mismatch"
                    )
                quantity += number(item.get("qty"))
            result[code] = str(quantity)
        return result

    def _account(self, row: dict, matches: list[dict]) -> tuple[dict, dict, str] | None:
        if len({order["order_id"] for order in matches}) != 1:
            raise RecoveryEvidenceError("Correlation identifies multiple broker orders")
        final = matches[0]
        combo = row["kind"] == "PLACE_COMBO"
        expected = json.loads(row["merged_request"] or row["request"])
        request = json.loads(row["request"])
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
            keys = (
                ("order_status", "order_type")
                if combo
                else ("order_status", "order_type", "code", "trd_side")
            )
            for name in keys:
                if final.get(name) is None or order.get(name) != final[name]:
                    raise RecoveryEvidenceError(
                        "Broker observations disagree or lack required fields"
                    )
            for name in ("qty", "price", "dealt_qty", "dealt_avg_price"):
                if number(order.get(name)) != number(final.get(name)):
                    raise RecoveryEvidenceError("Broker observations disagree")
            if combo and self._leg_identity(
                order.get("combo_legs", order.get("combo_leg_list"))
            ) != self._leg_identity(
                final.get("combo_legs", final.get("combo_leg_list"))
            ):
                raise RecoveryEvidenceError("Broker combo observations disagree")
        if row["broker_order_id"] and final["order_id"] != row["broker_order_id"]:
            raise RecoveryEvidenceError("Tag disagrees with recorded broker identity")
        status = final["order_status"]
        qty, price = number(final.get("qty")), number(final.get("price"))
        filled, average = (
            number(final.get("dealt_qty")),
            number(final.get("dealt_avg_price")),
        )
        if (
            status not in TERMINAL_BROKER | ACTIVE_BROKER
            or qty <= 0
            or filled < 0
            or filled > qty
            or (not combo and average < 0)
        ):
            raise RecoveryEvidenceError("Invalid broker status or fill evidence")
        if not combo and (
            not isinstance(final.get("code"), str)
            or not final["code"]
            or not isinstance(final.get("trd_side"), str)
            or not final["trd_side"]
        ):
            raise RecoveryEvidenceError("Missing REAL instrument/side evidence")
        if row["kind"] in {"PLACE", "PLACE_COMBO"}:
            if (
                qty != number(expected["qty"])
                or price != number(expected["price"])
                or final["order_type"] != expected["order_type"]
            ):
                raise RecoveryEvidenceError(
                    "Correlated placement disagrees with request"
                )
            if not combo and (
                final["code"] != request["code"]
                or final["trd_side"] != request["trd_side"]
            ):
                raise RecoveryEvidenceError(
                    "Correlated placement disagrees with request"
                )
            if combo and self._leg_identity(
                final.get("combo_legs", final.get("combo_leg_list"))
            ) != self._leg_identity(expected["combo_legs"]):
                raise RecoveryEvidenceError(
                    "Correlated combo legs disagree with request"
                )
            for name in (
                "time_in_force",
                "session",
                "aux_price",
                "trail_type",
                "trail_value",
                "trail_spread",
            ):
                value = expected.get(name)
                if value is None:
                    continue
                for order in matches:
                    observed = order.get(name)
                    if observed is None:
                        raise RecoveryEvidenceError(f"Missing broker {name} evidence")
                    same = (
                        number(observed) == number(value)
                        if name in {"aux_price", "trail_value", "trail_spread"}
                        else observed == value
                    )
                    if not same:
                        raise RecoveryEvidenceError(
                            f"Broker {name} disagrees with request"
                        )
            resolution = "BROKER_CONFIRMED"
        elif status in TERMINAL_BROKER:
            resolution = "TERMINAL_ACCOUNTED"
        else:
            mutation = str(request.get("modify_order_op", "CANCEL")).strip().upper()
            if row["kind"] == "CANCEL":
                return None
            if mutation == "DISABLE":
                if status != "DISABLED":
                    return None
            elif mutation in {"NORMAL", "ENABLE"}:
                if mutation == "ENABLE" and status == "DISABLED":
                    return None
                if any(
                    expected.get(name) is not None
                    and number(expected[name]) != number(final.get(name))
                    for name in ("qty", "price")
                ):
                    return None
            else:
                return None
            resolution = "CURRENT_STATE_ACCOUNTED"
        codes = (
            [leg["code"] for leg in self._leg_identity(expected["combo_legs"])]
            if combo
            else [final["code"]]
        )
        positions = self._positions(row, codes)
        facts = {
            "acc_id": row["account"],
            "trd_env": "REAL",
            "broker_status": status,
            "filled_quantity": str(filled),
            "average_fill_price": str(average),
            "remaining_executable_quantity": "0"
            if status in TERMINAL_BROKER
            else str(qty - filled),
            "current_positions": positions,
            "mutation_success_proven": row["kind"] in {"PLACE", "PLACE_COMBO"},
        }
        if not combo:
            facts.update(
                code=final["code"], current_symbol_position=positions[final["code"]]
            )
        return final, facts, resolution

    def check(self, operation_id: str) -> dict:
        execution = self.execution
        with execution.lock:
            row = execution.store.lookup(operation_id)
            if row is None:
                raise ValueError("Operation is not owned by this journal")
            if execution._late_failure:
                raise ExecutionStoreError(
                    "OUTCOME_NOT_STORED: restart with healthy storage"
                )
            if not any(
                item["operation_id"] == operation_id
                for item in execution.store.recovery_rows(self.clock())
            ):
                return execution.result(row)
            started = self.clock()
            details: dict = {"orders": []}
            resolution = receipt = facts = None
            if row["state"] == "ADMITTED" and not row["marker"]:
                outcome, resolution = "NOT_DISPATCHED", "NOT_DISPATCHED"
                details["reason"] = "Durable admission has no SDK dispatch marker"
            else:
                try:
                    matches = self._matches(row, self._observations(row))
                    details["correlated_order_seen"] = bool(matches)
                    json.dumps(matches, allow_nan=False)
                    details["orders"] = matches
                    if matches:
                        accounted = self._account(row, matches)
                        if accounted:
                            receipt, facts, resolution = accounted
                            outcome = "FOUND"
                        else:
                            outcome = "TARGET_PENDING"
                    else:
                        outcome = "NOT_FOUND"
                    details["reason"] = outcome
                except ExecutionStoreError:
                    raise
                except Exception as exc:
                    outcome = "ERROR"
                    details["reason"] = str(exc)
            completed = self.clock()
            if self._stop.is_set():
                return execution.result(row)
            execution.store.record_recovery(
                operation_id,
                observed_state=row["state"],
                started_at=started,
                completed_at=completed,
                outcome=outcome,
                details=details,
                next_check_at=completed + CHECK_INTERVAL,
                resolution=resolution,
                receipt=receipt,
                accounted_facts=facts,
            )
            result = execution.store.lookup(operation_id)
            assert result is not None
            return execution.result(result)
