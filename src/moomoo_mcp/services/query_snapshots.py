"""Bounded immutable pagination for APIs without broker continuation tokens."""

import copy
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from moomoo_mcp.services.query_contracts import (
    DealView,
    OrderView,
    QueryPage,
    QueryScope,
    TradeQueryError,
)

MAX_PAGE_SIZE = 200
MAX_SNAPSHOT_ROWS = 10_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOTS = 16
SNAPSHOT_TTL_SECONDS = 120.0


@dataclass(frozen=True)
class _Snapshot:
    query: dict[str, Any]
    items: list[OrderView | DealView]
    scope: QueryScope
    fetched_at: str
    expires: float
    size: int


class QuerySnapshots:
    """Thread-safe process cache; tokens grant no broker authorization."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self._snapshots: OrderedDict[str, _Snapshot] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit < 1 or limit > MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

    def _remove(self, key: str) -> None:
        self._bytes -= self._snapshots.pop(key).size

    def _expire(self) -> None:
        now = self.clock()
        for key in list(self._snapshots):
            if self._snapshots[key].expires <= now:
                self._remove(key)

    @staticmethod
    def _page(snapshot: _Snapshot, key: str, offset: int, limit: int) -> QueryPage:
        items = copy.deepcopy(snapshot.items[offset : offset + limit])
        following = offset + len(items)
        return {
            "items": items,
            "next_cursor": (
                f"{key}:{following}" if following < len(snapshot.items) else None
            ),
            "total_rows": len(snapshot.items),
            "returned_rows": len(items),
            "scope": copy.deepcopy(snapshot.scope),
            "fetched_at": snapshot.fetched_at,
        }

    def create(
        self,
        query: dict[str, Any],
        items: list[OrderView | DealView],
        scope: QueryScope,
        fetched_at: str,
    ) -> QueryPage:
        limit = query["limit"]
        self.validate_limit(limit)
        if len(items) > MAX_SNAPSHOT_ROWS:
            raise TradeQueryError(
                "SNAPSHOT_LIMIT", "pagination", "too many rows; narrow the query"
            )
        size = 0
        payload = {"items": items, "query": query, "scope": scope}
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(
            payload
        ):
            size += len(chunk.encode("utf-8"))
            if size > min(MAX_SNAPSHOT_BYTES, MAX_CACHE_BYTES):
                raise TradeQueryError(
                    "SNAPSHOT_LIMIT",
                    "pagination",
                    "response too large; narrow the query",
                )
        snapshot = _Snapshot(
            copy.deepcopy(query),
            copy.deepcopy(items),
            copy.deepcopy(scope),
            fetched_at,
            self.clock() + SNAPSHOT_TTL_SECONDS,
            size,
        )
        key = secrets.token_urlsafe(24)
        if len(items) <= limit:
            return self._page(snapshot, key, 0, limit)
        with self._lock:
            self._expire()
            while self._snapshots and (
                len(self._snapshots) >= MAX_SNAPSHOTS
                or self._bytes + size > MAX_CACHE_BYTES
            ):
                self._remove(next(iter(self._snapshots)))
            self._snapshots[key] = snapshot
            self._bytes += size
            return self._page(snapshot, key, 0, limit)

    def continue_page(self, cursor: str, query: dict[str, Any]) -> QueryPage:
        self.validate_limit(query["limit"])
        if not isinstance(cursor, str) or len(cursor) > 128:
            raise ValueError("cursor must be a server-issued continuation")
        key, separator, position = cursor.partition(":")
        if not separator or not position.isdecimal() or len(position) > 8:
            raise ValueError("cursor must be a server-issued continuation")
        offset = int(position)
        with self._lock:
            self._expire()
            snapshot = self._snapshots.get(key)
            if snapshot is None:
                raise TradeQueryError(
                    "CURSOR_EXPIRED", "pagination", "snapshot expired or was evicted"
                )
            if snapshot.query != query:
                raise TradeQueryError(
                    "CURSOR_SCOPE", "pagination", "cursor belongs to another query"
                )
            limit = query["limit"]
            if offset < limit or offset >= len(snapshot.items) or offset % limit:
                raise ValueError("cursor contains an invalid page position")
            return self._page(snapshot, key, offset, limit)
