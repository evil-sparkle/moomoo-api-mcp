"""Trade service for managing Moomoo trading context and account operations."""

import logging
import math
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from moomoo import (
    RET_OK,
    ComboLeg,
    OpenSecTradeContext,
    OrderStatus,
    SecurityFirm,
    TrdMarket,
)

from moomoo_mcp.services.clock import utc_now_iso
from moomoo_mcp.services.health import (
    SYNC_CONNECT_TIMEOUT_SECONDS,
    BoundedProbe,
    failure,
    run_detached,
)
from moomoo_mcp.services.instruments import InstrumentLookup
from moomoo_mcp.services.order_errors import (
    OrderNotSentError,
    OrderOutcomeUnknownError,
    OrderReceiptUnreadableError,
    not_sent,
    not_sent_message,
    outcome_unknown_message,
    receipt_unreadable_message,
)
from moomoo_mcp.services.sdk_response import as_frame
from moomoo_mcp.services.trading_policy import (
    ENV_REAL_ACC_IDS,
    InstrumentFacts,
    LegFacts,
    OrderFacts,
    TradingMode,
    TradingPolicy,
    TradingPolicyError,
)
from moomoo_mcp.services.validation import (
    validate_order_values,
    validate_required_order_fields,
)

logger = logging.getLogger(__name__)

# How long startup waits for the trade connection before carrying on
# without it. The worker keeps trying in the background.
CONNECT_TIMEOUT_SECONDS = 5.0


# Every attribute ComboLeg defines. Listed here rather than read off the
# instance so a future SDK field cannot silently widen the response, and so a
# leg the gateway sent without one still has the key.
COMBO_LEG_FIELDS = ("code", "trd_side", "qty_ratio", "position_id", "pred_side")


def _plain_combo_legs(records: list[dict]) -> list[dict]:
    """Convert the SDK's ComboLeg objects in ``records`` into plain dicts.

    order_list_query, history_order_list_query and place_combo_order all return
    a ``combo_legs`` column holding ComboLeg instances. They are ordinary Python
    objects with no JSON representation, so a single spread order made the whole
    MCP response unserializable: the tool failed outright instead of degrading
    one row, hiding every other order in the list.

    Converting here also exposes each leg's position_id to the identifier
    serialization the tool layer applies. That walk traverses dicts and lists
    and could not see inside an opaque object, so a 64-bit leg identifier was
    reaching double-parsing clients as a number.

    The records come straight from ``DataFrame.to_dict``, so they are already
    the caller's own copies and are updated in place.
    """
    for record in records:
        legs = record.get("combo_legs")
        if not isinstance(legs, (list, tuple)):
            continue
        record["combo_legs"] = [
            {field: getattr(leg, field, None) for field in COMBO_LEG_FIELDS}
            if isinstance(leg, ComboLeg)
            else leg
            for leg in legs
        ]
    return records


# What the SDK's decoders substitute for a field the gateway did not send.
# Confirmed against ComboOrderTradingInfoQuery.unpack_rsp, which writes this
# string — not a number and not NaN — for every absent impact field.
SDK_MISSING_SENTINEL = "N/A"


def _null_if_missing(value: Any) -> Any:
    """Normalize the SDK's missing-value markers to None.

    A field the gateway omitted reaches us as the string "N/A" from the SDK's
    decoder, or as NaN if it went through a pandas frame that widened a column.
    Both mean "not supplied".

    Reporting null says exactly that. Passing "N/A" through would put a string
    in a numeric field, and substituting 0.0 would claim the package has no
    effect on that measure — a different statement, and a dangerous one when the
    measure is a margin requirement.
    """
    if isinstance(value, str) and value.strip() == SDK_MISSING_SENTINEL:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


# Modification operations that add or restore exposure, and are therefore
# assessed against the limits and refused while the service is halted. The rest
# (CANCEL, DISABLE, DELETE) only reduce exposure and stay permitted.
EXPOSING_MODIFY_OPS = frozenset({"NORMAL", "ENABLE"})


@dataclass
class _ExecutionState:
    """Whether this service is willing to add exposure.

    Two states. ``ARMED`` is normal. ``HALTED`` means a relock after a write
    failed, so the gateway may still be unlocked and nobody has confirmed
    otherwise.

    The only way back is a successful ``lock_trade``. A just-in-time relock that
    succeeds later does not count: it undoes an unlock this server made a moment
    earlier and says nothing about why the earlier one failed. Nor does a lock
    after a reconnect, which happens without anyone seeing the halt. Requiring an
    explicit lock-only call makes recovery a visible, logged act.

    Guarded by its own lock, because it is read and written from tool threads and
    from the SDK's reconnect thread.
    """

    halted_since: str | None = None
    last_lock_error: str | None = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def halted(self) -> bool:
        with self._lock:
            return self.halted_since is not None

    def record_relock_failure(self, error: str) -> None:
        """A just-in-time relock failed: halt, keeping any original start time."""
        with self._lock:
            if self.halted_since is None:
                self.halted_since = utc_now_iso()
            self.last_lock_error = error

    def record_lock_result(self, error: str | None) -> bool:
        """Apply an explicit ``lock_trade`` outcome. Returns whether it cleared a halt.

        A failure keeps the halt and its original start time: a lock that the
        gateway refused must never look like a recovery.
        """
        with self._lock:
            if error is not None:
                self.last_lock_error = error
                return False
            cleared = self.halted_since is not None
            self.halted_since = None
            self.last_lock_error = None
            return cleared

    def snapshot(self) -> dict[str, Any]:
        """The state as health reports it. Reads memory; changes nothing."""
        with self._lock:
            return {
                "execution_halted": self.halted_since is not None,
                "halted_since": self.halted_since,
                "halt_error": self.last_lock_error,
            }


