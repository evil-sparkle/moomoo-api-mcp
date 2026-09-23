"""Serialized paper mutation lifecycle, separate from REAL unlock policy."""

from __future__ import annotations

import json
import math
import threading
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Any

from moomoo import RET_OK

from moomoo_mcp.services.execution_identity import (
    canonicalize_request,
    decimal_price,
    differing_fields,
    validate_token,
)
from moomoo_mcp.services.execution_store import (
    ExecutionConflict,
    ExecutionStore,
    ExecutionStoreError,
)
from moomoo_mcp.services.order_errors import not_sent
from moomoo_mcp.services.sdk_response import as_frame
from moomoo_mcp.services.trading_policy import LegFacts, OrderFacts
from moomoo_mcp.tools.serialization import serialize_identifiers

if TYPE_CHECKING:
    from moomoo_mcp.services.trade_service import TradeService

TERMINAL_BROKER = frozenset(
    {"FILLED_ALL", "CANCELLED_ALL", "CANCELLED_PART", "FAILED", "DELETED", "REJECTED"}
)


class PaperExecution:
    def __init__(
        self, service: TradeService, store: ExecutionStore, allowlist: frozenset[int]
    ):
        self.service = service
        self.store = store
        self.allowlist = allowlist
        self.lock = threading.RLock()
        self._late_failure: dict[str, dict] = {}
        self._late_identity: dict[str, dict] = {}
        self.store.review()

    def health(self) -> dict:
        health = self.store.health()
        if self._late_failure:
            health["state"] = "JOURNAL_BLOCKED"
            health["blocking_reasons"] = sorted(
                set(health["blocking_reasons"]) | {"OUTCOME_NOT_STORED"}
            )
            health["blocking"] = max(health["blocking"], len(self._late_failure))
        return health

    def ready(self) -> None:
        if self._late_failure:
            raise ExecutionStoreError(
                "OUTCOME_NOT_STORED: paper execution blocked; restart and"
                " review recovery"
            )
        self.store.require_ready()

    @staticmethod
    def _whole(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                "Paper execution supports positive whole-share quantities only"
            )
        return value

    def _schema(self, kind: str, params: dict) -> None:
        if "price" in params:
            decimal_price(params["price"])
        if "qty" in params:
            self._whole(params["qty"])
        if kind == "PLACE":
            if params["order_type"] != "NORMAL" or params["time_in_force"] != "DAY":
                raise ValueError(
                    "Version 1 paper execution supports NORMAL limit DAY orders only"
                )
            if not params["code"].startswith("US.") or params["trd_side"] not in {
                "BUY",
                "SELL",
            }:
                raise ValueError(
                    "Version 1 paper execution supports US stocks/ETFs and "
                    "BUY/SELL only"
                )
            if params.get("adjust_limit", 0) != 0 or any(
                params.get(k) is not None
                for k in ("aux_price", "trail_type", "trail_value", "trail_spread")
            ):
                raise ValueError(
                    "Price adjustment and trigger/trailing parameters are "
                    "outside paper scope"
                )
        else:
            if (
                not isinstance(params.get("order_id"), str)
                or not params["order_id"].isascii()
                or not params["order_id"].isdecimal()
                or int(params["order_id"]) <= 0
            ):
                raise ValueError(
                    "Paper mutation must name one positive decimal order ID"
                )
            if kind == "MODIFY" and (
                not {"qty", "price"} & params.keys()
                or params.get("adjust_limit", 0) != 0
            ):
                raise ValueError(
                    "Paper modifications support only price and total quantity"
                )

    def _account(self, acc_id: int | str) -> int:
        requested = self.service._exact_account_id(acc_id, allow_zero=True)
        if requested and requested not in self.allowlist:
            raise ValueError("Account is not on the simulated-account allowlist")
        accounts = self.service.get_accounts()
        eligible = {
            int(a["acc_id"])
            for a in accounts
            if a.get("trd_env") == "SIMULATE"
            and int(a["acc_id"]) in self.allowlist
            and ("US" in (a.get("trdmarket_auth") or []))
        }
        if requested:
            if requested not in eligible:
                raise ValueError(
                    "Allowlisted account could not be verified as a US "
                    "simulated account"
                )
            return requested
        if len(eligible) != 1:
            raise ValueError(
                "Paper execution requires exactly one eligible account or"
                " an explicit acc_id"
            )
        return next(iter(eligible))

    def _retry(self, row: dict, kind: str, params: dict, acc_id: int | str) -> dict:
        requested = self.service._exact_account_id(acc_id, allow_zero=True)
        bound = int(row["account"])
        proposed = canonicalize_request("SIMULATE", requested or bound, kind, params)
        fields = differing_fields(row["canonical"], proposed)
        if fields:
            raise ExecutionConflict("Operation ID conflict: " + ", ".join(fields))
        if row["operation_id"] in self._late_failure:
            return self._late_failure[row["operation_id"]]
        return self.result(row)

    @staticmethod
    def result(row: dict) -> dict:
        receipt = json.loads(row["receipt"]) if row.get("receipt") else {}
        return {
            "operation_id": row["operation_id"],
            "admission_epoch": row["admission_epoch"],
            "state": row["state"],
            "disposition": row["disposition"],
            "status": "IN_FLIGHT"
            if row["state"] in {"ADMITTED", "DISPATCHING"}
            else row["state"],
            "acc_id": row["account"],
            "trd_env": "SIMULATE",
            "receipt": receipt,
            "broker_order_id": row.get("broker_order_id"),
            "broker_status": row.get("broker_status"),
            "error": row.get("error"),
            "submission_state": "durably_stored",
            "observations": json.loads(row["evidence"]) if row.get("evidence") else [],
            "accounted_facts": json.loads(row["accounted_facts"])
            if row.get("accounted_facts")
            else None,
        }

    def execute(
        self,
        kind: str,
        params: dict,
        *,
        operation_id: str | None,
        admission_epoch: str | None,
        acc_id: int | str,
    ) -> dict:
        with not_sent("paper " + kind):
            self.service.policy.check_write(kind, "SIMULATE")
            validate_token(operation_id, admission_epoch)
            assert operation_id is not None and admission_epoch is not None
            self._schema(kind, params)
        # A failed lookup cannot prove that a previously admitted token was not sent.
        if operation_id in self._late_failure:
            return self._retry(self._late_identity[operation_id], kind, params, acc_id)
        row = self.store.lookup(operation_id)
        if row:
            return self._retry(row, kind, params, acc_id)
        with not_sent("paper " + kind):
            # Fail storage/recovery and stale tokens before any broker discovery.
            if admission_epoch != self.store.epoch:
                raise ExecutionConflict(
                    "Unknown non-current-epoch token cannot be accounted for;"
                    " do not refresh it"
                )
            self.ready()
            account = self._account(acc_id)
            canonical = canonicalize_request("SIMULATE", account, kind, params)
            row, admitted = self.store.admit(
                operation_id, admission_epoch, account, kind, canonical, params
            )
            if not admitted:
                return self._retry(row, kind, params, acc_id)
        with self.lock:
            self._late_identity[operation_id] = row
            try:
                self.ready()
                merged = self._prepare(kind, params, account)
                self.store.mark_dispatch(operation_id, merged)
            except Exception as exc:
                with not_sent("paper " + kind):
                    self.store.outcome(
                        operation_id,
                        "REFUSED",
                        "NOT_SENT",
                        error=f"No order was sent: {exc}",
                    )
                    self._late_identity.pop(operation_id, None)
                    raise exc
            # Marker committed, no SQL transaction remains. From here never NOT_SENT.
            evidence: dict = {}
            try:
                ctx = self.service.trade_ctx
                assert ctx is not None
                if kind == "PLACE":
                    ret, data = ctx.place_order(**merged)
                else:
                    ret, data = ctx.modify_order(**merged)
            except Exception as exc:
                return self._finish(
                    operation_id,
                    "UNKNOWN_OUTCOME",
                    "POSSIBLY_SENT",
                    error=(
                        f"Outcome is unknown; may have been sent. Do not resend: {exc}"
                    ),
                    reason="UNRESOLVED_OUTCOME",
                )
            if ret != RET_OK:
                return self._finish(
                    operation_id,
                    "UNKNOWN_OUTCOME",
                    "POSSIBLY_SENT",
                    error=(
                        f"Outcome is unknown; may have been sent. Do not resend: {data}"
                    ),
                    reason="UNRESOLVED_OUTCOME",
                )
            # Preserve any independently readable ID even if full conversion fails.
            try:
                payload: Any = data
                value = payload["order_id"].iloc[0]
                evidence["order_id"] = str(value)
            except Exception:
                pass
            try:
                receipt = serialize_identifiers(self.service._first_record(kind, data))
                json.dumps(receipt, allow_nan=False)
            except Exception as exc:
                return self._finish(
                    operation_id,
                    "ACKNOWLEDGED",
                    "ACKNOWLEDGED",
                    receipt=evidence,
                    error=(
                        "Gateway acknowledged; "
                        f"local conversion failure: {exc}; do not resend"
                    ),
                    reason="RECEIPT_UNREADABLE",
                )
            return self._finish(
                operation_id, "ACKNOWLEDGED", "ACKNOWLEDGED", receipt=receipt
            )

    def _finish(
        self,
        operation_id: str,
        state: str,
        disposition: str,
        *,
        receipt: dict | None = None,
        error: str | None = None,
        reason: str | None = None,
    ) -> dict:
        committed = False
        try:
            self.store.outcome(
                operation_id,
                state,
                disposition,
                receipt=receipt,
                error=error,
                reason=reason,
            )
            committed = True
            row = self.store.lookup(operation_id)
            assert row is not None
            self._late_identity.pop(operation_id, None)
            return self.result(row)
        except Exception as exc:
            result = {
                "operation_id": operation_id,
                "state": state,
                "disposition": disposition if committed else "OBSERVED_NOT_STORED",
                "submission_state": "durably_stored" if committed else "observed",
                "receipt": receipt or {},
                "error": f"Local persistence failure: {exc}; do not resend",
                "broker_acknowledged": state == "ACKNOWLEDGED",
            }
            self._late_failure[operation_id] = result
            return result

    def _prepare(self, kind: str, params: dict, account: int) -> dict:
        ctx = self.service.trade_ctx
        if ctx is None:
            raise ValueError("Trade context not connected")
        if kind == "PLACE":
            order = params
        else:
            order = self.service._fetch_order(
                "paper " + kind, params["order_id"], "SIMULATE", account
            )
            # Broker facts are evidence; absent required fields fail closed.
            if (
                order.get("time_in_force") != "DAY"
                or order.get("order_type") != "NORMAL"
                or order.get("fill_outside_rth") not in (False, 0)
                or order.get("session") not in ("RTH", "NONE")
            ):
                raise ValueError(
                    "Target order is outside verified DAY/regular-hours paper scope"
                )
            if kind == "MODIFY" and order.get("order_status") in TERMINAL_BROKER:
                raise ValueError("Target order is terminal")
        code = str(order.get("code", ""))
        if not code.startswith("US.") or order.get("trd_side") not in {"BUY", "SELL"}:
            raise ValueError("Target is outside US equity paper scope")
        lookup = self.service.instrument_lookup
        if lookup is None:
            raise ValueError("Instrument classification cannot be verified")
        instruments = lookup([code])
        if (
            len(instruments) != 1
            or instruments[0].code != code
            or instruments[0].classification not in {"STOCK", "ETF"}
        ):
            raise ValueError("Paper execution requires a verified US stock or ETF")
        if kind == "CANCEL":
            return {
                "modify_order_op": "CANCEL",
                "order_id": params["order_id"],
                "qty": 0,
                "price": 0,
                "trd_env": "SIMULATE",
                "acc_id": account,
            }
        qty = params.get("qty", order.get("qty"))
        # SDK reports whole quantities as floats; validate rather than truncate.
        if (
            isinstance(qty, bool)
            or not isinstance(qty, (int, float))
            or not math.isfinite(qty)
            or qty <= 0
            or int(qty) != qty
        ):
            raise ValueError("Paper quantity must be a positive whole number")
        price = params.get("price", str(order.get("price")))
        number = decimal_price(price)
        cap = self.service.policy.max_order_notional.get("USD")
        if cap is not None:
            # Preserve the submitted decimal at the monetary limit boundary too.
            with localcontext() as context:
                context.prec = max(128, len(str(qty)) + len(str(price)) + 2)
                if number * Decimal(str(qty)) > Decimal(str(cap)):
                    raise ValueError("Paper order exceeds the USD notional cap")
        self.service.policy.assess_order(
            "paper " + kind,
            OrderFacts(
                order_type="NORMAL",
                trd_side=str(order["trd_side"]),
                qty=int(qty),
                price=float(number),
                legs=tuple(LegFacts(instrument=item) for item in instruments),
            ),
        )
        if kind == "PLACE":
            return {
                "code": code,
                "qty": int(qty),
                "price": price,
                "trd_side": params["trd_side"],
                "order_type": "NORMAL",
                "time_in_force": "DAY",
                "fill_outside_rth": False,
                "session": "RTH",
                "adjust_limit": 0,
                "trd_env": "SIMULATE",
                "acc_id": account,
                "remark": params.get("remark", ""),
            }
        return {
            "modify_order_op": "NORMAL",
            "order_id": params["order_id"],
            "qty": int(qty),
            "price": price,
            "adjust_limit": 0,
            "trd_env": "SIMULATE",
            "acc_id": account,
        }

    def observations(self, row: dict) -> list[dict]:
        ctx = self.service.trade_ctx
        if ctx is None:
            raise ValueError("Trade context not connected")
        account = int(row["account"])
        results = []
        for operation, response in (
            (
                "order_list_query",
                ctx.order_list_query(
                    trd_env="SIMULATE", acc_id=account, refresh_cache=True
                ),
            ),
            (
                "history_order_list_query",
                ctx.history_order_list_query(trd_env="SIMULATE", acc_id=account),
            ),
        ):
            ret, data = response
            if ret != RET_OK:
                raise ValueError(f"{operation} failed: {data}")
            results.extend(
                serialize_identifiers(as_frame(operation, data).to_dict("records"))
            )
        return results

    def reconcile(self, operation_id: str) -> dict:
        with self.lock:
            row = self.store.lookup(operation_id)
            if row is None:
                raise ValueError("Operation is not owned by this journal")
            observations = self.observations(row)
            exact = [
                o
                for o in observations
                if row["broker_order_id"]
                and str(o.get("order_id")) == row["broker_order_id"]
            ]
            # Current/history copies of one broker identity are not two candidates.
            by_id = {str(o["order_id"]): o for o in exact}
            agreement = bool(exact) and all(
                all(
                    o.get(k) == exact[0].get(k)
                    for k in (
                        "order_status",
                        "qty",
                        "price",
                        "dealt_qty",
                        "dealt_avg_price",
                    )
                )
                for o in exact
            )
            proven = (
                agreement
                and row["kind"] == "PLACE"
                and row["state"] == "UNKNOWN_OUTCOME"
                and len(by_id) == 1
            )
            self.store.record_evidence(
                operation_id,
                list(by_id.values()) if proven else observations,
                reconcile=proven,
            )
            result = self.store.lookup(operation_id)
            assert result is not None
            return self.result(result)

    def acknowledge(
        self,
        *,
        principal: str | None,
        operator_id: str,
        operation_id: str,
        resolution: str,
        reason: str,
        evidence_reference: str,
        recovery_epoch: str,
        observed_state: str,
        accounted_facts: dict,
    ) -> dict:
        if principal != "operator" or operator_id != principal:
            raise PermissionError(
                "Separate authenticated operator capability required; "
                "identity must match principal"
            )
        if (
            resolution != "TERMINAL_ACCOUNTED"
            or not reason.strip()
            or not evidence_reference.strip()
        ):
            raise ValueError(
                "TERMINAL_ACCOUNTED requires reason and verified broker evidence"
            )
        with self.lock:
            row = self.store.lookup(operation_id)
            if row is None:
                raise ValueError("Operation is not owned by this journal")
            if recovery_epoch != self.store.epoch or observed_state != row["state"]:
                raise ExecutionConflict("Stale recovery epoch or observed state")
            request = json.loads(row["request"])
            target = (
                row["broker_order_id"]
                if row["kind"] == "PLACE"
                else request.get("order_id")
            )
            if not target or evidence_reference != "broker-order:" + str(target):
                raise ValueError(
                    "Evidence must identify the recorded broker order; "
                    "candidates do not establish ownership"
                )
            matches = [
                o
                for o in self.observations(row)
                if str(o.get("order_id")) == str(target)
            ]
            if not matches:
                raise ValueError(
                    "No verifiable broker evidence; absence cannot account "
                    "for an operation"
                )
            final = matches[0]
            if any(
                any(
                    o.get(key) != final.get(key)
                    for key in (
                        "order_status",
                        "dealt_qty",
                        "dealt_avg_price",
                        "code",
                        "qty",
                        "price",
                        "trd_side",
                    )
                )
                for o in matches
            ):
                raise ValueError("Broker observations disagree; review again")
            status = final.get("order_status")
            if status not in TERMINAL_BROKER:
                raise ValueError("Broker order is not terminal")
            facts = {
                "final_status": status,
                "filled_quantity": final.get("dealt_qty"),
                "average_fill_price": final.get("dealt_avg_price"),
                "remaining_executable_quantity": 0,
            }
            for key, value in facts.items():
                if value is None or accounted_facts.get(key) != value:
                    raise ValueError(
                        f"Accounted fact {key} does not match broker evidence"
                    )
            # Record actual account position, not the unsupported claim that a fill
            # necessarily represents the whole current position.
            positions = self.service.get_positions(
                code=str(final["code"]),
                trd_env="SIMULATE",
                acc_id=int(row["account"]),
                refresh_cache=True,
            )
            current_position = sum(
                Decimal(str(p["qty"]))
                for p in positions
                if p.get("code") == final["code"]
            )
            if (
                Decimal(str(accounted_facts.get("resulting_position")))
                != current_position
            ):
                raise ValueError(
                    "Resulting position does not match broker position evidence"
                )
            self.store.acknowledge(
                operation_id,
                operator_id=principal,
                recovery_epoch=recovery_epoch,
                observed_state=observed_state,
                reason=reason,
                evidence_reference=evidence_reference,
                accounted_facts=accounted_facts,
            )
            result = self.store.lookup(operation_id)
            assert result is not None
            return self.result(result)
