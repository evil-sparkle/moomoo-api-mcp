#!/usr/bin/env python3
"""Re-check, against the pinned SDK, every fact the trading-safeguards design assumes.

`openspec/changes/harden-trading-safeguards/design.md` states a list of facts about
`moomoo-api` and then builds decisions on top of them. Those facts were read once, by
hand, from a dev venv. This script reads them again, mechanically, so that a future
SDK bump either keeps them true or fails loudly.

Everything here is offline. No OpenD gateway, no account, no network: each check
inspects the installed `moomoo` package -- its protobuf descriptors, its call
signatures, its response-unpacking source and its own field docstrings. The checks
that genuinely need a live gateway or an account are not here; they live in
`scripts/verify_gateway_facts.py` and are operator-run.

Run it directly for a readable report::

    uv run python scripts/verify_sdk_facts.py

It exits non-zero when any fact no longer holds. `tests/test_sdk_facts.py` runs the
same checks under pytest so CI carries them too.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from moomoo import (  # type: ignore[import-untyped]
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    SecurityFirm,
    SecurityType,
    TrdEnv,
)
from moomoo.common.pb import (  # type: ignore[import-untyped]
    Qot_GetSecuritySnapshot_pb2 as snapshot_pb,
)
from moomoo.quote import quote_query  # type: ignore[import-untyped]
from moomoo.trade.open_trade_context import (  # type: ignore[import-untyped]
    OpenSecTradeContext as TradeContext,
)

# The version the design was written against. A mismatch is reported, not fatal:
# the checks below are the real test of whether the design still holds.
PINNED_SDK_VERSION = "10.10.7008"


@dataclass(frozen=True)
class Finding:
    """One design-relied fact, re-checked."""

    fact: str
    decision: str
    holds: bool
    evidence: str


def _snapshot_keys() -> frozenset[str]:
    """Every key `MarketSnapshotQuery.unpack_rsp` can put in a snapshot row.

    The method builds a plain dict with literal string keys, so the source is the
    authoritative list of what a caller can read. Parsing it beats calling the
    gateway: it covers the branches an offline check could never reach, such as the
    option-only fields.
    """
    source = inspect.getsource(quote_query.MarketSnapshotQuery.unpack_rsp)
    keys: set[str] = set()
    for line in source.splitlines():
        marker = "snapshot_tmp["
        start = line.find(marker)
        if start < 0:
            continue
        rest = line[start + len(marker) :]
        quote = rest[:1]
        if quote not in {"'", '"'}:
            continue
        end = rest.find(quote, 1)
        if end > 1:
            keys.add(rest[1:end])
    return frozenset(keys)


def _option_ex_data_descriptor() -> Any:
    return snapshot_pb.DESCRIPTOR.message_types_by_name["OptionSnapshotExData"]


def _empty_option_ex_data() -> Any:
    """An unpopulated OptionSnapshotExData.

    The generated pb2 module builds its message classes at import time, so they
    are invisible to a static checker; reaching them by name is the honest way to
    say "this exists at runtime".
    """
    return vars(snapshot_pb)["OptionSnapshotExData"]()


def _option_ex_data_fields() -> frozenset[str]:
    return frozenset(field.name for field in _option_ex_data_descriptor().fields)


def _snapshot_docstring() -> str:
    return inspect.getdoc(OpenQuoteContext.get_market_snapshot) or ""


def _docstring_line(haystack: str, field: str) -> str:
    """The `get_market_snapshot` docstring line describing `field`, stripped."""
    for line in haystack.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{field} "):
            return " ".join(stripped.split())
    return ""


# --- the checks -----------------------------------------------------------------
#
# Each returns (holds, evidence). They are deliberately independent: a failure names
# one fact and one decision, so a future SDK bump gets a list of what moved.


def check_snapshot_has_no_classification() -> tuple[bool, str]:
    keys = _snapshot_keys()
    classification_keys = {"sec_type", "stock_type", "security_type", "sec_type_str"}
    found = sorted(keys & classification_keys)
    return (
        not found,
        f"MarketSnapshotQuery.unpack_rsp emits no {sorted(classification_keys)} key"
        if not found
        else f"snapshot now carries {found}; classification need not come from "
        "get_stock_basicinfo any more",
    )


def check_snapshot_has_no_currency() -> tuple[bool, str]:
    keys = _snapshot_keys()
    found = sorted(k for k in keys if "currency" in k or "curr_code" in k)
    return (
        not found,
        "MarketSnapshotQuery.unpack_rsp emits no currency key, so currency must be "
        "established outside the snapshot"
        if not found
        else f"snapshot now carries {found}; the verified-market table can be "
        "replaced by a real currency field",
    )


def check_quote_fields_use_na_sentinel() -> tuple[bool, str]:
    source = inspect.getsource(quote_query.MarketSnapshotQuery.unpack_rsp)
    sentinel_fields = []
    for field in ("ask_price", "bid_price", "option_contract_multiplier"):
        assignment = f"snapshot_tmp['{field}']"
        for line in source.splitlines():
            if assignment in line and "'N/A'" in line:
                sentinel_fields.append(field)
                break
    expected = ["ask_price", "bid_price", "option_contract_multiplier"]
    return (
        sentinel_fields == expected,
        f"these snapshot fields fall back to the string 'N/A': {sentinel_fields}. "
        "Quote normalization must treat 'N/A' as absent for all of them, not only "
        "for bid and ask",
    )


def check_contract_size_is_unguarded() -> tuple[bool, str]:
    """`option_contract_size` has no `HasField` guard, so an omitted field reads 0.0.

    It is assigned straight from `contractSizeFloat`, which is an optional proto
    field. Protobuf returns the zero default for an absent optional scalar, so the
    caller cannot tell "not sent" from "zero" -- and a multiplier of zero would
    value every option at nothing. This is why the adapter treats a non-positive
    multiplier as absent and refuses, rather than trusting the number.
    """
    source = inspect.getsource(quote_query.MarketSnapshotQuery.unpack_rsp)
    line = next(
        (
            " ".join(raw.split())
            for raw in source.splitlines()
            if "snapshot_tmp['option_contract_size']" in raw
        ),
        "",
    )
    unguarded = bool(line) and "HasField" not in line
    # Demonstrated rather than deduced: an unset optional scalar reads back as the
    # zero default, and only HasField can tell that apart from a real zero.
    empty = _empty_option_ex_data()
    reads_as_zero = (
        not empty.HasField("contractSizeFloat") and empty.contractSizeFloat == 0.0
    )
    return (
        unguarded and reads_as_zero,
        f"{line!r}; an unset contractSizeFloat reads back as "
        f"{empty.contractSizeFloat!r} with HasField "
        f"{empty.HasField('contractSizeFloat')}, so an omitted value is "
        "indistinguishable from zero once the SDK has copied it out",
    )


def check_contract_multiplier_is_index_option_only() -> tuple[bool, str]:
    """The SDK's own field table says which option field carries what.

    This is the evidence task 1.1 asks for on monetary semantics, and it is
    documentary rather than numeric: it does not rest on a field happening to equal
    100 for standard US contracts.
    """
    doc = _snapshot_docstring()
    size_line = _docstring_line(doc, "option_contract_size")
    multiplier_line = _docstring_line(doc, "option_contract_multiplier")
    nominal_line = _docstring_line(doc, "option_contract_nominal_value")
    # 每份合约数 = units of the underlying per contract.
    # 合约乘数，指数期权特有字段 = contract multiplier, a field specific to index
    # options.
    # 合约名义金额 = contract nominal amount.
    holds = (
        "每份合约数" in size_line
        and "指数期权特有字段" in multiplier_line
        and "合约名义金额" in nominal_line
    )
    return (
        holds,
        f"{size_line!r}; {multiplier_line!r}; {nominal_line!r}",
    )


def check_option_fields_exist() -> tuple[bool, str]:
    fields = _option_ex_data_fields()
    required = {"contractSize", "contractSizeFloat", "contractMultiplier"}
    missing = sorted(required - fields)
    return (not missing, f"OptionSnapshotExData carries {sorted(required)}")


def check_snapshot_carries_prices_and_lot_size() -> tuple[bool, str]:
    keys = _snapshot_keys()
    required = {"last_price", "bid_price", "ask_price", "lot_size"}
    missing = sorted(required - keys)
    return (not missing, f"snapshot carries {sorted(required)}")


def check_basicinfo_takes_code_list() -> tuple[bool, str]:
    params = inspect.signature(OpenQuoteContext.get_stock_basicinfo).parameters
    return (
        "code_list" in params,
        f"get_stock_basicinfo{inspect.signature(OpenQuoteContext.get_stock_basicinfo)}"
        " accepts an explicit code_list, so the adapter asks for the order's codes "
        "rather than enumerating a market",
    )


def check_basicinfo_returns_stock_type() -> tuple[bool, str]:
    doc = inspect.getdoc(OpenQuoteContext.get_stock_basicinfo) or ""
    return ("stock_type" in doc, "get_stock_basicinfo documents a stock_type column")


def check_security_types_cover_the_classifications() -> tuple[bool, str]:
    required = {"STOCK", "ETF", "DRVT"}
    present = {name for name in required if hasattr(SecurityType, name)}
    return (
        present == required,
        f"SecurityType defines {sorted(required)}; assess_order classifies against "
        "these three and refuses anything else",
    )


def check_order_type_classes_are_exhaustive() -> tuple[bool, str]:
    """Every submission order type falls in exactly one of the two design classes.

    The algorithmic types are deliberately left out: Moomoo documents TWAP/VWAP as
    query-only, so they are not submission types. If the SDK gains a new order type,
    this fails and someone has to classify it -- which is the point.
    """
    fixed_limit = {
        "NORMAL",
        "ABSOLUTE_LIMIT",
        "SPECIAL_LIMIT",
        "SPECIAL_LIMIT_ALL",
        "AUCTION_LIMIT",
        "STOP_LIMIT",
        "LIMIT_IF_TOUCHED",
    }
    no_fixed_limit = {
        "MARKET",
        "AUCTION",
        "STOP",
        "MARKET_IF_TOUCHED",
        "TRAILING_STOP",
        "TRAILING_STOP_LIMIT",
    }
    query_only = {"TWAP", "TWAP_LIMIT", "VWAP", "VWAP_LIMIT"}
    defined = {name for name in dir(OrderType) if name.isupper() and name != "NONE"}
    unclassified = sorted(defined - fixed_limit - no_fixed_limit - query_only)
    overlap = sorted(fixed_limit & no_fixed_limit)
    missing = sorted((fixed_limit | no_fixed_limit | query_only) - defined)
    holds = not unclassified and not overlap and not missing
    return (
        holds,
        "OrderType splits into fixed-limit, no-fixed-limit and query-only with "
        "nothing left over"
        if holds
        else f"unclassified={unclassified} overlap={overlap} missing={missing}",
    )


def check_order_list_query_targets_one_order() -> tuple[bool, str]:
    params = inspect.signature(OpenSecTradeContext.order_list_query).parameters
    needed = {"order_id", "refresh_cache", "trd_env", "acc_id"}
    missing = sorted(needed - set(params))
    return (
        not missing,
        f"order_list_query accepts {sorted(needed)}, so a modification can fetch "
        "exactly its target order",
    )


def check_history_order_query_has_no_order_id() -> tuple[bool, str]:
    """Worth pinning: the history query cannot be asked for one order.

    Task 1.2 measures how long a terminal paper order stays queryable through both
    queries. The history side has to filter by code and date range instead, so the
    operator script cannot simply pass an order id to both.
    """
    params = inspect.signature(OpenSecTradeContext.history_order_list_query).parameters
    return (
        "order_id" not in params,
        "history_order_list_query takes no order_id; it filters by code and date",
    )


def check_lock_path_resolves_a_real_account() -> tuple[bool, str]:
    """The `_check_acc_id` call sits outside the `is_unlock` branch.

    This is the whole basis of the design's "a failing lock has no recovery" risk:
    locking, not just unlocking, needs a resolvable REAL account.
    """
    source = inspect.getsource(TradeContext.unlock_trade)
    lines = [" ".join(line.split()) for line in source.splitlines()]
    try:
        guard = next(
            i for i, line in enumerate(lines) if line.startswith("if is_unlock")
        )
        check = next(i for i, line in enumerate(lines) if "_check_acc_id(" in line)
    except StopIteration:
        return (False, "could not locate the is_unlock guard or the _check_acc_id call")
    guard_indent = len(source.splitlines()[guard]) - len(
        source.splitlines()[guard].lstrip()
    )
    check_indent = len(source.splitlines()[check]) - len(
        source.splitlines()[check].lstrip()
    )
    outside_branch = check > guard and check_indent <= guard_indent
    return (
        outside_branch,
        "unlock_trade calls _check_acc_id(TrdEnv.REAL, 0) outside the is_unlock "
        "branch, so a lock (is_unlock=False) also needs a resolvable REAL account",
    )


def check_unlock_is_cached_and_cleared_by_lock() -> tuple[bool, str]:
    source = " ".join(inspect.getsource(TradeContext.unlock_trade).split())
    cached = "self._ctx_unlock = (password, password_md5) if is_unlock else None" in (
        source
    )
    return (
        cached,
        "unlock_trade caches the credential in _ctx_unlock on unlock and clears it "
        "on lock",
    )


def check_reconnect_replays_the_cached_unlock() -> tuple[bool, str]:
    base = next(
        cls for cls in TradeContext.__mro__ if "on_api_socket_reconnected" in vars(cls)
    )
    source = " ".join(inspect.getsource(base.on_api_socket_reconnected).split())
    replays = "if self._ctx_unlock is not None:" in source and "self.unlock_trade(" in (
        source
    )
    return (
        replays,
        f"{base.__name__}.on_api_socket_reconnected replays the cached unlock after "
        "every reconnect, which is why startup auto-unlock leaves the gateway "
        "unlocked for the life of the process",
    )


def check_security_firm_values() -> tuple[bool, str]:
    names = sorted(
        name for name in dir(SecurityFirm) if name.isupper() and name != "NONE"
    )
    return (
        bool(names),
        f"SecurityFirm defines {names}; load_settings validates against these",
    )


def check_trd_env_values() -> tuple[bool, str]:
    names = sorted(name for name in dir(TrdEnv) if name.isupper())
    return (names == ["REAL", "SIMULATE"], f"TrdEnv defines {names}")


CHECKS: Sequence[tuple[str, str, Callable[[], tuple[bool, str]]]] = (
    (
        "the snapshot carries no security classification",
        "Decision 3 (adapter reads get_stock_basicinfo)",
        check_snapshot_has_no_classification,
    ),
    (
        "the snapshot carries no currency field",
        "Decision 2 (verified-market currency table)",
        check_snapshot_has_no_currency,
    ),
    (
        "the snapshot carries last_price, bid_price, ask_price and lot_size",
        "Decision 3 (market reference M)",
        check_snapshot_carries_prices_and_lot_size,
    ),
    (
        "absent quote fields read back as the string 'N/A'",
        "Decision 3 (quote normalization precedes assessment)",
        check_quote_fields_use_na_sentinel,
    ),
    (
        "option_contract_size is unguarded and reads 0.0 when omitted",
        "Decision 3 (a non-positive multiplier is absent, not zero)",
        check_contract_size_is_unguarded,
    ),
    (
        "option_contract_multiplier is documented as index-options-only, "
        "option_contract_size as units per contract",
        "Decision 3 (which field is the monetary multiplier)",
        check_contract_multiplier_is_index_option_only,
    ),
    (
        "OptionSnapshotExData carries the contract fields",
        "Decision 3 (monetary multiplier)",
        check_option_fields_exist,
    ),
    (
        "get_stock_basicinfo accepts an explicit code_list",
        "Decision 3 (adapter asks for the order's codes)",
        check_basicinfo_takes_code_list,
    ),
    (
        "get_stock_basicinfo returns stock_type",
        "Decision 3 (classification source)",
        check_basicinfo_returns_stock_type,
    ),
    (
        "SecurityType defines STOCK, ETF and DRVT",
        "Decision 3 (multipliers by classification)",
        check_security_types_cover_the_classifications,
    ),
    (
        "every submission OrderType is classified exactly once",
        "Decision 3 (FIXED_LIMIT_TYPES / NO_FIXED_LIMIT_TYPES)",
        check_order_type_classes_are_exhaustive,
    ),
    (
        "order_list_query can target one order and refresh the cache",
        "Decision 5 (modifications assess the resulting order)",
        check_order_list_query_targets_one_order,
    ),
    (
        "history_order_list_query cannot target one order",
        "task 1.2 (retention measurement filters by code and date)",
        check_history_order_query_has_no_order_id,
    ),
    (
        "locking, not just unlocking, resolves a REAL account",
        "Decision 8 risk (a failing lock_trade has no recovery)",
        check_lock_path_resolves_a_real_account,
    ),
    (
        "the SDK caches a successful unlock and clears it on lock",
        "Decision 7 (startup auto-unlock is removed)",
        check_unlock_is_cached_and_cleared_by_lock,
    ),
    (
        "a reconnect replays the cached unlock",
        "Decision 7 (lock at rest on every reconnect)",
        check_reconnect_replays_the_cached_unlock,
    ),
    (
        "SecurityFirm enumerates the accepted firms",
        "Decision 1 (an unknown firm is a startup error)",
        check_security_firm_values,
    ),
    (
        "TrdEnv is exactly REAL and SIMULATE",
        "Decision 6 (account routing)",
        check_trd_env_values,
    ),
)


def run_checks() -> list[Finding]:
    findings: list[Finding] = []
    for fact, decision, check in CHECKS:
        try:
            holds, evidence = check()
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed fact
            holds, evidence = False, f"check raised {type(exc).__name__}: {exc}"
        findings.append(
            Finding(fact=fact, decision=decision, holds=holds, evidence=evidence)
        )
    return findings


def installed_sdk_version() -> str:
    import moomoo  # type: ignore[import-untyped]

    version: Any = getattr(moomoo, "__version__", None)
    return str(version) if version else "unknown"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the failures and the summary line",
    )
    args = parser.parse_args(argv)

    version = installed_sdk_version()
    print(
        f"moomoo-api installed: {version} (design was written against "
        f"{PINNED_SDK_VERSION})"
    )
    if version != PINNED_SDK_VERSION:
        print(
            "  note: the version differs from the pinned one. The checks below, not "
            "the version string, decide whether the design still holds."
        )
    print()

    findings = run_checks()
    for finding in findings:
        if args.quiet and finding.holds:
            continue
        mark = "ok  " if finding.holds else "FAIL"
        print(f"[{mark}] {finding.fact}")
        print(f"        relied on by: {finding.decision}")
        print(f"        evidence:     {finding.evidence}")
        print()

    failed = [finding for finding in findings if not finding.holds]
    print(f"{len(findings) - len(failed)}/{len(findings)} facts still hold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