class TradeService:
    """Service to manage Moomoo Trade API connections and account operations."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11111,
        security_firm: str | None = None,
        policy: TradingPolicy | None = None,
        trade_password: str | None = None,
        trade_password_md5: str | None = None,
        instrument_lookup: InstrumentLookup | None = None,
    ):
        """Initialize TradeService.

        Args:
            host: Host address of OpenD gateway.
            port: Port number of OpenD gateway.
            security_firm: Securities firm identifier (e.g., 'FUTUSG' for Singapore,
                'FUTUSECURITIES' for HK). If None, no filter is applied.
            policy: Trading policy governing which order environments this
                service may write to. Defaults to read-only, so a service
                constructed without an explicit intent cannot send an order.
            trade_password: Stored plain-text trade credential, or None. Passed
                in rather than read from the environment here, so the whole
                configuration is validated once at startup and a test states
                what it is testing instead of patching os.environ.
            trade_password_md5: Stored hashed trade credential, or None.
            instrument_lookup: Resolves order codes to the facts the notional
                assessment needs. Called only when a notional cap is configured,
                so an unconfigured deployment pays no quote latency and takes on
                no quote dependency.
        """
        self.host = host
        self.port = port
        self.security_firm = security_firm
        self.policy = policy or TradingPolicy()
        self.trade_password = trade_password
        self.trade_password_md5 = trade_password_md5
        self.instrument_lookup = instrument_lookup
        self.trade_ctx: OpenSecTradeContext | None = None
        self._trade_probe = BoundedProbe("trade")
        self._connect_lock = threading.Lock()
        self._jit_lock = threading.RLock()
        self._connect_future: Future | None = None
        self._closed = False
        self._execution = _ExecutionState()

    @property
    def has_trade_credential(self) -> bool:
        """Whether a stored credential exists for the just-in-time unlock."""
        return bool(self.trade_password or self.trade_password_md5)

    @property
    def locks_gateway_at_rest(self) -> bool:
        """Whether this service asserts a lock on connect and on every reconnect.

        READ_ONLY locks because it never writes. REAL locks only when it holds a
        credential: without one it cannot unlock again, so locking the gateway
        would strand an operator who unlocked it by hand.
        """
        if self.policy.mode is TradingMode.READ_ONLY:
            return True
        return self.policy.mode is TradingMode.REAL and self.has_trade_credential

    @property
    def execution_state(self) -> dict[str, Any]:
        """The execution halt, as health reports it."""
        return self._execution.snapshot()

    def _convert_status_filter(
        self, status_filter_list: list[str] | None
    ) -> list[OrderStatus]:
        """Convert string status values to OrderStatus enum values.

        The Moomoo SDK expects OrderStatus enum values, not strings.
        This method converts user-provided string values to the proper enum format.

        Args:
            status_filter_list: List of status strings like ['SUBMITTED', 'FILLED_ALL'].

        Returns:
            List of OrderStatus enum values. Returns an empty list if input is None.

        Raises:
            ValueError: If an invalid status string is provided.
        """
        if status_filter_list is None:
            return []

        converted = []
        for status_str in status_filter_list:
            # OrderStatus has attribute matching string (e.g. OrderStatus.SUBMITTED)
            status_enum = getattr(OrderStatus, status_str.upper(), None)
            if status_enum is None:
                valid_statuses = [
                    "UNSUBMITTED",
                    "WAITING_SUBMIT",
                    "SUBMITTING",
                    "SUBMIT_FAILED",
                    "SUBMITTED",
                    "FILLED_PART",
                    "FILLED_ALL",
                    "CANCELLING_PART",
                    "CANCELLING_ALL",
                    "CANCELLED_PART",
                    "CANCELLED_ALL",
                    "REJECTED",
                    "DISABLED",
                    "DELETED",
                    "FAILED",
                    "NONE",
                ]
                raise ValueError(
                    f"Invalid order status: '{status_str}'. "
                    f"Valid options: {valid_statuses}"
                )
            converted.append(status_enum)
        return converted

    def _get_market_from_code(self, code: str | None) -> str | None:
        """Extract market from stock code (e.g., 'JP' from 'JP.8058').

        The SDK types ComboLeg.code as optional, so callers reading a leg's
        code can arrive here with None. Treat that like a code carrying no
        market prefix instead of raising on the membership test.
        """
        if code and "." in code:
            return code.split(".")[0].upper()
        return None

    @staticmethod
    def _mask_acc_id(acc_id: Any) -> str:
        """An account identifier reduced to its last four digits.

        Refusal messages have to be specific enough to act on and are read by an
        agent that may log them. Four digits is what a person needs to tell two
        of their own accounts apart.
        """
        text = str(acc_id)
        return f"...{text[-4:]}" if len(text) > 4 else text

    def _resolve_account(
        self, trd_env: str, market: str | None, acc_id: int | str
    ) -> int:
        """Decide which account a mutation targets, or refuse to guess.

        The old behaviour was to take the first account in the environment whose
        market authorization covered the code. That is a silent choice between
        accounts that may hold very different amounts of money, and with
        ``acc_id="0"`` it was also the default. Here ``"0"`` resolves only when
        exactly one account is eligible; anything else is refused and names the
        candidates.

        Args:
            trd_env: 'REAL' or 'SIMULATE'.
            market: The market the order touches, or None for modify and cancel,
                which name an order rather than an instrument.
            acc_id: The requested account, or 0 to resolve one.

        Returns:
            The account identifier to submit against.

        Raises:
            ValueError: If an explicit REAL account is not allowlisted, or if
                zero or several accounts are eligible.
        """
        requested_env = str(trd_env).strip().upper()
        resolved = int(acc_id) if isinstance(acc_id, str) else int(acc_id)

        if resolved != 0:
            # An explicit account needs no gateway call to check: the allowlist
            # is configuration, and a REAL write to an unlisted account is
            # refused whether or not that account exists.
            if self._allowlist_applies(requested_env):
                self._check_real_allowlist(resolved)
            return resolved

        accounts = self._eligible_accounts(requested_env, market)
        if len(accounts) == 1:
            return int(accounts[0]["acc_id"])

        where = f" authorized for {market}" if market else ""
        if not accounts:
            raise ValueError(
                f"No {requested_env} account{where} is eligible for this "
                "request. Check get_accounts"
                + (
                    f", and that the account is listed in {ENV_REAL_ACC_IDS}."
                    if requested_env == "REAL"
                    else "."
                )
            )
        masked = ", ".join(self._mask_acc_id(account["acc_id"]) for account in accounts)
        raise ValueError(
            f"{len(accounts)} {requested_env} accounts{where} are eligible "
            f"({masked}), so acc_id='0' does not identify one. Name the account "
            "explicitly with acc_id."
        )

    def _allowlist_applies(self, trd_env: str) -> bool:
        """Whether the REAL account allowlist governs this request.

        Only in REAL mode, and only for REAL accounts. Outside REAL mode the
        allowlist is ignored rather than empty-means-deny: a READ_ONLY
        deployment configures no allowlist and still needs to resolve a REAL
        account for reads and previews, and it cannot write in any case — the
        policy refuses that before this runs.
        """
        return trd_env == "REAL" and self.policy.mode is TradingMode.REAL

    def _check_real_allowlist(self, acc_id: int) -> None:
        """Refuse a REAL account this deployment was not configured to trade.

        Raises:
            ValueError: If the account is not on the allowlist.
        """
        allowed = self.policy.real_acc_ids
        if not allowed:
            raise ValueError(
                f"No REAL accounts are configured. Set {ENV_REAL_ACC_IDS} to the "
                "accounts REAL writes may target."
            )
        if acc_id not in allowed:
            raise ValueError(
                f"REAL account {self._mask_acc_id(acc_id)} is not listed in "
                f"{ENV_REAL_ACC_IDS}, so this server may not trade it."
            )

    def _eligible_accounts(
        self, trd_env: str, market: str | None
    ) -> list[dict[str, Any]]:
        """The accounts a mutation in this environment could target.

        Eligibility is environment, then market authorization when a market is
        known, then the allowlist in REAL. Modify and cancel pass no market:
        they name an order, and the order already knows its instrument.
        """
        try:
            accounts = self.get_accounts()
        except Exception as exc:
            raise ValueError(
                f"Could not retrieve the account list to resolve an account: {exc}"
            ) from exc

        eligible = [
            account for account in accounts if account.get("trd_env") == trd_env
        ]
        if market:
            target = market.strip().upper()
            eligible = [
                account
                for account in eligible
                if target
                in (account.get("market_auth") or account.get("trdmarket_auth") or [])
            ]
        if self._allowlist_applies(trd_env):
            allowed = self.policy.real_acc_ids
            eligible = [
                account
                for account in eligible
                if int(account.get("acc_id", 0)) in allowed
            ]
        return eligible

    def _open_trade_context(self) -> None:
        """Construct the SDK trade context and publish it when it is ready."""
        kwargs = {"host": self.host, "port": self.port}

        # Add security_firm if specified
        if self.security_firm:
            # Convert string to SecurityFirm enum
            firm_enum = getattr(SecurityFirm, self.security_firm, None)
            if firm_enum:
                kwargs["security_firm"] = firm_enum

        trade_ctx = OpenSecTradeContext(**kwargs)
        trade_ctx.set_sync_query_connect_timeout(SYNC_CONNECT_TIMEOUT_SECONDS)
        self._watch_reconnects(trade_ctx)

        with self._connect_lock:
            if self._closed:
                # close() ran while this was still retrying. Publishing the
                # context now would leak a live connection past shutdown.
                should_close = True
            else:
                self.trade_ctx = trade_ctx
                should_close = False
        if should_close:
            trade_ctx.close()
            return

        self._enforce_gateway_lock(trade_ctx, "connecting")

    def _watch_reconnects(self, trade_ctx: OpenSecTradeContext) -> None:
        """Re-assert this server's gateway lock after every SDK reconnect.

        The SDK reconnects on its own — six seconds after the socket drops, for
        as long as it takes — and reuses the same context object, so nothing
        above this layer ever observes that it happened. The lock a READ_ONLY
        deployment asserts when it first connects is therefore silently lost the
        moment OpenD restarts or the link blips, leaving this server talking to
        a gateway whose unlock state it no longer knows.

        ``on_api_socket_reconnected`` is the SDK's own post-reconnect hook: it is
        where the trade context replays a cached unlock and the quote context
        replays its subscriptions. Wrapping it on the instance rather than
        subclassing keeps this independent of how the context was constructed,
        which is what lets the tests substitute one.
        """
        reconnected = trade_ctx.on_api_socket_reconnected

        def on_api_socket_reconnected():
            result = reconnected()
            self._enforce_gateway_lock(trade_ctx, "reconnecting")
            return result

        trade_ctx.on_api_socket_reconnected = on_api_socket_reconnected

    def _enforce_gateway_lock(self, trade_ctx: OpenSecTradeContext, when: str) -> None:
        """Lock the gateway at rest, without ever raising.

        Two deployments lock at rest: READ_ONLY, which never writes, and REAL
        with a stored credential, which unlocks only for the instant a write is
        dispatched. REAL without a credential does not, because it could not
        unlock again and would strand an operator who unlocked by hand.

        Called on the SDK's own connect and reconnect threads, so a failure here
        must not propagate: an exception would abort the SDK's post-reconnect
        work and be reported as a failed reconnect, costing the connection that
        did succeed. A gateway that refuses the lock is logged and left alone —
        policy still rejects every write before it reaches the gateway, so the
        lock is defence in depth, not the thing standing between this server and
        an order.

        A lock at rest never changes the execution state, in either direction. A
        reconnect lock happens without anyone seeing a halt, so letting it clear
        one would hide exactly the condition the halt exists to surface.
        """
        if not self.locks_gateway_at_rest:
            return

        # A write in flight holds this lock and has the gateway deliberately
        # unlocked. Locking underneath it would make the write fail on a locked
        # gateway; its own relock covers the window instead, and the SDK has
        # already replayed the unlock the write needs.
        if not self._jit_lock.acquire(blocking=False):
            logger.info(
                f"Skipped the trade gateway lock after {when}: a write holds the "
                "just-in-time lock and will re-lock when it finishes."
            )
            return

        try:
            self._lock_gateway(trade_ctx)
        except Exception as exc:  # noqa: BLE001 - runs on an SDK-owned thread
            logger.warning(f"Failed to lock the trade gateway after {when}: {exc}")
        else:
            logger.info(
                f"Locked trade gateway after {when} ({self.policy.mode.value} mode)."
            )
        finally:
            self._jit_lock.release()

    def connect(self, timeout: float | None = None) -> None:
        """Start the trade connection, waiting at most ``timeout`` seconds.

        OpenSecTradeContext offers no async-connect option and its constructor
        does not raise when OpenD is unreachable — it retries every six seconds
        forever. Calling it inline would hang the MCP lifespan before it yields,
        taking check_health down with the gateway it exists to diagnose.

        So it runs on a single background worker. If the gateway is down this
        returns once the timeout elapses, health reports the trade service as
        unavailable, and the worker publishes the context if OpenD later
        appears. Only one such worker ever exists: repeated calls join the one
        in flight rather than stacking up connection attempts.

        Args:
            timeout: Seconds to wait for the connection before returning.
                Defaults to ``CONNECT_TIMEOUT_SECONDS``.
        """
        if timeout is None:
            timeout = CONNECT_TIMEOUT_SECONDS

        with self._connect_lock:
            self._closed = False
            future = self._connect_future
            if future is None or future.done():
                future = run_detached(self._open_trade_context, "trade-connect")
                self._connect_future = future

        try:
            future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning(
                f"Trade connection to {self.host}:{self.port} is still being "
                f"established after {timeout:.0f}s. The server remains available; "
                "check_health reports the trade service until it connects."
            )
        except Exception as exc:  # noqa: BLE001 - startup must stay available
            logger.error(f"Trade connection failed: {exc}")

    def close(self) -> None:
        """Close the trade context and abandon any connection still in flight.

        A connection attempt cannot be interrupted: the SDK constructor owns its
        thread until OpenD answers. Shutdown therefore does not wait for it. It
        marks the service closed — so a worker that eventually succeeds closes
        the context it built instead of publishing it — and drops the future.
        The worker is a daemon thread, so the interpreter can exit while the
        constructor is still retrying.
        """
        self._trade_probe.close()
        with self._connect_lock:
            # Set before releasing the lock so a worker that is still retrying
            # closes whatever it eventually builds instead of publishing it.
            self._closed = True
            self._connect_future = None
            trade_ctx = self.trade_ctx
            self.trade_ctx = None
        if trade_ctx:
            trade_ctx.close()

    def probe_trade(self) -> dict[str, Any]:
        """Actively check trade connectivity with a read-only account listing.

        ``get_acc_list`` is the lightest trade read that proves the trade socket
        is answering; it neither unlocks trading nor mutates anything. Only the
        return code and the number of visible accounts are reported — never
        account identifiers, balances, or positions.
        """
        trade_ctx = self.trade_ctx
        if trade_ctx is None:
            return failure(
                "unavailable", "Trade context not initialized", reason="not_initialized"
            )

        ret, data = trade_ctx.get_acc_list()
        if ret != RET_OK:
            return failure("error", data)

        try:
            account_count = len(data)
        except TypeError:
            account_count = 0
        return {"status": "ok", "account_count": account_count}

    def submit_probe(self) -> Future:
        """Start (or join) the bounded trade connectivity probe."""
        return self._trade_probe.submit(self.probe_trade)

    def collect_probe(self, future: Future, timeout: float) -> dict[str, Any]:
        """Collect a trade probe result within the remaining health deadline."""
        return self._trade_probe.collect(future, timeout)

    def get_accounts(self) -> list[dict]:
        """Get list of trading accounts.

        Returns:
            List of account dictionaries with acc_id, trd_env, etc.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_acc_list()
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_list failed: {data}")

        return as_frame("get_acc_list", data).to_dict("records")

    def get_assets(
        self,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
        currency: str | None = None,
    ) -> dict:
        """Get account assets (cash, market value, etc.).

        Args:
            trd_env: Trading environment, 'REAL' or 'SIMULATE'.
            acc_id: Account ID. Must be obtained from get_accounts().
            refresh_cache: Whether to refresh the cache.
            currency: Filter by currency (e.g., 'HKD', 'USD'). Leave None for default.

        Returns:
            Dictionary with asset information.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        kwargs = {
            "trd_env": trd_env,
            "acc_id": acc_id,
            "refresh_cache": refresh_cache,
        }
        if currency is not None:
            normalized_currency = currency.strip().upper()
            if normalized_currency:
                kwargs["currency"] = normalized_currency

        ret, data = self.trade_ctx.accinfo_query(**kwargs)
        if ret != RET_OK:
            raise RuntimeError(f"accinfo_query failed: {data}")

        records = as_frame("accinfo_query", data).to_dict("records")
        return records[0] if records else {}

    def get_positions(
        self,
        code: str = "",
        market: str = "",
        pl_ratio_min: float | None = None,
        pl_ratio_max: float | None = None,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
        show_option_strategy_view: bool = False,
    ) -> list[dict]:
        """Get current positions.

        Args:
            code: Filter by stock code.
            market: Filter by market (e.g., 'US', 'HK', 'CN', 'SG', 'JP').
            pl_ratio_min: Minimum profit/loss ratio filter.
            pl_ratio_max: Maximum profit/loss ratio filter.
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().
            refresh_cache: Whether to refresh cache.
            show_option_strategy_view: Group multi-leg option positions into
                strategies. Each strategy is returned as a 'COMBINED' row
                alongside its 'LEG' rows, and every row carries the
                'position_id' that place_combo_order requires when closing.

        Returns:
            List of position dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Map market string to TrdMarket enum
        # Note: CN is for A-share simulation only; HKCC for Stock Connect (live only)
        market_map = {
            "US": TrdMarket.US,
            "HK": TrdMarket.HK,
            "CN": TrdMarket.CN,
            "HKCC": TrdMarket.HKCC,
            "SG": TrdMarket.SG,
            "JP": TrdMarket.JP,
        }
        position_market = TrdMarket.NONE
        if market:
            try:
                # Try direct map first
                position_market = market_map.get(market.upper())
                if position_market is None:
                    # Try to use getattr for other potential values
                    position_market = getattr(TrdMarket, market.upper())
            except AttributeError:
                position_market = TrdMarket.NONE

        ret, data = self.trade_ctx.position_list_query(
            code=code,
            position_market=position_market,
            pl_ratio_min=pl_ratio_min,
            pl_ratio_max=pl_ratio_max,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
            show_option_strategy_view=show_option_strategy_view,
        )
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query failed: {data}")

        return as_frame("position_list_query", data).to_dict("records")

    def get_max_tradable(
        self,
        order_type: str,
        code: str,
        price: float,
        order_id: str = "",
        adjust_limit: float = 0,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Get maximum tradable quantity for a stock.

        Args:
            order_type: Order type string (e.g., 'NORMAL').
            code: Stock code.
            price: Target price.
            order_id: Optional order ID for modification.
            adjust_limit: Adjust limit percentage.
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().

        Returns:
            Dictionary with max quantities for buy/sell.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.acctradinginfo_query(
            order_type=order_type,
            code=code,
            price=price,
            order_id=order_id if order_id else None,
            # The SDK defaults adjust_limit to 0, so it infers as int; the
            # gateway takes a price-adjustment ratio, which is a float.
            adjust_limit=adjust_limit,  # pyright: ignore[reportArgumentType]
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"acctradinginfo_query failed: {data}")

        records = as_frame("acctradinginfo_query", data).to_dict("records")
        return records[0] if records else {}

    def get_margin_ratio(self, code_list: list[str]) -> list[dict]:
        """Get margin ratio for stocks.

        Args:
            code_list: List of stock codes.

        Returns:
            List of margin ratio dictionaries.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_margin_ratio(code_list=code_list)
        if ret != RET_OK:
            raise RuntimeError(f"get_margin_ratio failed: {data}")

        return as_frame("get_margin_ratio", data).to_dict("records")

    def get_cash_flow(
        self,
        clearing_date: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get account cash flow history.

        Args:
            clearing_date: Filter by clearing date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID. Must be obtained from get_accounts().

        Returns:
            List of cash flow record dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.get_acc_cash_flow(
            clearing_date=clearing_date,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"get_acc_cash_flow failed: {data}")

        return as_frame("get_acc_cash_flow", data).to_dict("records")

    def unlock_trade(
        self, password: str | None = None, password_md5: str | None = None
    ) -> None:
        """Unlock the gateway on behalf of a caller, or refuse.

        This is the manual path, for deployments that store no credential. When
        one is stored the server owns the lock: it keeps the gateway locked at
        rest and unlocks only for the moment a write is dispatched. A manual
        unlock would leave it unlocked indefinitely and quietly undo that, so it
        is refused.

        Args:
            password: Plain text trade password.
            password_md5: MD5 hash of trade password (alternative to password).

        Raises:
            TradingPolicyError: If the mode does not permit unlocking, or a
                stored credential makes manual unlocking the wrong tool.
            ValueError: If neither a password nor a hash was supplied.
            RuntimeError: If not connected, or the gateway refuses the unlock.
        """
        # Checked before the connection check so a denied unlock never reaches
        # the gateway, whatever the connection state.
        self.policy.check_unlock()

        if self.has_trade_credential:
            raise TradingPolicyError(
                "unlock_trade is not permitted: this server holds a stored trade "
                "credential and manages the gateway lock itself. REAL writes "
                "unlock just in time for a single order and re-lock immediately "
                "afterwards, so the gateway is never left unlocked. Unset "
                "MOOMOO_TRADE_PASSWORD and MOOMOO_TRADE_PASSWORD_MD5 if you want "
                "to manage the lock by hand instead."
            )

        if not password and not password_md5:
            raise ValueError(
                "unlock_trade requires a password or password_md5. This server "
                "stores no trade credential, so there is nothing to fall back to."
            )

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        self._unlock_gateway(self.trade_ctx, password, password_md5)

    def lock_trade(self) -> dict[str, Any]:
        """Lock the gateway, and clear an execution halt if the lock succeeds.

        This is a lock-only request: it never unlocks. It is also the only route
        out of ``HALTED``, which is why it is serialized with writes. Taking the
        just-in-time lock in blocking mode means it cannot lock the gateway
        inside another write's unlock window, and cannot report a halt as
        cleared while a cancellation's relock is still outstanding.

        Returns:
            ``status``, whether the halt is still in effect afterwards, and
            whether this call cleared one.

        Raises:
            RuntimeError: If not connected, or the gateway refuses the lock. A
                refused lock leaves any halt exactly as it was: it must never
                look like a recovery.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        with self._jit_lock:
            try:
                self._lock_gateway(self.trade_ctx)
            except Exception as exc:
                self._execution.record_lock_result(str(exc))
                raise
            cleared = self._execution.record_lock_result(None)

        return {
            "status": "locked",
            "execution_halted": False,
            "halt_cleared": cleared,
        }

    def _unlock_gateway(
        self,
        trade_ctx: OpenSecTradeContext,
        password: str | None,
        password_md5: str | None,
    ) -> None:
        """Issue one unlock request.

        The gateway answering that no unlock is required counts as success: it
        returns RET_OK with an explanatory message, and treating that as a
        failure would refuse every write on a gateway that was already unlocked.
        Nothing here asserts that the gateway is unlocked afterwards — only that
        the request was not refused.

        Raises:
            RuntimeError: If the gateway refuses the unlock.
        """
        ret, data = trade_ctx.unlock_trade(
            password=password,
            password_md5=password_md5,
            is_unlock=True,
        )
        if ret != RET_OK:
            raise RuntimeError(f"unlock_trade failed: {data}")

    def _lock_gateway(self, trade_ctx: OpenSecTradeContext) -> None:
        """Lock a specific context, which may not be the published one yet.

        The connect worker locks the context it has just built, before it is
        reachable through ``self.trade_ctx``.
        """
        ret, data = trade_ctx.unlock_trade(is_unlock=False)
        if ret != RET_OK:
            raise RuntimeError(f"lock_trade failed: {data}")

    def _uses_jit_unlock(self, trd_env: str) -> bool:
        """Whether a write in this environment unlocks and relocks around itself."""
        if str(trd_env).strip().upper() != "REAL":
            return False
        return self.has_trade_credential

    def _check_execution_halt(self, operation: str) -> None:
        """Refuse an exposure-adding write while the service is halted.

        Raises:
            TradingPolicyError: If the execution state is HALTED.
        """
        if not self._execution.halted:
            return
        state = self._execution.snapshot()
        raise TradingPolicyError(
            f"{operation} is refused: order execution is halted. A relock after "
            f"an earlier write failed at {state['halted_since']} "
            f"({state['halt_error']}), so the gateway may still be unlocked. "
            "Call lock_trade to lock the gateway and clear the halt; it is the "
            "only way to clear it. Cancellations remain permitted while halted."
        )

    def _dispatch_write(
        self,
        trd_env: str,
        operation: str,
        call: Callable[[], tuple[Any, Any]],
        convert: Callable[[Any], dict],
    ) -> tuple[dict, str | None]:
        """Run one order-mutating gateway call, and classify what came back.

        This is the dispatch boundary. Everything before ``call()`` is a refusal;
        everything from ``call()`` onwards is one of the two post-boundary
        outcomes. The three are kept apart because they call for different
        actions from whoever gets the error: fix and retry, go and look, or
        never resend.

        Args:
            trd_env: The environment the write targets.
            operation: Name of the operation, used in every message.
            call: Issues the SDK write and returns its ``(ret, data)``.
            convert: Turns a successful payload into the receipt.

        Returns:
            The receipt, and the relock error if the relock failed.

        Raises:
            OrderNotSentError: If the just-in-time unlock failed. No write was
                attempted.
            OrderOutcomeUnknownError: If the call started and nothing
                acknowledged it.
            OrderReceiptUnreadableError: If the gateway acknowledged the request
                and its response could not be read.
        """
        uses_jit = self._uses_jit_unlock(trd_env)

        with self._jit_lock:
            if uses_jit:
                try:
                    assert self.trade_ctx is not None
                    self._unlock_gateway(
                        self.trade_ctx, self.trade_password, self.trade_password_md5
                    )
                except Exception as exc:
                    raise OrderNotSentError(
                        not_sent_message(
                            operation,
                            f"The gateway refused the just-in-time unlock: {exc}",
                        )
                    ) from exc

            relock_error: str | None = None
            try:
                try:
                    ret, data = call()
                except Exception as exc:
                    raise OrderOutcomeUnknownError(
                        outcome_unknown_message(
                            operation, f"{type(exc).__name__}: {exc}"
                        )
                    ) from exc

                if ret != RET_OK:
                    # A non-OK return is not read as a rejection. The gateway's
                    # error text cannot reliably tell a broker refusal from a
                    # timeout or a dropped transport, and guessing "rejected"
                    # would tell a caller nothing was sent when something may
                    # have been.
                    raise OrderOutcomeUnknownError(
                        outcome_unknown_message(operation, str(data))
                    )

                try:
                    receipt = convert(data)
                except Exception as exc:
                    raise OrderReceiptUnreadableError(
                        receipt_unreadable_message(
                            operation, f"{type(exc).__name__}: {exc}"
                        )
                    ) from exc
            except (
                OrderOutcomeUnknownError,
                OrderReceiptUnreadableError,
            ) as exc:
                relock_error = self._relock_after_write(uses_jit)
                if relock_error is not None:
                    raise type(exc)(
                        f"{exc} The gateway was also left unlocked: {relock_error}. "
                        "Order execution is halted until lock_trade succeeds."
                    ) from exc
                raise
            else:
                relock_error = self._relock_after_write(uses_jit)

        return receipt, relock_error

    def _relock_after_write(self, uses_jit: bool) -> str | None:
        """Re-lock after a write, recording a failure as a halt.

        Returns the relock error, or None. Never raises: the write's own outcome
        has already been decided, and losing an acknowledged receipt to a lock
        failure would be strictly worse than reporting both.
        """
        if not uses_jit:
            return None
        try:
            assert self.trade_ctx is not None
            self._lock_gateway(self.trade_ctx)
        except Exception as exc:  # noqa: BLE001 - the failure is the result
            error = str(exc)
            self._execution.record_relock_failure(error)
            logger.error(
                f"Failed to re-lock the trade gateway after a write: {error}. "
                "Order execution is halted until lock_trade succeeds."
            )
            return error
        return None

    def _instrument_facts(
        self, operation: str, codes: Sequence[str]
    ) -> list[InstrumentFacts]:
        """Look up the facts the notional assessment needs.

        Raises:
            ValueError: If no lookup is wired, which with a cap configured means
                the order cannot be valued and must be refused.
        """
        if self.instrument_lookup is None:
            raise ValueError(
                f"{operation} cannot be valued against the configured notional "
                "cap: this service has no instrument lookup wired, so the "
                "instrument's price and classification are unavailable."
            )
        return list(self.instrument_lookup(codes))

    def _single_leg_facts(
        self,
        operation: str,
        *,
        code: str,
        order_type: str,
        trd_side: str,
        qty: int,
        price: float | None,
        aux_price: float | None,
    ) -> OrderFacts:
        """Build the assessment input for a single-leg order."""
        legs: list[LegFacts] = []
        if self.policy.notional_cap_configured:
            facts = self._instrument_facts(operation, [code])
            legs = [LegFacts(instrument=facts[0])]
        else:
            legs = [LegFacts(instrument=InstrumentFacts(code=code))]
        return OrderFacts(
            order_type=order_type,
            trd_side=trd_side,
            qty=qty,
            legs=legs,
            price=price,
            aux_price=aux_price,
        )

    def place_order(
        self,
        code: str,
        price: float,
        qty: int,
        trd_side: str,
        order_type: str = "NORMAL",
        time_in_force: str = "DAY",
        adjust_limit: float = 0,
        aux_price: float | None = None,
        trail_type: str | None = None,
        trail_value: float | None = None,
        trail_spread: float | None = None,
        *,
        trd_env: str,
        acc_id: int | str = "0",
        remark: str = "",
    ) -> dict:
        """Place a new trading order.

        Every refusal happens before the single gateway write, in this order:
        policy, order values, account, halt, limits. What comes back is one of
        three outcomes — see :mod:`moomoo_mcp.services.order_errors`.

        Args:
            code: Stock code (e.g., 'US.AAPL').
            price: Order price.
            qty: Order quantity.
            trd_side: Trade side ('BUY' or 'SELL').
            order_type: Order type ('NORMAL', 'MARKET', etc.).
            time_in_force: Time in force ('DAY' or 'GTC'). Defaults to 'DAY'.
            adjust_limit: Adjust limit percentage.
            aux_price: Trigger price for stop/if-touched order types.
            trail_type: Trailing type ('RATIO' or 'AMOUNT') for trailing stop types.
            trail_value: Trailing value (ratio or amount) for trailing stop types.
            trail_spread: Optional trailing spread for trailing stop limit types.
            trd_env: Trading environment ('REAL' or 'SIMULATE'). Required: an
                order never infers which environment it belongs to.
            acc_id: Account ID, or '0' to resolve one when exactly one is
                eligible.
            remark: Order remark/note.

        Returns:
            The gateway's receipt, plus the resolved ``acc_id`` and ``trd_env``.

        Raises:
            OrderNotSentError: If anything refused the order before dispatch.
            OrderOutcomeUnknownError: If the call started and nothing
                acknowledged it.
            OrderReceiptUnreadableError: If the gateway acknowledged the request
                and its receipt could not be read.
        """
        operation = "place_order"
        with not_sent(operation):
            self.policy.check_write(operation, trd_env)
            validate_order_values(
                operation,
                order_type=order_type,
                qty=qty,
                price=price,
                aux_price=aux_price,
                trail_value=trail_value,
                trail_spread=trail_spread,
            )
            validate_required_order_fields(
                operation,
                order_type=order_type,
                aux_price=aux_price,
                trail_type=trail_type,
                trail_value=trail_value,
            )

            if not self.trade_ctx:
                raise RuntimeError("Trade context not connected")

            resolved_acc_id = self._resolve_account(
                trd_env, self._get_market_from_code(code), acc_id
            )
            self._check_execution_halt(operation)
            self.policy.assess_order(
                operation,
                self._single_leg_facts(
                    operation,
                    code=code,
                    order_type=order_type,
                    trd_side=trd_side,
                    qty=qty,
                    price=price,
                    aux_price=aux_price,
                ),
            )

        trade_ctx = self.trade_ctx
        assert trade_ctx is not None

        receipt, relock_error = self._dispatch_write(
            trd_env,
            operation,
            lambda: trade_ctx.place_order(
                price=price,
                qty=qty,
                code=code,
                trd_side=trd_side,
                order_type=order_type,
                time_in_force=time_in_force,
                # The SDK defaults adjust_limit to 0, so it infers as int; the
                # gateway takes a price-adjustment ratio, which is a float.
                adjust_limit=adjust_limit,  # pyright: ignore[reportArgumentType]
                aux_price=aux_price,
                trail_type=trail_type,
                trail_value=trail_value,
                trail_spread=trail_spread,
                trd_env=trd_env,
                acc_id=resolved_acc_id,
                remark=remark,
            ),
            lambda data: self._first_record(operation, data),
        )
        return self._with_routing(receipt, resolved_acc_id, trd_env, relock_error)

    @staticmethod
    def _first_record(operation: str, data: Any) -> dict:
        records = as_frame(operation, data).to_dict("records")
        return dict(records[0]) if records else {}

    def _with_routing(
        self,
        receipt: dict,
        acc_id: int,
        trd_env: str,
        relock_error: str | None,
    ) -> dict:
        """Attach the routing the write used, and any relock failure.

        The receipt is returned even when the relock failed. The order exists;
        losing its identifier because the lock afterwards did not take would be
        a strictly worse outcome than reporting both facts.
        """
        result = dict(receipt)
        result["acc_id"] = acc_id
        result["trd_env"] = trd_env
        if relock_error is not None:
            result["gateway_relock_error"] = relock_error
            result["execution_halted"] = True
        return result

    def _build_combo_legs(self, combo_legs: list[dict]) -> list[ComboLeg]:
        """Validate leg dictionaries and convert them to SDK ComboLeg objects.

        Validation happens before any gateway call so that a malformed strategy
        fails fast with an actionable message rather than an opaque protocol error.

        Args:
            combo_legs: Leg dictionaries, each with 'code', 'trd_side', 'qty_ratio'.

        Returns:
            List of ComboLeg objects ready for the SDK.

        Raises:
            ValueError: If the leg list is malformed.
        """
        if len(combo_legs) < 2:
            raise ValueError(
                "A combo order requires at least two legs. "
                "Use place_order for single-leg orders."
            )

        valid_sides = {"BUY", "SELL"}
        legs: list[ComboLeg] = []
        markets: set[str] = set()

        for index, leg in enumerate(combo_legs):
            code = str(leg.get("code") or "").strip()
            if not code:
                raise ValueError(f"Leg {index} is missing a non-empty 'code'.")

            trd_side = str(leg.get("trd_side") or "").strip().upper()
            if trd_side not in valid_sides:
                raise ValueError(
                    f"Invalid trd_side '{leg.get('trd_side')}' on leg {index}. "
                    f"Valid options: {sorted(valid_sides)}"
                )

            # qty_ratio is a multiplier, not a convenience default: the actual
            # quantity of a leg is (order qty x qty_ratio). Assuming 1 for an
            # omitted ratio would silently submit a different strategy — a 1:2:1
            # butterfly would become 1:1:1 — so an absent ratio is an error.
            if "qty_ratio" not in leg:
                raise ValueError(
                    f"Leg {index} is missing 'qty_ratio'. It multiplies the order "
                    "quantity for this leg, so it must be stated explicitly."
                )
            qty_ratio = leg["qty_ratio"]
            if not isinstance(qty_ratio, int) or isinstance(qty_ratio, bool):
                raise ValueError(
                    f"Leg {index} has a non-integer 'qty_ratio': {qty_ratio!r}"
                )
            if qty_ratio <= 0:
                raise ValueError(
                    f"Leg {index} has a 'qty_ratio' of {qty_ratio}; it must be a "
                    "positive integer."
                )

            market = self._get_market_from_code(code)
            if market:
                markets.add(market)

            # ComboLeg.__init__ assigns each field a bare None without an
            # annotation, so the SDK declares every attribute's type as None
            # and rejects the values the gateway actually requires. The
            # ignores below cover that defect, not a problem with these values.
            combo_leg = ComboLeg()
            combo_leg.code = code  # pyright: ignore[reportAttributeAccessIssue]
            combo_leg.trd_side = trd_side  # pyright: ignore[reportAttributeAccessIssue]
            combo_leg.qty_ratio = qty_ratio  # pyright: ignore[reportAttributeAccessIssue]

            # Required by the gateway when the order closes an existing position.
            # Obtained from get_positions(show_option_strategy_view=True).
            # Accept an exact integer, or a decimal string. Strings matter because
            # these identifiers exceed the range JSON consumers can represent
            # exactly. Anything lossy is refused rather than coerced: int(True) is
            # 1 and int(123.75) is 123, and silently trading on either would target
            # the wrong position.
            position_id = leg.get("position_id")
            if position_id is not None:
                if isinstance(position_id, bool):
                    raise ValueError(
                        f"Leg {index} has a boolean 'position_id': {position_id!r}"
                    )
                if isinstance(position_id, int):
                    combo_leg.position_id = position_id  # pyright: ignore[reportAttributeAccessIssue]
                elif isinstance(position_id, str) and position_id.strip().isdigit():
                    combo_leg.position_id = int(position_id.strip())  # pyright: ignore[reportAttributeAccessIssue]
                else:
                    raise ValueError(
                        f"Leg {index} has a non-integer 'position_id': "
                        f"{position_id!r}. Provide an integer or a decimal string."
                    )

            legs.append(combo_leg)

        if len(markets) > 1:
            raise ValueError(
                f"All combo legs must belong to the same market, got: {sorted(markets)}"
            )

        return legs

    def _combo_facts(
        self,
        operation: str,
        legs: list[ComboLeg],
        price: float,
        qty: int,
        order_type: str,
    ) -> OrderFacts:
        """Build the assessment input for a combo package."""
        codes = [str(leg.code) for leg in legs]
        leg_facts: list[LegFacts] = []
        if self.policy.notional_cap_configured:
            facts = {
                instrument.code: instrument
                for instrument in self._instrument_facts(operation, codes)
            }
            for leg in legs:
                instrument = facts.get(str(leg.code))
                if instrument is None:
                    raise ValueError(
                        f"{operation}: no instrument facts for leg {leg.code}."
                    )
                leg_facts.append(
                    LegFacts(instrument=instrument, qty_ratio=int(leg.qty_ratio or 1))
                )
        else:
            leg_facts = [
                LegFacts(
                    instrument=InstrumentFacts(code=str(leg.code)),
                    qty_ratio=int(leg.qty_ratio or 1),
                )
                for leg in legs
            ]
        return OrderFacts(
            order_type=order_type,
            # A package has no single side; the legs carry their own. The
            # combo rule never consults it.
            trd_side="",
            qty=qty,
            legs=leg_facts,
            price=price,
            is_combo=True,
        )

    def place_combo_order(
        self,
        combo_legs: list[dict],
        price: float,
        qty: int,
        order_type: str = "NORMAL",
        time_in_force: str = "DAY",
        *,
        trd_env: str,
        acc_id: int | str = "0",
        remark: str = "",
    ) -> dict:
        """Place a multi-leg option strategy as a single atomic order.

        The package fills as one unit or not at all, so a strategy can never be
        left half-executed the way independent single-leg orders can.

        The notional guardrail measures the package *premium*, not maximum loss:
        a short package can lose far more than the premium it collects.

        Args:
            combo_legs: Legs of the strategy. Each is a dict with 'code',
                'trd_side' ('BUY' or 'SELL'), and 'qty_ratio' (positive int,
                required — it multiplies the order qty for that leg). May also
                carry 'position_id', which the gateway requires when the order
                closes an existing position; obtain it from
                get_positions(show_option_strategy_view=True).
            price: Net price of the whole package, not a per-leg price.
            qty: Number of packages to trade.
            order_type: Order type ('NORMAL' for limit, 'MARKET', etc.).
            time_in_force: Time in force ('DAY' or 'GTC'). Defaults to 'DAY'.
            trd_env: Trading environment ('REAL' or 'SIMULATE'). Required.
            acc_id: Account ID, or '0' to resolve one when exactly one is
                eligible.
            remark: Order remark/note.

        Returns:
            The gateway's receipt, plus the resolved ``acc_id`` and ``trd_env``.

        Raises:
            OrderNotSentError: If anything refused the order before dispatch.
            OrderOutcomeUnknownError: If the call started and nothing
                acknowledged it.
            OrderReceiptUnreadableError: If the gateway acknowledged the request
                and its receipt could not be read.
        """
        operation = "place_combo_order"
        with not_sent(operation):
            self.policy.check_write(operation, trd_env)
            validate_order_values(
                operation, order_type=order_type, qty=qty, combo_price=price
            )

            if not self.trade_ctx:
                raise RuntimeError("Trade context not connected")

            legs = self._build_combo_legs(combo_legs)
            resolved_acc_id = self._resolve_account(
                trd_env, self._get_market_from_code(legs[0].code), acc_id
            )
            self._check_execution_halt(operation)
            self.policy.assess_order(
                operation, self._combo_facts(operation, legs, price, qty, order_type)
            )

        trade_ctx = self.trade_ctx
        assert trade_ctx is not None

        receipt, relock_error = self._dispatch_write(
            trd_env,
            operation,
            lambda: trade_ctx.place_combo_order(
                combo_leg_list=legs,
                price=price,
                qty=qty,
                order_type=order_type,
                time_in_force=time_in_force,
                trd_env=trd_env,
                acc_id=resolved_acc_id,
                remark=remark,
            ),
            lambda data: self._first_combo_record(operation, data),
        )
        return self._with_routing(receipt, resolved_acc_id, trd_env, relock_error)

    @staticmethod
    def _first_combo_record(operation: str, data: Any) -> dict:
        records = _plain_combo_legs(as_frame(operation, data).to_dict("records"))
        return dict(records[0]) if records else {}

    # The account-impact fields comboorder_tradinginfo_query returns. Listed
    # here rather than passed through wholesale so a caller sees a stable set of
    # keys, with an explicit null for anything the gateway did not supply.
    COMBO_PREVIEW_FIELDS = (
        "nlv_change",
        "initial_margin_change",
        "maintenance_margin_change",
        "option_bp",
        "max_withdraw_change",
        "bp_decrease",
    )

    def preview_combo_order(
        self,
        combo_legs: list[dict],
        price: float,
        qty: int,
        order_type: str = "NORMAL",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> dict:
        """Preview the account impact of a multi-leg package without submitting it.

        This is a read: it asks the gateway what the package would do to margin
        and buying power. Nothing is placed, modified, cancelled, unlocked, or
        reserved, so it is permitted in every trading mode — though the broker
        can still refuse the query itself.

        Leg validation and account selection are shared with place_combo_order,
        so a package that previews is the same package that would be submitted.

        Args:
            combo_legs: Legs of the strategy, in the same format as
                place_combo_order: 'code', 'trd_side', 'qty_ratio' (required),
                and 'position_id' when closing an existing position.
            price: Net price of the whole package. The sign convention is passed
                through untouched: moomoo does not document a debit/credit
                convention, and normalizing it here would invent one.
            qty: Number of packages.
            order_type: Order type ('NORMAL' for limit, 'MARKET', etc.).
            trd_env: Trading environment ('REAL' or 'SIMULATE').
            acc_id: Account ID. Resolved from the legs' market when omitted.

        Returns:
            Dictionary with 'checked_at' (UTC observation time), the resolved
            'acc_id' and 'trd_env', and the gateway's impact fields. A field the
            gateway did not supply is None, never a substituted zero.

            The values are a point-in-time calculation. They are not a quote, an
            acceptance, or a guarantee that the package would fill.

        Raises:
            ValueError: If the leg list is malformed.
            RuntimeError: If not connected, or the gateway rejects the query.
        """
        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        legs = self._build_combo_legs(combo_legs)

        # Same resolver as placement, so the preview describes the account the
        # order would actually reach — including refusing where the placement
        # would refuse, rather than previewing an account it could not use.
        resolved_acc_id = self._resolve_account(
            trd_env, self._get_market_from_code(legs[0].code), acc_id
        )

        ret, data = self.trade_ctx.comboorder_tradinginfo_query(
            combo_leg_list=legs,
            price=price,
            qty=qty,
            order_type=order_type,
            trd_env=trd_env,
            acc_id=resolved_acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"comboorder_tradinginfo_query failed: {data}")

        records = (
            as_frame("comboorder_tradinginfo_query", data).to_dict("records")
            if data is not None
            else []
        )
        record = records[0] if records else {}

        preview: dict[str, Any] = {
            "checked_at": utc_now_iso(),
            "acc_id": resolved_acc_id,
            "trd_env": trd_env,
        }
        for field in self.COMBO_PREVIEW_FIELDS:
            preview[field] = _null_if_missing(record.get(field))
        return preview

    def _fetch_order(
        self, operation: str, order_id: str, trd_env: str, acc_id: int
    ) -> dict:
        """Read the order a modification targets, for assessment.

        A modification is assessed as the order that would result, so the fields
        the caller did not send have to come from the broker's current order —
        not from the agent's memory of it, which is what the value at risk
        actually depends on.

        Raises:
            ValueError: If the order cannot be retrieved or is not there.
        """
        assert self.trade_ctx is not None
        ret, data = self.trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=True,
        )
        if ret != RET_OK:
            raise ValueError(
                f"{operation}: could not retrieve order {order_id} to check the "
                f"resulting order against the configured limits: {data}"
            )
        records = as_frame("order_list_query", data).to_dict("records")
        matching = [
            dict(record)
            for record in records
            if str(record.get("order_id")) == str(order_id)
        ]
        if not matching:
            raise ValueError(
                f"{operation}: order {order_id} was not found in the "
                f"{trd_env} account, so the modification cannot be checked "
                "against the configured limits."
            )
        return matching[0]

    def modify_order(
        self,
        order_id: str,
        modify_order_op: str,
        qty: int | None = None,
        price: float | None = None,
        adjust_limit: float = 0,
        *,
        trd_env: str,
        acc_id: int | str = "0",
    ) -> dict:
        """Modify an existing order.

        ``NORMAL`` and ``ENABLE`` add or restore exposure, so they are assessed
        as the order that would result: the existing order is fetched, the
        requested changes are merged over it, and the whole thing is checked as
        if it were a new placement. ``CANCEL``, ``DISABLE`` and ``DELETE`` only
        reduce exposure and skip the assessment — which is also why they stay
        permitted while execution is halted.

        Args:
            order_id: Order ID to modify.
            modify_order_op: Modification operation ('NORMAL', 'CANCEL',
                'DISABLE', 'ENABLE', 'DELETE').
            qty: New quantity (optional).
            price: New price (optional).
            adjust_limit: Adjust limit percentage.
            trd_env: Trading environment ('REAL' or 'SIMULATE'). Required.
            acc_id: Account ID, or '0' to resolve one when exactly one is
                eligible.

        Returns:
            The gateway's receipt, plus the resolved ``acc_id`` and ``trd_env``.

        Raises:
            OrderNotSentError: If anything refused the modification before
                dispatch, including an order that could not be found.
            OrderOutcomeUnknownError: If the call started and nothing
                acknowledged it.
            OrderReceiptUnreadableError: If the gateway acknowledged the request
                and its receipt could not be read.
        """
        requested_op = str(modify_order_op).strip().upper()
        operation = f"modify_order ({requested_op})"

        with not_sent(operation):
            self.policy.check_write(operation, trd_env)

            if not self.trade_ctx:
                raise RuntimeError("Trade context not connected")

            # No market: a modification names an order, and the order already
            # knows its instrument.
            resolved_acc_id = self._resolve_account(trd_env, None, acc_id)

            if requested_op in EXPOSING_MODIFY_OPS:
                self._check_execution_halt(operation)
                existing = self._fetch_order(
                    operation, order_id, trd_env, resolved_acc_id
                )
                merged_qty = qty if qty is not None else existing.get("qty")
                merged_price = price if price is not None else existing.get("price")
                existing_type = str(existing.get("order_type") or "NORMAL")
                validate_order_values(
                    operation,
                    order_type=existing_type,
                    qty=int(merged_qty) if merged_qty is not None else None,
                    price=merged_price,
                    aux_price=existing.get("aux_price"),
                )
                self.policy.assess_order(
                    operation,
                    self._single_leg_facts(
                        operation,
                        code=str(existing.get("code")),
                        order_type=existing_type,
                        trd_side=str(existing.get("trd_side") or ""),
                        qty=int(merged_qty) if merged_qty is not None else 0,
                        price=(
                            float(merged_price) if merged_price is not None else None
                        ),
                        aux_price=self._optional_float(existing.get("aux_price")),
                    ),
                )

        trade_ctx = self.trade_ctx
        assert trade_ctx is not None

        receipt, relock_error = self._dispatch_write(
            trd_env,
            operation,
            lambda: trade_ctx.modify_order(
                modify_order_op=modify_order_op,
                order_id=order_id,
                qty=qty,
                price=price,
                # The SDK defaults adjust_limit to 0, so it infers as int; the
                # gateway takes a price-adjustment ratio, which is a float.
                adjust_limit=adjust_limit,  # pyright: ignore[reportArgumentType]
                trd_env=trd_env,
                acc_id=resolved_acc_id,
            ),
            lambda data: self._first_record("modify_order", data),
        )
        return self._with_routing(receipt, resolved_acc_id, trd_env, relock_error)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        """A broker field as a float, or None when it is not a usable number."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                return None
        if not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    def cancel_order(
        self,
        order_id: str,
        *,
        trd_env: str,
        acc_id: int | str = "0",
    ) -> dict:
        """Cancel an existing order.

        Convenience wrapper around modify_order with CANCEL operation.
        Cancellation reduces exposure, so it stays permitted while execution is
        halted: an operator facing a halt must still be able to pull orders.

        Args:
            order_id: Order ID to cancel.
            trd_env: Trading environment ('REAL' or 'SIMULATE'). Required.
            acc_id: Account ID, or '0' to resolve one when exactly one is
                eligible.

        Returns:
            The gateway's receipt, plus the resolved ``acc_id`` and ``trd_env``.

        Raises:
            OrderNotSentError: If anything refused the cancellation before
                dispatch. Cancellation is a write like any other: a read-only
                deployment cannot cancel an order it was never able to place.
            OrderOutcomeUnknownError: If the call started and nothing
                acknowledged it.
            OrderReceiptUnreadableError: If the gateway acknowledged the request
                and its receipt could not be read.
        """
        operation = "cancel_order"
        with not_sent(operation):
            self.policy.check_write(operation, trd_env)

            if not self.trade_ctx:
                raise RuntimeError("Trade context not connected")

            resolved_acc_id = self._resolve_account(trd_env, None, acc_id)

        trade_ctx = self.trade_ctx
        assert trade_ctx is not None

        receipt, relock_error = self._dispatch_write(
            trd_env,
            operation,
            lambda: trade_ctx.modify_order(
                modify_order_op="CANCEL",
                order_id=order_id,
                qty=0,
                price=0,
                adjust_limit=0,
                trd_env=trd_env,
                acc_id=resolved_acc_id,
            ),
            lambda data: self._first_record(operation, data),
        )
        return self._with_routing(receipt, resolved_acc_id, trd_env, relock_error)

    def get_orders(
        self,
        code: str = "",
        status_filter_list: list[str] | None = None,
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
    ) -> list[dict]:
        """Get list of today's orders.

        Args:
            code: Filter by stock code.
            status_filter_list: Filter by order statuses (as strings).
                Valid options: UNSUBMITTED, WAITING_SUBMIT, SUBMITTING,
                SUBMIT_FAILED, SUBMITTED, FILLED_PART, FILLED_ALL,
                CANCELLING_PART, CANCELLING_ALL, CANCELLED_PART,
                CANCELLED_ALL, REJECTED, DISABLED, DELETED, FAILED, NONE.
            trd_env: Trading environment.
            acc_id: Account ID.
            refresh_cache: Whether to refresh cache.

        Returns:
            List of order dictionaries. Returns empty list if no orders found.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Convert string status values to OrderStatus enum values
        converted_status_filter = self._convert_status_filter(status_filter_list)

        ret, data = self.trade_ctx.order_list_query(
            code=code,
            status_filter_list=converted_status_filter,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
        if ret != RET_OK:
            raise RuntimeError(f"order_list_query failed: {data}")

        # Handle None or empty DataFrame gracefully
        if data is None:
            return []

        frame = as_frame("order_list_query", data)
        if frame.empty:
            return []

        return _plain_combo_legs(frame.to_dict("records"))

    def get_deals(
        self,
        code: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
        refresh_cache: bool = False,
    ) -> list[dict]:
        """Get list of today's deals (executed trades).

        Args:
            code: Filter by stock code.
            trd_env: Trading environment.
            acc_id: Account ID.
            refresh_cache: Whether to refresh cache.

        Returns:
            List of deal dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.deal_list_query(
            code=code,
            trd_env=trd_env,
            acc_id=acc_id,
            refresh_cache=refresh_cache,
        )
        if ret != RET_OK:
            raise RuntimeError(f"deal_list_query failed: {data}")

        return as_frame("deal_list_query", data).to_dict("records")

    def get_history_orders(
        self,
        code: str = "",
        status_filter_list: list[str] | None = None,
        start: str = "",
        end: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get historical orders.

        Args:
            code: Filter by stock code.
            status_filter_list: Filter by order statuses (as strings).
                Valid options: UNSUBMITTED, WAITING_SUBMIT, SUBMITTING,
                SUBMIT_FAILED, SUBMITTED, FILLED_PART, FILLED_ALL,
                CANCELLING_PART, CANCELLING_ALL, CANCELLED_PART,
                CANCELLED_ALL, REJECTED, DISABLED, DELETED, FAILED, NONE.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            List of historical order dictionaries.
            Returns empty list if no orders found.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        # Convert string status values to OrderStatus enum values
        converted_status_filter = self._convert_status_filter(status_filter_list)

        ret, data = self.trade_ctx.history_order_list_query(
            code=code,
            status_filter_list=converted_status_filter,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"history_order_list_query failed: {data}")

        # Handle None or empty DataFrame gracefully
        if data is None:
            return []

        frame = as_frame("history_order_list_query", data)
        if frame.empty:
            return []

        return _plain_combo_legs(frame.to_dict("records"))

    def get_history_deals(
        self,
        code: str = "",
        start: str = "",
        end: str = "",
        trd_env: str = "SIMULATE",
        acc_id: int | str = "0",
    ) -> list[dict]:
        """Get historical deals (executed trades).

        Args:
            code: Filter by stock code.
            start: Start date (YYYY-MM-DD).
            end: End date (YYYY-MM-DD).
            trd_env: Trading environment.
            acc_id: Account ID.

        Returns:
            List of historical deal dictionaries.
        """
        if isinstance(acc_id, str):
            acc_id = int(acc_id)

        if not self.trade_ctx:
            raise RuntimeError("Trade context not connected")

        ret, data = self.trade_ctx.history_deal_list_query(
            code=code,
            start=start,
            end=end,
            trd_env=trd_env,
            acc_id=acc_id,
        )
        if ret != RET_OK:
            raise RuntimeError(f"history_deal_list_query failed: {data}")

        return as_frame("history_deal_list_query", data).to_dict("records")
