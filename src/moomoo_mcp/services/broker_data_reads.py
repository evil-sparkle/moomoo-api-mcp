"""Selected bounded read APIs sharing the existing gateway and dispatcher."""

from typing import Any

from moomoo import RET_OK, OpenQuoteContext
from moomoo.common.pb import Qot_Common_pb2

from moomoo_mcp.services.broker_dispatch import BrokerRequestDispatcher
from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.data_evidence import (
    DataEvidence,
    evidence,
    frame_records,
    validate_codes,
)
from moomoo_mcp.services.instruments import InstrumentAdapter
from moomoo_mcp.services.query_contracts import TradeQueryError
from moomoo_mcp.services.quote_errors import raise_quote_error
from moomoo_mcp.services.sdk_response import as_dict
from moomoo_mcp.services.validation import validate_choice, validate_date_range

BROKER_READ_OPERATIONS = frozenset(
    {
        "get_market_snapshot",
        "get_history_kl_quota",
        "get_rehab",
        "get_corporate_actions_dividends",
        "get_earnings_calendar",
        "get_option_volatility",
    }
)


def validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")


class BrokerDataReads:
    quote_ctx: OpenQuoteContext | None
    dispatcher: BrokerRequestDispatcher
    instrument_adapter: InstrumentAdapter

    def _read(self, operation: str, **params: Any) -> Any:
        if operation not in BROKER_READ_OPERATIONS:
            raise ValueError("Broker read operation is not in the closed inventory")
        if self.quote_ctx is None:
            raise RuntimeError("Quote context not connected")
        method = getattr(self.quote_ctx, operation, None)
        if not callable(method):
            raise TradeQueryError(
                "CAPABILITY_UNAVAILABLE", operation, "SDK method absent"
            )
        response: Any = self.dispatcher.call(operation, method, **params)
        if not isinstance(response, tuple) or len(response) != 2:
            raise TradeQueryError("INVALID_RESPONSE", operation, "expected SDK pair")
        ret, data = response
        if ret != RET_OK:
            raise_quote_error(
                operation, data, params.get("code_list", [params.get("code", "")])
            )
        return data

    def get_market_snapshot_evidence(self, codes: list[str]) -> DataEvidence:
        validate_codes(codes)
        started = utc_now_iso()
        rows = frame_records(
            "get_market_snapshot", self._read("get_market_snapshot", code_list=codes)
        )
        return evidence(
            "get_market_snapshot",
            started,
            rows,
            requested=codes,
            limitations=[
                "No per-side timestamp, delay, NBBO or atomic liquidity guarantee."
            ],
        )

    def get_instrument_facts(self, codes: list[str]) -> DataEvidence:
        validate_codes(codes)
        started = utc_now_iso()
        rows = self.instrument_adapter.read_facts(codes)
        result = evidence(
            "get_stock_basicinfo+get_market_snapshot",
            started,
            rows,
            requested=codes,
            limitations=[
                "Unknown/conflicting lifecycle facts do not establish compatibility.",
                "Static metadata may be cached; snapshots are requested anew.",
            ],
        )
        missing = [
            row["code"]
            for row in rows
            if not row["snapshot_present"] or not row["reference_present"]
        ]
        result["coverage"]["missing"] = missing
        result["coverage"]["complete_response"] = not missing
        result["errors"] = dict.fromkeys(missing, "INCOMPLETE_FACT_SOURCES")
        return result

    def get_history_kl_quota(self) -> DataEvidence:
        """Aggregate only; omit other clients' security request details."""
        started = utc_now_iso()
        data = self._read("get_history_kl_quota", get_detail=False)
        if not isinstance(data, tuple) or len(data) != 3:
            raise TradeQueryError(
                "INVALID_RESPONSE", "get_history_kl_quota", "expected quota tuple"
            )
        used, remaining, _details = data
        for value in (used, remaining):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise TradeQueryError(
                    "INVALID_RESPONSE", "get_history_kl_quota", "invalid quota"
                )
        return evidence(
            "get_history_kl_quota",
            started,
            [{"used_quota": used, "remain_quota": remaining}],
            scope={
                "scope": "provider user",
                "window": "provider rolling window; deployment-specific",
                "unit": "provider historical quota units",
                "details_requested": False,
                "documented_window_days": 7,
                "runtime_window_verified": False,
            },
            limitations=[
                "Observation, not a reservation; other clients may consume quota.",
                "Separate from subscription and endpoint rate limits.",
            ],
        )

    def get_rehab(self, code: str, limit: int = 200) -> DataEvidence:
        validate_limit(limit)
        validate_codes([code])
        started = utc_now_iso()
        rows = frame_records("get_rehab", self._read("get_rehab", code=code))
        return evidence(
            "get_rehab",
            started,
            rows,
            key="ex_div_date",
            limit=limit,
            scope={"code": code, "limit": limit},
            limitations=[
                "Provider adjustment factors, not a complete corporate-action archive.",
                "Dates/factor bases preserved; publication/revision history unknown.",
            ],
        )

    def get_corporate_actions_dividends(
        self, code: str, limit: int = 200
    ) -> DataEvidence:
        validate_limit(limit)
        validate_codes([code])
        started = utc_now_iso()
        data = as_dict(
            "get_corporate_actions_dividends",
            self._read("get_corporate_actions_dividends", code=code),
        )
        rows = data.get("dividend_list")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise TradeQueryError(
                "INVALID_RESPONSE",
                "get_corporate_actions_dividends",
                "dividend_list absent or invalid",
            )
        return evidence(
            "get_corporate_actions_dividends",
            started,
            rows,
            key="event_id",
            limit=limit,
            scope={
                "code": code,
                "limit": limit,
                "order": "provider announcement descending",
            },
            limitations=[
                "No currency/amount parsed from prose or inferred event status.",
                "Publication/revision history unknown; not point-in-time backtests.",
            ],
        )

    def get_earnings_calendar(
        self, market: str, start: str, end: str, limit: int = 200
    ) -> DataEvidence:
        validate_limit(limit)
        market = validate_choice(
            "market", market, ("US", "HK", "SH", "SZ", "SG", "JP", "AU", "CA")
        )
        if not start or not end:
            raise ValueError("earnings calendar requires explicit start/end dates")
        validate_date_range(start, end, max_span_days=7)
        started = utc_now_iso()
        rows = frame_records(
            "get_earnings_calendar",
            self._read(
                "get_earnings_calendar", market=market, begin_date=start, end_date=end
            ),
        )
        # A security can legitimately report multiple fiscal periods/events.
        return evidence(
            "get_earnings_calendar",
            started,
            rows,
            key="event_id",
            limit=limit,
            scope={
                "market": market,
                "start": start,
                "end": end,
                "limit": limit,
                "sort": "provider default HOT",
            },
            limitations=[
                "BEFORE/AFTER/REGULAR is provider timing; absent timing is unknown.",
                "Fiscal/event time is not historical publication availability.",
                "Provider universe only; paging/revision coverage not established.",
            ],
        )

    def get_option_volatility(
        self, code: str, period: str = "MONTH", hv_days: int = 30, limit: int = 200
    ) -> DataEvidence:
        validate_codes([code])
        validate_limit(limit)
        period = validate_choice(
            "period", period, ("WEEK", "MONTH", "QUARTER", "HALF_YEAR", "YEAR")
        )
        if (
            isinstance(hv_days, bool)
            or not isinstance(hv_days, int)
            or not 5 <= hv_days <= 250
        ):
            raise ValueError("hv_days must be an integer from 5 to 250")
        enum_names = {
            "WEEK": "Week",
            "MONTH": "Month",
            "QUARTER": "Quarter",
            "HALF_YEAR": "HalfYear",
            "YEAR": "Year",
        }
        descriptor = Qot_Common_pb2.DESCRIPTOR.enum_types_by_name.get(
            "OptionVolatilityTimePeriodType"
        )
        member = (
            descriptor.values_by_name.get(
                "OptionVolatilityTimePeriodType_" + enum_names[period]
            )
            if descriptor
            else None
        )
        if member is None:
            raise TradeQueryError(
                "CAPABILITY_UNAVAILABLE",
                "get_option_volatility",
                "SDK period enum absent",
            )
        started = utc_now_iso()
        facts = self.instrument_adapter.read_facts([code])[0]
        if (
            facts["facts"]["classification"]["value"] != "DRVT"
            or facts["facts"]["is_option"]["value"] is not True
        ):
            raise ValueError(
                "volatility requires a provider-verified option contract code"
            )
        rows = frame_records(
            "get_option_volatility",
            self._read(
                "get_option_volatility",
                code=code,
                query_time_period=member.number,
                hv_time_period=hv_days,
            ),
        )
        return evidence(
            "get_option_volatility",
            started,
            rows,
            key="timestamp",
            limit=limit,
            scope={
                "code": code,
                "period": period,
                "hv_days": hv_days,
                "volatility_unit": "percentage points",
                "timestamp_unit": "Unix seconds; provider market-day midnight",
                "annualization_basis": None,
            },
            limitations=[
                "Provider observations, not reconstructed or forward-filled.",
                "Publication/revision/expired coverage unknown; not point-in-time.",
                "IV below HV does not establish value or physical expected return.",
            ],
        )
