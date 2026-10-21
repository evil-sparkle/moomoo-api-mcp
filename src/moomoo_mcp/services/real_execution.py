"""Durable REAL identity around the existing validated mutation boundary."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from moomoo_mcp.client_authorization import visible_journal_error
from moomoo_mcp.services.admission import AdmissionPlan
from moomoo_mcp.services.broker_dispatch import QuotaRequest
from moomoo_mcp.services.execution_identity import differing_fields, validate_token
from moomoo_mcp.services.execution_store import (
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)
from moomoo_mcp.services.instrument_rules import normalize_order_session
from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderReceiptUnreadableError,
    not_sent,
    not_sent_message,
)
from moomoo_mcp.services.real_recovery import RealRecovery
from moomoo_mcp.services.recovery import pending_outcome_health
from moomoo_mcp.services.validation import (
    validate_real_request,
)
from moomoo_mcp.tools.serialization import serialize_identifiers

if TYPE_CHECKING:
    from moomoo_mcp.services.trade_service import TradeService

ROUTING = frozenset(
    {"trd_env", "acc_id", "operation_id", "admission_epoch", "approval_id"}
)


def _identity_value(value: Any) -> Any:
    """Normalize numeric equivalence without accepting strings as REAL numbers."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("REAL request numbers must be finite")
        text = format(Decimal(str(value)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return {"number": "0" if text == "-0" else text}
    if isinstance(value, dict):
        return {key: _identity_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_identity_value(item) for item in value]
    return value


def canonical_real_request(account: int, kind: str, params: dict) -> str:
    params = dict(params)
    if kind == "PLACE" and "session" in params:
        selected = normalize_order_session(params["session"], trd_env="REAL")
        if selected == "N/A":
            # Old callers never supplied this field. The SDK default is the
            # same request, including retries after upgrading the server.
            params.pop("session")
        else:
            params["session"] = selected
    return json.dumps(
        {
            "env": "REAL",
            "account": str(account),
            "op_type": kind,
            "params": _identity_value(params),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class RealExecution:
    def __init__(self, service: TradeService, store: ExecutionStore):
        if store.environment != "REAL":
            raise ValueError("REAL execution requires a REAL journal")
        self.service = service
        self.dispatcher = service.dispatcher
        self.store = store
        self.lock = threading.RLock()
        self._admission_lock = threading.Lock()
        self._active = threading.local()
        self._late_failure: dict[str, dict] = {}
        self._late_identity: dict[str, dict] = {}
        self.store.review()
        self.recovery = RealRecovery(self)
        # The durable marker is the only path into SDK mutation. A recovered
        # unmarked admission therefore has positive local proof of no dispatch.
        for row in self.store.recovery_rows(self.recovery.clock()):
            if row["state"] == "ADMITTED" and not row["marker"]:
                self.recovery.check(row["operation_id"])

    @staticmethod
    def _kind(method: str, params: dict) -> str:
        if method == "place_order":
            return "PLACE"
        if method == "place_combo_order":
            return "PLACE_COMBO"
        if method == "cancel_order" or (
            method == "modify_order"
            and str(params.get("modify_order_op", "")).strip().upper() == "CANCEL"
        ):
            return "CANCEL"
        if method == "modify_order":
            return "MODIFY"
        raise ValueError("Unsupported REAL mutation method")

    @staticmethod
    def _original(params: dict) -> dict:
        return {key: value for key, value in params.items() if key not in ROUTING}

    def _retry(self, row: dict, kind: str, params: dict) -> dict:
        from moomoo_mcp.client_authorization import check_account_access

        check_account_access("REAL", row["account"], write=True)
        account = self.service._exact_account_id(
            params.get("acc_id", "0"), allow_zero=True
        )
        proposed = canonical_real_request(
            account or int(row["account"]), kind, self._original(params)
        )
        fields = differing_fields(row["canonical"], proposed)
        if fields:
            raise ExecutionConflict("Operation ID conflict: " + ", ".join(fields))
        if row["operation_id"] in self._late_failure:
            return self._late_failure[row["operation_id"]]
        return self.result(row)

    def retry(self, method: str, params: dict) -> dict | None:
        with not_sent(method):
            self.service.policy.check_write(method, "REAL")
            validate_token(params.get("operation_id"), params.get("admission_epoch"))
            kind = self._kind(method, params)
            # Validate the complete request before a failed lookup can occur.
            canonical_real_request(1, kind, self._original(params))
        identifier = params["operation_id"]
        row = (
            self._late_identity[identifier]
            if identifier in self._late_failure
            else self.store.lookup(identifier)
        )
        return self._retry(row, kind, params) if row is not None else None

    def recover_admission(self, method: str, params: dict) -> dict | None:
        return self.retry(method, params)

    def preflight(self, method: str, params: dict) -> tuple[dict | None, int | None]:
        existing = self.retry(method, params)
        if existing is not None:
            return existing, None
        with not_sent(method):
            validate_real_request(method, params)
            if params["admission_epoch"] != self.store.epoch:
                raise ExecutionConflict(
                    "Unknown non-current-epoch token cannot be accounted for; "
                    "do not refresh it"
                )
            self.ready()
            if self.service.trade_ctx is None:
                raise RuntimeError("Trade context not connected")
            kind = self._kind(method, params)
            exposing = kind in {"PLACE", "PLACE_COMBO"} or (
                kind == "MODIFY"
                and str(params.get("modify_order_op", "")).strip().upper()
                in {"NORMAL", "ENABLE"}
            )
            if exposing:
                self.service._check_execution_halt(method, "REAL")
            market = None
            if kind == "PLACE":
                market = self.service._get_market_from_code(params["code"])
            elif kind == "PLACE_COMBO":
                legs = self.service._build_combo_legs(params["combo_legs"])
                market = self.service._get_market_from_code(legs[0].code)
            requested_account = self.service._exact_account_id(
                params.get("acc_id", "0"), allow_zero=True
            )
            account = self.service._resolve_account("REAL", market, requested_account)
        return None, account

    def health(self) -> dict:
        return pending_outcome_health(self.store.health(), self._late_failure.keys())

    def ready(self) -> None:
        if self._late_failure:
            raise ExecutionStoreError(
                "OUTCOME_NOT_STORED: REAL execution blocked; "
                "restart with healthy storage"
            )
        self.store.require_ready()

    def result(self, row: dict) -> dict:
        if row["operation_id"] in self._late_failure:
            return self._late_failure[row["operation_id"]]
        receipt = json.loads(row["receipt"]) if row.get("receipt") else {}
        return receipt | {
            "operation_id": row["operation_id"],
            "admission_epoch": row["admission_epoch"],
            "order_tag": row["order_tag"],
            "trd_env": "REAL",
            "acc_id": row["account"],
            "state": row["state"],
            "status": "IN_FLIGHT"
            if row["state"] in {"ADMITTED", "DISPATCHING"}
            else row["state"],
            "disposition": row["disposition"],
            "receipt": receipt,
            "broker_order_id": row.get("broker_order_id"),
            "broker_status": row.get("broker_status"),
            "error": visible_journal_error(row.get("error"), "REAL"),
            "submission_state": "durably_stored",
            "broker_acknowledged": row["disposition"] == "ACKNOWLEDGED"
            or bool(
                row.get("accounted_facts")
                and json.loads(row["accounted_facts"]).get("mutation_success_proven")
            ),
            "recovery": self.store.recovery_context(row),
            "recovery_checks": [
                check | {"details": json.loads(check["details"])}
                for check in row["recovery_checks"]
            ],
            "observations": json.loads(row["evidence"]) if row.get("evidence") else [],
            "modification_observed": bool(row["modification_observed"])
            if row["kind"] == "MODIFY"
            else None,
            "accounted_facts": json.loads(row["accounted_facts"])
            if row.get("accounted_facts")
            else None,
        }

    def execute(self, method: str, params: dict, call: Callable[[dict], dict]) -> dict:
        # Nested combo legs and other caller-owned objects must not change after
        # their immutable identity has been admitted.
        params = deepcopy(params)
        existing = self.retry(method, params)
        if existing is not None:
            return existing
        with self._admission_lock:
            existing, account = self.preflight(method, params)
            if existing is not None:
                return existing
            assert account is not None
            original = self._original(params)
            kind = self._kind(method, params)
            with not_sent(method):
                row, admitted = self.store.admit(
                    params["operation_id"],
                    params["admission_epoch"],
                    account,
                    kind,
                    canonical_real_request(account, kind, original),
                    original,
                )
            if not admitted:
                return self._retry(row, kind, params)
        with self.lock:
            identifier = row["operation_id"]
            self._late_identity[identifier] = row
            self._active.operation = {
                "row": row,
                "marked": False,
                "evidence": {},
                "observed": [],
            }
            fixed = params | {"trd_env": "REAL", "acc_id": account}
            if kind in {"PLACE", "PLACE_COMBO"}:
                fixed["remark"] = row["order_tag"]
            try:
                with not_sent(method, admitted=True):
                    self.ready()
                receipt = call(fixed)
                if not self._active.operation["marked"]:
                    raise OrderNotSentError(
                        not_sent_message(
                            method, "Durable dispatch boundary was not entered"
                        ),
                        admitted=True,
                    )
                try:
                    receipt = serialize_identifiers(receipt)
                    json.dumps(receipt, allow_nan=False)
                except Exception as exc:
                    return self._finish(
                        row,
                        "ACKNOWLEDGED",
                        "ACKNOWLEDGED",
                        receipt=self._active.operation["evidence"],
                        error=(
                            f"Acknowledged receipt conversion failed: {exc}; "
                            "do not resend"
                        ),
                        reason="RECEIPT_UNREADABLE",
                    )
                return self._finish(
                    row, "ACKNOWLEDGED", "ACKNOWLEDGED", receipt=receipt
                )
            except Exception as exc:
                active = self._active.operation
                if not active["marked"]:
                    self._finish(row, "REFUSED", "NOT_SENT", error=str(exc))
                    if isinstance(exc, OrderNotSentError):
                        exc.admitted = True
                        raise
                    raise OrderNotSentError(
                        not_sent_message(method, str(exc)), admitted=True
                    ) from exc
                acknowledged = isinstance(exc, OrderReceiptUnreadableError)
                return self._finish(
                    row,
                    "ACKNOWLEDGED" if acknowledged else "UNKNOWN_OUTCOME",
                    "ACKNOWLEDGED" if acknowledged else "POSSIBLY_SENT",
                    receipt=active["evidence"] or None,
                    error=str(exc),
                    reason="RECEIPT_UNREADABLE"
                    if acknowledged
                    else "UNRESOLVED_OUTCOME",
                )
            finally:
                del self._active.operation

    def before_dispatch(self, merged: dict) -> None:
        active = getattr(self._active, "operation", None)
        if active is None:
            raise OrderNotSentError("REAL mutation has no active durable admission")
        with not_sent("REAL journal dispatch", admitted=True):
            if active["marked"]:
                raise ExecutionConflict("REAL operation cannot dispatch more than once")
            self.ready()
            if (
                merged.get("trd_env") != "REAL"
                or str(merged.get("acc_id")) != active["row"]["account"]
            ):
                raise ExecutionConflict(
                    "Dispatch account/environment disagrees with admission"
                )
            self.service._confirm_dispatch_controls()
            self.store.mark_dispatch(active["row"]["operation_id"], merged)
        active["marked"] = True

    def require_active(self) -> None:
        if getattr(self._active, "operation", None) is None:
            raise OrderNotSentError("REAL mutation has no active durable admission")

    def observe_acknowledgement(self, data: Any) -> None:
        active = getattr(self._active, "operation", None)
        if active is None:
            return
        try:
            value = (
                data["order_id"].iloc[0] if hasattr(data, "iloc") else data["order_id"]
            )
            identity = str(value)
            if identity.isascii() and identity.isdecimal() and int(identity) > 0:
                active["evidence"]["order_id"] = identity
        except Exception:
            pass  # Acknowledgement remains true even when identity is unreadable.

    def validate_target(self, order: dict) -> None:
        active = getattr(self._active, "operation", None)
        if active is None:
            return
        account = active["row"]["account"]
        if (
            order.get("trd_env", "REAL") != "REAL"
            or str(order.get("acc_id", account)) != account
        ):
            raise ValueError(
                "Broker target account/environment disagrees with admission"
            )
        pending = self.store.unobserved_modifications(
            int(active["row"]["account"]), str(order["order_id"])
        )
        for previous in pending:
            expected = json.loads(previous["merged_request"])
            for name in ("qty", "price"):
                if expected.get(name) is None:
                    continue
                try:
                    current = Decimal(str(order.get(name)))
                    matches = current.is_finite() and current == Decimal(
                        str(expected[name])
                    )
                except (ValueError, ArithmeticError):
                    matches = False
                if not matches:
                    raise ValueError(
                        "Prior acknowledged REAL modification "
                        f"{previous['operation_id']} "
                        f"is not yet observed in broker {name}; no order was sent"
                    )
            active["observed"].append(previous["operation_id"])

    def _finish(
        self,
        row: dict,
        state: str,
        disposition: str,
        *,
        receipt: dict | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> dict:
        identifier = row["operation_id"]
        committed = False
        try:
            self.store.outcome(
                identifier,
                state,
                disposition,
                receipt=receipt,
                error=error,
                reason=reason,
                observed_modifications=self._active.operation["observed"],
            )
            committed = True
            current = self.store.lookup(identifier)
            assert current is not None
            result = self.result(current)
            self._late_identity.pop(identifier, None)
            if reason:
                self.recovery.wake()
            return result
        except Exception as exc:
            result = (receipt or {}) | {
                "operation_id": identifier,
                "admission_epoch": row["admission_epoch"],
                "order_tag": row["order_tag"],
                "trd_env": "REAL",
                "acc_id": row["account"],
                "state": state,
                "disposition": disposition if committed else "OBSERVED_NOT_STORED",
                "receipt": receipt or {},
                "broker_acknowledged": state == "ACKNOWLEDGED",
                "submission_state": "durably_stored" if committed else "observed",
                "error": f"Local persistence failure: {exc}; do not resend",
                "recovery": {
                    "disposition": "PENDING_STORAGE_RECOVERY",
                    "original_operation_replay_allowed": False,
                },
            }
            self._late_failure[identifier] = result
            return result

    def reconcile(self, operation_id: str) -> dict:
        return self.recovery.check(operation_id)

    def prepare_admission(self, method: str, params: dict[str, Any]) -> AdmissionPlan:
        if method != "reconcile":
            return AdmissionPlan()
        row = self.store.lookup(params["operation_id"])
        if row is None:
            raise ValueError("Operation is not owned by this journal")
        if self._late_failure or (row["state"] == "ADMITTED" and not row["marker"]):
            return AdmissionPlan()
        if not any(
            item["operation_id"] == row["operation_id"]
            for item in self.store.recovery_rows(self.recovery.clock())
        ):
            return AdmissionPlan()
        account = int(row["account"])
        return AdmissionPlan(
            [
                QuotaRequest("order_list_query", account),
                QuotaRequest("history_order_list_query", account),
                QuotaRequest("position_list_query", account),
            ]
        )
