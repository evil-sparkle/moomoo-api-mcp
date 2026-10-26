"""Read-only order/deal queries, independent of execution and recovery state."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from moomoo import RET_OK, SecurityFirm

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.query_contracts import (
    DealView,
    OrderFeeResult,
    OrderQueryResult,
    OrderView,
    QueryPage,
    QueryScope,
    TradeQueryError,
    deal_view,
    exact_identifier,
    optional_number,
    order_view,
    plain_combo_legs,
)
from moomoo_mcp.services.query_snapshots import MAX_SNAPSHOT_ROWS, QuerySnapshots
from moomoo_mcp.services.sdk_response import as_frame
from moomoo_mcp.services.validation import validate_choice

if TYPE_CHECKING:
    from moomoo_mcp.services.trade_service import TradeService

PAGE_OPERATIONS = {
    "get_orders_page": "order_list_query",
    "get_deals_page": "deal_list_query",
    "get_history_orders_page": "history_order_list_query",
    "get_history_deals_page": "history_deal_list_query",
}
SDK_QUERY_OPERATIONS = frozenset(PAGE_OPERATIONS.values()) | {"order_fee_query"}


def require_real(trd_env: str) -> None:
    if validate_choice("trd_env", trd_env, ("REAL", "SIMULATE")) != "REAL":
        raise ValueError(
            "Deal and fee queries are supported only for REAL accounts; "
            "use order queries for SIMULATE execution quantities"
        )


def validate_order_id(order_id: str) -> None:
    if not isinstance(order_id, str) or not order_id or order_id != order_id.strip():
        raise ValueError("order_id must be a non-empty exact broker identifier string")


def validate_fee_request(order_id_list: list[str], trd_env: str) -> None:
    require_real(trd_env)
    if not isinstance(order_id_list, list) or not 1 <= len(order_id_list) <= 400:
        raise ValueError("order_id_list must contain between 1 and 400 identifiers")
    for order_id in order_id_list:
        validate_order_id(order_id)


def validate_lookup(source: str, order_id: str, start: str, end: str) -> str:
    validate_order_id(order_id)
    source = validate_choice("source", source, ("CURRENT", "HISTORY"))
    if source == "CURRENT":
        if start or end:
            raise ValueError("start/end are available only for HISTORY lookup")
        return source
    if not start or not end:
        raise ValueError("HISTORY lookup requires explicit start and end dates")
    dates = []
    for field, value in (("start", start), ("end", end)):
        if not isinstance(value, str) or len(value) not in {10, 19}:
            raise ValueError(f"{field} must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
        form = "%Y-%m-%d" if len(value) == 10 else "%Y-%m-%d %H:%M:%S"
        try:
            parsed = datetime.strptime(value, form)
        except ValueError as exc:
            raise ValueError(f"{field} has an invalid date or timestamp") from exc
        if parsed.strftime(form) != value:
            raise ValueError(f"{field} must use the documented date format")
        dates.append(parsed)
    if dates[0] > dates[1] or (dates[1] - dates[0]).days > 365:
        raise ValueError("HISTORY lookup requires an ordered range of at most 366 days")
    return source


class TradeQueryService:
    """Use the owner's current process context, including after reconnect."""

    def __init__(self, owner: "TradeService"):
        self.owner = owner
        self.snapshots = QuerySnapshots()

    def _records(
        self, operation: str, params: dict[str, Any], *, row_limit: int | None = None
    ) -> list[dict]:
        if operation not in SDK_QUERY_OPERATIONS:
            raise ValueError("Unsupported trading query operation")
        context = self.owner.trade_ctx
        if context is None:
            raise RuntimeError("Trade context not connected")
        ret, data = self.owner.dispatcher.call(
            operation, getattr(context, operation), **params
        )
        if ret != RET_OK:
            raise TradeQueryError(
                "BROKER_ERROR", operation, f"{operation} failed: {data}"
            )
        if data is None and operation in {
            "order_list_query",
            "history_order_list_query",
        }:
            return []
        try:
            frame = as_frame(operation, data)
        except RuntimeError as exc:
            raise TradeQueryError("INVALID_RESPONSE", operation, str(exc)) from exc
        if row_limit is not None and len(frame) > row_limit:
            raise TradeQueryError(
                "SNAPSHOT_LIMIT", operation, "too many returned rows; narrow the query"
            )
        return plain_combo_legs(frame.to_dict("records"))

    def get_orders(
        self,
        code="",
        status_filter_list=None,
        trd_env="SIMULATE",
        acc_id: int | str = "0",
        refresh_cache=False,
    ) -> list[dict]:
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        return self._records(
            "order_list_query",
            {
                "code": code,
                "status_filter_list": self.owner._convert_status_filter(
                    status_filter_list
                ),
                "trd_env": trd_env,
                "acc_id": acc_id,
                "refresh_cache": refresh_cache,
            },
        )

    def get_deals(
        self,
        code="",
        trd_env="SIMULATE",
        acc_id: int | str = "0",
        refresh_cache=False,
    ) -> list[dict]:
        require_real(trd_env)
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        return self._records(
            "deal_list_query",
            {
                "code": code,
                "trd_env": trd_env,
                "acc_id": acc_id,
                "refresh_cache": refresh_cache,
            },
        )

    def get_history_orders(
        self,
        code="",
        status_filter_list=None,
        start="",
        end="",
        trd_env="SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        return self._records(
            "history_order_list_query",
            {
                "code": code,
                "status_filter_list": self.owner._convert_status_filter(
                    status_filter_list
                ),
                "start": start,
                "end": end,
                "trd_env": trd_env,
                "acc_id": acc_id,
            },
        )

    def get_history_deals(
        self,
        code="",
        start="",
        end="",
        trd_env="SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        require_real(trd_env)
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        return self._records(
            "history_deal_list_query",
            {
                "code": code,
                "start": start,
                "end": end,
                "trd_env": trd_env,
                "acc_id": acc_id,
            },
        )

    @staticmethod
    def _scope(source: str, trd_env: str, acc_id: int, filters: dict) -> QueryScope:
        return {
            "source": source,
            "acc_id": str(acc_id),
            "trd_env": trd_env,
            "filters": filters,
            "coverage": (
                "Provider current orders: open orders and recently terminal orders"
                if source == "CURRENT_ORDERS"
                else "Provider-returned rows within the requested account and filters; "
                "not proof of lifetime completeness"
            ),
        }

    def get_order(
        self,
        order_id: str,
        *,
        source="CURRENT",
        code="",
        start="",
        end="",
        trd_env="REAL",
        acc_id: int | str = "0",
        refresh_cache=False,
    ) -> OrderQueryResult:
        source = validate_lookup(source, order_id, start, end)
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        params = {"code": code, "trd_env": trd_env, "acc_id": acc_id}
        if source == "CURRENT":
            params.update(order_id=order_id, refresh_cache=refresh_cache)
            operation, scope_source = "order_list_query", "CURRENT_ORDERS"
        else:
            params.update(start=start, end=end)
            operation, scope_source = "history_order_list_query", "HISTORY_ORDERS"
        rows = self._records(operation, params, row_limit=MAX_SNAPSHOT_ROWS)
        matches = [
            row
            for row in rows
            if exact_identifier(row.get("order_id"), "order_id") == order_id
        ]
        if len(matches) > 1:
            raise TradeQueryError(
                "AMBIGUOUS_ORDER", operation, "multiple exact matches"
            )
        return {
            "found": bool(matches),
            "order": order_view(matches[0]) if matches else None,
            "scope": self._scope(
                scope_source,
                trd_env,
                acc_id,
                {
                    "order_id": order_id,
                    "code": code,
                    "start": start,
                    "end": end,
                    "refresh_cache": refresh_cache if source == "CURRENT" else None,
                },
            ),
            "fetched_at": utc_now_iso(),
        }

    def check_fee_account(self, acc_id: int) -> None:
        accounts = self.owner.get_accounts()
        matching = [
            account
            for account in accounts
            if self.owner._exact_account_id(account.get("acc_id"), allow_zero=False)
            == acc_id
        ]
        if len(matching) != 1:
            raise ValueError("Fee account discovery is missing or ambiguous")
        if (
            matching[0].get("security_firm") == SecurityFirm.FUTUCA
            or self.owner.security_firm == SecurityFirm.FUTUCA
        ):
            raise ValueError("Order fee queries are unavailable for Moomoo CA accounts")

    def get_order_fees(
        self,
        order_id_list: list[str],
        *,
        trd_env="REAL",
        acc_id: int | str = "0",
    ) -> list[OrderFeeResult]:
        validate_fee_request(order_id_list, trd_env)
        trd_env, acc_id = self.owner.resolve_read_account(trd_env, acc_id)
        self.check_fee_account(acc_id)
        rows = self._records(
            "order_fee_query",
            {
                "order_id_list": order_id_list,
                "trd_env": trd_env,
                "acc_id": acc_id,
            },
            row_limit=400,
        )
        result: list[OrderFeeResult] = []
        seen: set[str] = set()
        for row in rows:
            order_id = exact_identifier(row.get("order_id"), "order_id")
            if order_id not in order_id_list:
                raise TradeQueryError(
                    "INVALID_RESPONSE", "order_fee_query", "unexpected order identifier"
                )
            if order_id in seen:
                raise TradeQueryError(
                    "INVALID_RESPONSE", "order_fee_query", "duplicate order identifier"
                )
            seen.add(order_id)
            details = row.get("fee_details")
            normalized = None
            if isinstance(details, (list, tuple)):
                normalized = []
                for detail in details:
                    if not isinstance(detail, (list, tuple)) or len(detail) != 2:
                        raise TradeQueryError(
                            "INVALID_RESPONSE", "order_fee_query", "invalid fee detail"
                        )
                    normalized.append(
                        {"name": str(detail[0]), "amount": optional_number(detail[1])}
                    )
            result.append(
                {
                    "order_id": order_id,
                    "fee_amount": optional_number(row.get("fee_amount")),
                    "fee_details": normalized,
                }
            )
        return result

    def page(self, method: str, params: dict[str, Any]) -> QueryPage:
        operation = PAGE_OPERATIONS[method]
        QuerySnapshots.validate_limit(params["limit"])
        environment = validate_choice(
            "trd_env", params["trd_env"], ("REAL", "SIMULATE")
        )
        if "deals" in method:
            require_real(environment)
        query = {key: value for key, value in params.items() if key != "cursor"} | {
            "method": method,
            "trd_env": environment,
            "acc_id": str(
                self.owner._exact_account_id(params["acc_id"], allow_zero=True)
            ),
        }
        cursor = params.get("cursor")
        if cursor is not None:
            return self.snapshots.continue_page(cursor, query)
        environment, account = self.owner.resolve_read_account(
            environment, params["acc_id"]
        )
        sdk_params = {
            key: value
            for key, value in params.items()
            if key not in {"limit", "cursor"}
        }
        sdk_params.update(trd_env=environment, acc_id=account)
        if "status_filter_list" in sdk_params:
            sdk_params["status_filter_list"] = self.owner._convert_status_filter(
                sdk_params["status_filter_list"]
            )
        rows = self._records(operation, sdk_params, row_limit=MAX_SNAPSHOT_ROWS)
        views: list[OrderView | DealView] = [
            deal_view(row) if "deals" in method else order_view(row) for row in rows
        ]
        source = ("HISTORY" if "history" in method else "CURRENT") + (
            "_DEALS" if "deals" in method else "_ORDERS"
        )
        filters = {
            key: value
            for key, value in params.items()
            if key not in {"acc_id", "trd_env", "limit", "cursor"}
        }
        return self.snapshots.create(
            query,
            views,
            self._scope(source, environment, account, filters),
            utc_now_iso(),
        )
