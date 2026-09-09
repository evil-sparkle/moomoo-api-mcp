"""Explicit historical-candle pagination (R7)."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from moomoo_mcp.services.market_data_service import MarketDataService
from moomoo_mcp.tools.kline_cursor import (
    CursorError,
    decode_cursor,
    encode_cursor,
    resolve_date_range,
)
from tests.conftest import call_mcp_tool

BASE_QUERY = {
    "code": "US.AAPL",
    "ktype": "K_DAY",
    "start": "2026-01-01",
    "end": "2026-03-31",
    "max_count": 2,
    "autype": "QFQ",
}


def _candles(*days):
    return pd.DataFrame(
        [
            {
                "code": "US.AAPL",
                "time_key": f"2026-01-{day:02d} 00:00:00",
                "open": 100.0 + day,
                "high": 101.0 + day,
                "low": 99.0 + day,
                "close": 100.5 + day,
                "volume": 1000 * day,
            }
            for day in days
        ]
    )


@pytest.fixture
def quote_ctx():
    return MagicMock()


@pytest.fixture
def kline_context(mcp_app_context, quote_ctx):
    mcp_app_context.market_data_service = MarketDataService(quote_ctx=quote_ctx)
    return mcp_app_context


class TestCursorCodec:
    """The envelope binds a provider token to the query that produced it."""

    def test_roundtrip_preserves_the_token_exactly(self):
        token = b"\x00\x01\xfe\xff binary token"

        cursor = encode_cursor(token, BASE_QUERY, ("2026-01-01", "2026-03-31"))
        decoded, resolved = decode_cursor(cursor, BASE_QUERY)

        assert decoded == token
        assert resolved == ("2026-01-01", "2026-03-31")

    def test_cursor_is_url_safe_text(self):
        cursor = encode_cursor(b"\xfb\xff", BASE_QUERY, ("2026-01-01", "2026-03-31"))

        assert "+" not in cursor and "/" not in cursor
        assert cursor.isascii()

    def test_cursor_carries_no_credentials_or_account_data(self):
        import base64
        import json

        cursor = encode_cursor(b"tok", BASE_QUERY, ("2026-01-01", "2026-03-31"))
        envelope = json.loads(base64.urlsafe_b64decode(cursor))

        assert set(envelope) == {"v", "q", "r", "k"}
        assert set(envelope["q"]) == set(BASE_QUERY)

    @pytest.mark.parametrize(
        "field,changed",
        [
            ("code", "US.TSLA"),
            ("ktype", "K_60M"),
            ("start", "2026-02-01"),
            ("end", "2026-04-30"),
            ("max_count", 50),
            ("autype", "HFQ"),
        ],
    )
    def test_changed_filter_is_rejected(self, field, changed):
        cursor = encode_cursor(b"tok", BASE_QUERY, ("2026-01-01", "2026-03-31"))

        with pytest.raises(CursorError, match="different query"):
            decode_cursor(cursor, {**BASE_QUERY, field: changed})

    @pytest.mark.parametrize(
        "bad", ["", "   ", "not-base64!!", "YWJj", "x" * 9000]
    )
    def test_malformed_cursor_is_rejected(self, bad):
        with pytest.raises(CursorError):
            decode_cursor(bad, BASE_QUERY)

    def test_unknown_version_is_rejected(self):
        import base64
        import json

        envelope = json.dumps(
            {"v": 99, "q": BASE_QUERY, "r": {"start": "a", "end": "b"}, "k": "dG9r"}
        )
        cursor = base64.urlsafe_b64encode(envelope.encode()).decode()

        with pytest.raises(CursorError, match="version"):
            decode_cursor(cursor, BASE_QUERY)

    def test_missing_envelope_fields_are_rejected(self):
        import base64
        import json

        cursor = base64.urlsafe_b64encode(json.dumps({"v": 1}).encode()).decode()

        with pytest.raises(CursorError, match="bound query"):
            decode_cursor(cursor, BASE_QUERY)


class TestDateResolution:
    """Omitted bounds resolve once, not once per page."""

    def test_both_omitted_uses_a_year_ending_today(self):
        assert resolve_date_range(None, None, today=date(2026, 9, 10)) == (
            "2025-09-10",
            "2026-09-10",
        )

    def test_only_end_given_resolves_the_start(self):
        assert resolve_date_range(None, "2026-03-31") == ("2025-03-31", "2026-03-31")

    def test_only_start_given_resolves_the_end(self):
        assert resolve_date_range("2026-01-01", None) == ("2026-01-01", "2027-01-01")

    def test_both_given_are_untouched(self):
        assert resolve_date_range("2026-01-01", "2026-02-01") == (
            "2026-01-01",
            "2026-02-01",
        )


class TestPaginationThroughMcp:
    """Traversal behaviour observed through actual MCP tool calls."""

    @pytest.mark.asyncio
    async def test_first_page_reports_more_with_a_usable_cursor(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (0, _candles(2, 5), b"tok-1")

        page = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )

        assert page.structured["has_more"] is True
        assert page.structured["next_cursor"]
        assert [row["time_key"] for row in page.structured["data"]] == [
            "2026-01-02 00:00:00",
            "2026-01-05 00:00:00",
        ]
        assert quote_ctx.request_history_kline.call_args.kwargs["page_req_key"] is None

    @pytest.mark.asyncio
    async def test_successive_pages_forward_the_token_and_filters(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (0, _candles(2, 5), b"tok-1")
        first = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )

        quote_ctx.request_history_kline.return_value = (0, _candles(6, 7), b"tok-2")
        second = await call_mcp_tool(
            kline_context,
            "get_historical_klines_page",
            {**BASE_QUERY, "cursor": first.structured["next_cursor"]},
        )

        kwargs = quote_ctx.request_history_kline.call_args.kwargs
        assert kwargs["page_req_key"] == b"tok-1"
        assert kwargs["code"] == "US.AAPL"
        assert kwargs["start"] == "2026-01-01"
        assert kwargs["end"] == "2026-03-31"
        assert kwargs["max_count"] == 2
        assert [row["time_key"] for row in second.structured["data"]] == [
            "2026-01-06 00:00:00",
            "2026-01-07 00:00:00",
        ]

    @pytest.mark.asyncio
    async def test_full_traversal_preserves_order_without_duplicates(
        self, kline_context, quote_ctx
    ):
        pages = [
            (0, _candles(2, 5), b"tok-1"),
            (0, _candles(6, 7), b"tok-2"),
            (0, _candles(8), None),
        ]
        quote_ctx.request_history_kline.side_effect = pages

        collected = []
        cursor = None
        for _ in range(len(pages)):
            args = dict(BASE_QUERY)
            if cursor:
                args["cursor"] = cursor
            page = await call_mcp_tool(
                kline_context, "get_historical_klines_page", args
            )
            collected += page.structured["data"]
            cursor = page.structured["next_cursor"]
            if cursor is None:
                break

        assert cursor is None
        assert [row["time_key"] for row in collected] == [
            "2026-01-02 00:00:00",
            "2026-01-05 00:00:00",
            "2026-01-06 00:00:00",
            "2026-01-07 00:00:00",
            "2026-01-08 00:00:00",
        ]

    @pytest.mark.asyncio
    async def test_final_page_reports_completion(self, kline_context, quote_ctx):
        quote_ctx.request_history_kline.return_value = (0, _candles(2), None)

        page = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )

        assert page.structured["next_cursor"] is None
        assert page.structured["has_more"] is False

    @pytest.mark.asyncio
    async def test_empty_intermediate_page_still_reports_more(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (
            0,
            pd.DataFrame([], columns=["code", "time_key"]),
            b"tok-9",
        )

        page = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )

        assert page.structured["data"] == []
        assert page.structured["has_more"] is True

        # The retained token is the one the provider gave us.
        quote_ctx.request_history_kline.return_value = (0, _candles(2), None)
        await call_mcp_tool(
            kline_context,
            "get_historical_klines_page",
            {**BASE_QUERY, "cursor": page.structured["next_cursor"]},
        )
        assert (
            quote_ctx.request_history_kline.call_args.kwargs["page_req_key"] == b"tok-9"
        )

    @pytest.mark.asyncio
    async def test_corrupt_cursor_is_rejected_before_the_gateway(
        self, kline_context, quote_ctx
    ):
        with pytest.raises(Exception, match="cursor"):
            await call_mcp_tool(
                kline_context,
                "get_historical_klines_page",
                {**BASE_QUERY, "cursor": "not a real cursor"},
            )

        quote_ctx.request_history_kline.assert_not_called()

    @pytest.mark.asyncio
    async def test_cursor_reused_with_a_changed_symbol_is_rejected(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (0, _candles(2), b"tok-1")
        first = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )
        quote_ctx.request_history_kline.reset_mock()

        with pytest.raises(Exception, match="different query"):
            await call_mcp_tool(
                kline_context,
                "get_historical_klines_page",
                {
                    **BASE_QUERY,
                    "code": "US.TSLA",
                    "cursor": first.structured["next_cursor"],
                },
            )

        quote_ctx.request_history_kline.assert_not_called()

    @pytest.mark.asyncio
    async def test_cursor_reused_with_a_changed_adjustment_is_rejected(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (0, _candles(2), b"tok-1")
        first = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )
        quote_ctx.request_history_kline.reset_mock()

        with pytest.raises(Exception, match="different query"):
            await call_mcp_tool(
                kline_context,
                "get_historical_klines_page",
                {
                    **BASE_QUERY,
                    "autype": "HFQ",
                    "cursor": first.structured["next_cursor"],
                },
            )

        quote_ctx.request_history_kline.assert_not_called()

    @pytest.mark.asyncio
    async def test_omitted_dates_stay_fixed_across_a_midnight_transition(
        self, kline_context, quote_ctx, monkeypatch
    ):
        """The resolved window must not move when the calendar day rolls over."""
        query = {"code": "US.AAPL", "ktype": "K_DAY", "max_count": 2, "autype": "QFQ"}

        class FrozenDate(date):
            _today = date(2026, 9, 10)

            @classmethod
            def today(cls):
                return cls._today

        monkeypatch.setattr("moomoo_mcp.tools.kline_cursor.date", FrozenDate)

        quote_ctx.request_history_kline.return_value = (0, _candles(2), b"tok-1")
        first = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(query)
        )
        first_kwargs = quote_ctx.request_history_kline.call_args.kwargs
        assert first_kwargs["start"] == "2025-09-10"
        assert first_kwargs["end"] == "2026-09-10"

        # Midnight passes between pages.
        FrozenDate._today = date(2026, 9, 11)

        quote_ctx.request_history_kline.return_value = (0, _candles(3), None)
        await call_mcp_tool(
            kline_context,
            "get_historical_klines_page",
            {**query, "cursor": first.structured["next_cursor"]},
        )

        second_kwargs = quote_ctx.request_history_kline.call_args.kwargs
        assert second_kwargs["start"] == "2025-09-10"
        assert second_kwargs["end"] == "2026-09-10"

    @pytest.mark.asyncio
    async def test_later_page_failure_is_an_error_not_completion(
        self, kline_context, quote_ctx
    ):
        quote_ctx.request_history_kline.return_value = (0, _candles(2), b"tok-1")
        first = await call_mcp_tool(
            kline_context, "get_historical_klines_page", dict(BASE_QUERY)
        )

        quote_ctx.request_history_kline.return_value = (-1, "quota exceeded", None)
        with pytest.raises(Exception, match="quota exceeded"):
            await call_mcp_tool(
                kline_context,
                "get_historical_klines_page",
                {**BASE_QUERY, "cursor": first.structured["next_cursor"]},
            )


class TestLegacyToolUnchanged:
    """The original tool keeps its list response."""

    @pytest.mark.asyncio
    async def test_legacy_call_returns_a_plain_list(self, kline_context, quote_ctx):
        quote_ctx.request_history_kline.return_value = (0, _candles(2, 5), b"tok-1")

        result = await call_mcp_tool(
            kline_context,
            "get_historical_klines",
            {"code": "US.AAPL", "ktype": "K_DAY", "max_count": 2},
        )

        rows = result.structured["result"]
        assert isinstance(rows, list)
        assert [row["time_key"] for row in rows] == [
            "2026-01-02 00:00:00",
            "2026-01-05 00:00:00",
        ]
        assert "next_cursor" not in result.structured
