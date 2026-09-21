#!/usr/bin/env python3
"""Operator-run checks for the trading-safeguards facts a live gateway has to answer.

`scripts/verify_sdk_facts.py` settles everything the pinned SDK can settle on its
own. What is left needs an OpenD gateway and an account behind it: what a real
snapshot actually contains, how long a cancelled paper order stays queryable, and
which REAL reads still work once the gateway is locked. Those are tasks 1.1, 1.2 and
1.3 of `openspec/changes/harden-trading-safeguards/tasks.md`, and this script runs
them reproducibly so the answers are recorded rather than remembered.

Each phase is opt-in, and the two that are not read-only need their own flag:

    instruments       read-only quotes. Task 1.1: snapshot and classification fields
                      for a US stock, a US ETF and a US equity option, plus an
                      option with no trades today.
    positions         read-only. Task 1.1's independent cross-check: values an
                      existing option position against each candidate multiplier
                      and reports which one reproduces the broker's market value.
    paper-retention   places, cancels and re-queries ONE far-from-market SIMULATE
                      DAY order. Task 1.2. Needs --i-authorize-paper-orders.
    real-reads        locks the REAL gateway, then runs the REAL reads against it.
                      Task 1.3. Needs --i-authorize-real-gateway-lock, and it
                      changes the gateway's lock state.

Safety: this script never places, modifies or cancels a REAL order. The placement
path hardcodes `TrdEnv.SIMULATE` and asserts it before dispatch; there is no flag
that changes it. `real-reads` issues `unlock_trade(is_unlock=False)` -- a lock, never
an unlock -- and reads.

Results go to stdout as a readable report, and to `--json-out` as a machine-readable
record to paste into the change's verification log.

    uv run python scripts/verify_gateway_facts.py instruments \\
        --host 127.0.0.1 --port 11111 \\
        --stock US.AAPL --etf US.SPY --option US.AAPL260116C300000 \\
        --json-out evidence.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from moomoo import (  # type: ignore[import-untyped]
    RET_OK,
    ModifyOrderOp,
    OpenQuoteContext,
    OpenSecTradeContext,
    OrderType,
    SecurityFirm,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

# Quote fields the assessment reads, and the two candidates for the monetary
# multiplier. Recorded verbatim, including the 'N/A' sentinel, so the log shows what
# the gateway actually sent rather than what normalization made of it.
SNAPSHOT_FIELDS = (
    "code",
    "last_price",
    "bid_price",
    "ask_price",
    "lot_size",
    "option_valid",
    "option_contract_size",
    "option_contract_multiplier",
    "option_contract_nominal_value",
    "option_owner_lot_multiplier",
)
MULTIPLIER_CANDIDATES = (
    "option_contract_size",
    "option_contract_multiplier",
    "option_contract_nominal_value",
)


@dataclass
class Report:
    """Everything one run observed, ready to be written out as JSON."""

    phase: str
    observations: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)
        print(f"  note: {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"  FAIL: {message}")


def _call(report: Report, operation: str, fn: Any, /, **kwargs: Any) -> Any | None:
    """Run one SDK call, recording its outcome. Returns None on a non-OK return."""
    ret, data = fn(**kwargs)
    if ret != RET_OK:
        report.fail(f"{operation} returned {ret}: {data}")
        return None
    return data


def _rows(data: Any) -> list[dict[str, Any]]:
    """Normalize an SDK payload to a list of plain dicts."""
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(row) for row in data]
    to_dict = getattr(data, "to_dict", None)
    if to_dict is None:
        return []
    return [dict(row) for row in to_dict(orient="records")]


def _numeric(value: Any) -> float | None:
    """The adapter's normalization, applied here so the log shows both forms.

    Absent, non-numeric (the `'N/A'` sentinel included), non-finite and non-positive
    values all become None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


# --- phases ---------------------------------------------------------------------


def phase_instruments(
    quote_ctx: OpenQuoteContext, codes: dict[str, str], report: Report
) -> None:
    """Task 1.1: record the fields the assessment needs, per instrument."""
    wanted = [code for code in codes.values() if code]

    print(f"\n[snapshot] get_market_snapshot({wanted})")
    snapshots = _rows(
        _call(
            report,
            "get_market_snapshot",
            quote_ctx.get_market_snapshot,
            code_list=wanted,
        )
    )
    by_code = {row.get("code"): row for row in snapshots}
    returned = sorted(str(code) for code in by_code)
    report.observations["snapshot_codes_returned"] = returned
    missing = [code for code in wanted if code not in by_code]
    if missing:
        report.fail(f"get_market_snapshot did not return {missing}")

    recorded: dict[str, Any] = {}
    for role, code in codes.items():
        row = by_code.get(code)
        if row is None:
            continue
        fields = {name: row.get(name) for name in SNAPSHOT_FIELDS}
        fields["_normalized"] = {
            name: _numeric(row.get(name))
            for name in ("last_price", "bid_price", "ask_price", *MULTIPLIER_CANDIDATES)
        }
        recorded[role] = fields
        print(f"  {role} {code}:")
        for name in SNAPSHOT_FIELDS[1:]:
            print(f"      {name:<32} {row.get(name)!r}")
    report.observations["snapshots"] = recorded

    # Classification. get_stock_basicinfo is per-market, so group the codes by their
    # prefix and pass each group as an explicit code_list.
    print("\n[classification] get_stock_basicinfo(market, code_list=...)")
    classification: dict[str, Any] = {}
    by_market: dict[str, list[str]] = {}
    for code in wanted:
        market = code.split(".", 1)[0]
        by_market.setdefault(market, []).append(code)
    for market, group in by_market.items():
        # stock_type is a filter on the request; asking for each candidate type
        # separately is what actually returns options alongside equities.
        for stock_type in ("STOCK", "ETF", "DRVT"):
            data = _call(
                report,
                f"get_stock_basicinfo({market}, {stock_type})",
                quote_ctx.get_stock_basicinfo,
                market=market,
                stock_type=stock_type,
                code_list=group,
            )
            for row in _rows(data):
                code = row.get("code")
                if code in group:
                    classification[str(code)] = {
                        "stock_type": row.get("stock_type"),
                        "name": row.get("name"),
                        "lot_size": row.get("lot_size"),
                        "requested_as": stock_type,
                    }
    for code in wanted:
        found = classification.get(code)
        label = found["stock_type"] if found else "*** NOT RETURNED ***"
        print(f"  {code:<28} {label}")
        if found is None:
            report.fail(
                f"get_stock_basicinfo did not return {code} for any stock_type; "
                "the adapter refuses an order whose code is not classified"
            )
    report.observations["classification"] = classification

    option_code = codes.get("option")
    if option_code and option_code in by_code:
        row = by_code[option_code]
        present = {
            name: row.get(name)
            for name in MULTIPLIER_CANDIDATES
            if _numeric(row.get(name)) is not None
        }
        report.observations["option_multiplier_candidates_present"] = present
        report.note(
            f"option {option_code}: multiplier candidates that normalize to a "
            f"number: {present or 'none'}. A value here settles nothing on its own "
            "-- run the positions phase to see which one reproduces the broker's "
            "own market value."
        )

    quiet = codes.get("quiet_option")
    if quiet and quiet in by_code:
        row = by_code[quiet]
        report.observations["option_without_trades"] = {
            name: row.get(name) for name in ("last_price", "bid_price", "ask_price")
        }
        report.note(
            f"option with no trades today ({quiet}): last={row.get('last_price')!r} "
            f"bid={row.get('bid_price')!r} ask={row.get('ask_price')!r}. All three "
            "normalizing to None is the fail-closed refusal the design predicts for "
            "a SELL limit or a no-fixed-limit order."
        )


def phase_positions(
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    trd_env: str,
    report: Report,
) -> None:
    """Task 1.1's independent cross-check of the monetary multiplier.

    An option position's own `market_val`, divided by its quantity and its quoted
    price, is the cash multiplier the broker itself applied. Comparing that against
    each candidate field settles which field carries monetary semantics, without
    resting on a field happening to equal 100.
    """
    print(f"\n[positions] position_list_query(trd_env={trd_env})")
    positions = _rows(
        _call(
            report,
            "position_list_query",
            trade_ctx.position_list_query,
            trd_env=trd_env,
        )
    )
    option_positions = [
        row
        for row in positions
        if str(row.get("stock_name", "")) and _numeric(row.get("qty")) is not None
    ]
    if not option_positions:
        report.note(
            "no positions returned; the cross-check needs at least one open option "
            "position. Record this as 'not established' rather than assuming a field."
        )
        return

    codes = [str(row.get("code")) for row in option_positions]
    snapshots = {
        str(row.get("code")): row
        for row in _rows(
            _call(
                report,
                "get_market_snapshot",
                quote_ctx.get_market_snapshot,
                code_list=codes,
            )
        )
    }

    results: list[dict[str, Any]] = []
    for row in option_positions:
        code = str(row.get("code"))
        snapshot = snapshots.get(code)
        if snapshot is None or not snapshot.get("option_valid"):
            continue
        qty = _numeric(row.get("qty"))
        market_val = _numeric(row.get("market_val"))
        price = _numeric(row.get("nominal_price")) or _numeric(
            snapshot.get("last_price")
        )
        if qty is None or market_val is None or price is None:
            report.note(f"{code}: not enough numbers to cross-check; skipped")
            continue
        implied = market_val / (qty * price)
        matches = {name: _numeric(snapshot.get(name)) for name in MULTIPLIER_CANDIDATES}
        agreeing = sorted(
            name
            for name, value in matches.items()
            if value is not None and math.isclose(value, implied, rel_tol=0.01)
        )
        entry = {
            "code": code,
            "qty": qty,
            "price": price,
            "market_val": market_val,
            "implied_monetary_multiplier": implied,
            "candidates": matches,
            "fields_matching_implied": agreeing,
        }
        results.append(entry)
        print(
            f"  {code}: market_val {market_val} / (qty {qty} x price {price})"
            f" => implied multiplier {implied:.4f}"
        )
        print(f"      candidates: {matches}")
        print(f"      agreeing:   {agreeing or 'NONE'}")
        if not agreeing:
            report.fail(
                f"{code}: no candidate field reproduces the broker's market value. "
                "Do not pick a multiplier until this resolves."
            )
    report.observations["multiplier_cross_check"] = results


def phase_paper_retention(
    trade_ctx: OpenSecTradeContext,
    code: str,
    price: float,
    qty: int,
    report: Report,
) -> None:
    """Task 1.2: how long a cancelled paper DAY order stays queryable.

    One order, in SIMULATE, far from the market, cancelled immediately. The point is
    the retention window afterwards, which is what Decision 5 actually depends on:
    how late a modification can still find its target.
    """
    trd_env = TrdEnv.SIMULATE
    # Not a defensive flourish. This is the one place the script talks to an order
    # endpoint, and the guarantee in the module docstring rests on it.
    assert trd_env == TrdEnv.SIMULATE, "this script never places a REAL order"

    print(f"\n[paper] place_order(SIMULATE, {code}, {qty} @ {price}, DAY limit)")
    placed = _rows(
        _call(
            report,
            "place_order",
            trade_ctx.place_order,
            price=price,
            qty=qty,
            code=code,
            trd_side=TrdSide.BUY,
            order_type=OrderType.NORMAL,
            trd_env=trd_env,
        )
    )
    if not placed:
        report.fail("no order was placed; nothing to measure")
        return
    order_id = str(placed[0].get("order_id"))
    report.observations["order_id"] = order_id
    report.observations["submitted_time_in_force"] = "DAY"
    print(f"  order_id {order_id}")

    print("\n[paper] cancel_order")
    _call(
        report,
        "modify_order(CANCEL)",
        trade_ctx.modify_order,
        modify_order_op=ModifyOrderOp.CANCEL,
        order_id=order_id,
        qty=0,
        price=0,
        trd_env=trd_env,
    )

    def probe(label: str) -> dict[str, Any]:
        by_id = _rows(
            _call(
                report,
                f"order_list_query({label})",
                trade_ctx.order_list_query,
                order_id=order_id,
                trd_env=trd_env,
                refresh_cache=True,
            )
        )
        history = _rows(
            _call(
                report,
                f"history_order_list_query({label})",
                trade_ctx.history_order_list_query,
                code=code,
                trd_env=trd_env,
            )
        )
        found_history = [row for row in history if str(row.get("order_id")) == order_id]
        outcome = {
            "order_list_query_returns_it": bool(by_id),
            "order_list_query_status": by_id[0].get("order_status") if by_id else None,
            "stored_time_in_force": by_id[0].get("time_in_force") if by_id else None,
            "history_order_query_returns_it": bool(found_history),
        }
        print(f"  {label}: {outcome}")
        return outcome

    retention: dict[str, Any] = {"immediately": probe("immediately")}
    print("\n  waiting 60s for the later-in-session probe")
    time.sleep(60)
    retention["later_same_session"] = probe("later same session")
    report.observations["retention"] = retention
    report.note(
        "the after-close probe is a separate run: re-run this script's "
        "`paper-retention-recheck` phase with --order-id after the trading day "
        "closes. Decision 5 needs both windows."
    )


def phase_paper_retention_recheck(
    trade_ctx: OpenSecTradeContext, order_id: str, code: str, report: Report
) -> None:
    """The after-close half of task 1.2, run against an order from an earlier run."""
    trd_env = TrdEnv.SIMULATE
    print(f"\n[paper] after-close probe for order {order_id}")
    by_id = _rows(
        _call(
            report,
            "order_list_query",
            trade_ctx.order_list_query,
            order_id=order_id,
            trd_env=trd_env,
            refresh_cache=True,
        )
    )
    history = _rows(
        _call(
            report,
            "history_order_list_query",
            trade_ctx.history_order_list_query,
            code=code,
            trd_env=trd_env,
        )
    )
    outcome = {
        "order_list_query_returns_it": bool(by_id),
        "history_order_query_returns_it": any(
            str(row.get("order_id")) == order_id for row in history
        ),
        "stored_time_in_force": by_id[0].get("time_in_force") if by_id else None,
    }
    print(f"  after the trading day closed: {outcome}")
    report.observations["retention_after_close"] = outcome


def phase_real_reads(
    trade_ctx: OpenSecTradeContext, password_md5: str, report: Report
) -> None:
    """Task 1.3: which REAL reads still work with the gateway locked.

    The lock itself is the first finding. `lock_trade` is the only route out of
    `HALTED`, and the SDK resolves a REAL account before issuing it, so a gateway
    that cannot resolve one has no recovery path. That outcome is recorded whether
    it succeeds or fails.
    """
    print("\n[real] unlock_trade(is_unlock=False)  -- locking, never unlocking")
    ret, data = trade_ctx.unlock_trade(password_md5=password_md5, is_unlock=False)
    lock_ok = ret == RET_OK
    report.observations["lock_trade"] = {
        "succeeded": lock_ok,
        "message": str(data) if data is not None else None,
    }
    print(f"  lock_trade: {'OK' if lock_ok else 'FAILED'} ({data})")
    if not lock_ok:
        report.fail(
            "lock_trade failed on this gateway. Decision 8 makes it the only exit "
            "from HALTED, so record this as a missing recovery path before the "
            "halt ships."
        )

    reads: dict[str, Any] = {}
    trd_env = TrdEnv.REAL
    checks: list[tuple[str, Any, dict[str, Any]]] = [
        ("get_acc_list", trade_ctx.get_acc_list, {}),
        ("accinfo_query", trade_ctx.accinfo_query, {"trd_env": trd_env}),
        ("position_list_query", trade_ctx.position_list_query, {"trd_env": trd_env}),
        ("order_list_query", trade_ctx.order_list_query, {"trd_env": trd_env}),
        ("deal_list_query", trade_ctx.deal_list_query, {"trd_env": trd_env}),
    ]
    print("\n[real] reads against the locked gateway")
    for name, fn, kwargs in checks:
        try:
            ret, data = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 - the outcome is the observation
            reads[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            print(f"  {name:<24} RAISED {type(exc).__name__}: {exc}")
            continue
        ok = ret == RET_OK
        reads[name] = {"ok": ok, "message": None if ok else str(data)}
        print(f"  {name:<24} {'ok' if ok else 'FAILED: ' + str(data)}")
        if not ok:
            report.fail(
                f"{name} fails while locked; it needs a just-in-time unlock before "
                "startup unlock is removed (add a task in group 5)"
            )
    report.observations["real_reads_while_locked"] = reads
    report.note(
        "acctradinginfo_query (get_max_tradable) and comboorder_tradinginfo_query "
        "(preview_combo_order) need an order-shaped argument set, so run them from "
        "the MCP tools against this same locked gateway and record the outcome here."
    )


# --- wiring ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "phase",
        choices=[
            "instruments",
            "positions",
            "paper-retention",
            "paper-retention-recheck",
            "real-reads",
        ],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument(
        "--security-firm",
        default="FUTUINC",
        choices=[n for n in dir(SecurityFirm) if n.isupper() and n != "NONE"],
    )
    parser.add_argument("--stock", default="", help="task 1.1: a US stock code")
    parser.add_argument("--etf", default="", help="task 1.1: a US ETF code")
    parser.add_argument("--option", default="", help="task 1.1: a US equity option")
    parser.add_argument(
        "--quiet-option",
        default="",
        help="task 1.1: an option with no trades today, to capture absent quotes",
    )
    parser.add_argument("--order-code", default="", help="code for the paper order")
    parser.add_argument(
        "--order-price",
        type=float,
        default=0.0,
        help="limit price, far from the market",
    )
    parser.add_argument("--order-qty", type=int, default=1)
    parser.add_argument("--order-id", default="", help="for the after-close recheck")
    parser.add_argument(
        "--trd-env",
        default=TrdEnv.SIMULATE,
        choices=[TrdEnv.SIMULATE, TrdEnv.REAL],
        help="positions phase only; reads, never writes",
    )
    parser.add_argument(
        "--password-md5", default="", help="real-reads: the trade password's MD5"
    )
    parser.add_argument("--json-out", default="", help="write the record here")
    parser.add_argument(
        "--i-authorize-paper-orders",
        action="store_true",
        help="required for paper-retention: it places and cancels one SIMULATE order",
    )
    parser.add_argument(
        "--i-authorize-real-gateway-lock",
        action="store_true",
        help="required for real-reads: it changes the REAL gateway's lock state",
    )
    return parser


def refusal(args: argparse.Namespace) -> str:
    """Why this run must not proceed, or an empty string.

    Every guard lives here, and `main` consults it before opening any connection.
    Checking after connecting would mean an unauthorized run still reaches out to
    the gateway -- and, with no gateway listening, sit in the SDK's endless connect
    retry instead of refusing.
    """
    if args.phase == "paper-retention":
        if not args.i_authorize_paper_orders:
            return (
                "paper-retention places and cancels one SIMULATE order. Re-run with "
                "--i-authorize-paper-orders once that is authorized."
            )
        if not args.order_code or args.order_price <= 0:
            return (
                "--order-code and a positive --order-price are required, and the "
                "price must be far from the market."
            )
    if args.phase == "paper-retention-recheck" and not (
        args.order_id and args.order_code
    ):
        return "--order-id and --order-code are required."
    if args.phase == "real-reads":
        if not args.i_authorize_real_gateway_lock:
            return (
                "real-reads locks the REAL gateway. Re-run with "
                "--i-authorize-real-gateway-lock once that is authorized."
            )
        if not args.password_md5:
            return "--password-md5 is required to lock."
    if args.phase == "instruments" and not (args.stock or args.etf or args.option):
        return "pass at least one of --stock, --etf and --option."
    return ""


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    why = refusal(args)
    if why:
        print(f"refusing: {why}", file=sys.stderr)
        return 2

    report = Report(phase=args.phase)
    needs_trade = args.phase != "instruments"
    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: OpenSecTradeContext | None = None

    try:
        if args.phase in ("instruments", "positions"):
            quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
        if needs_trade:
            trade_ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.US,
                host=args.host,
                port=args.port,
                security_firm=getattr(SecurityFirm, args.security_firm),
            )

        if args.phase == "instruments":
            assert quote_ctx is not None
            phase_instruments(
                quote_ctx,
                {
                    "stock": args.stock,
                    "etf": args.etf,
                    "option": args.option,
                    "quiet_option": args.quiet_option,
                },
                report,
            )
        elif args.phase == "positions":
            assert quote_ctx is not None and trade_ctx is not None
            phase_positions(quote_ctx, trade_ctx, args.trd_env, report)
        elif args.phase == "paper-retention":
            assert trade_ctx is not None
            phase_paper_retention(
                trade_ctx, args.order_code, args.order_price, args.order_qty, report
            )
        elif args.phase == "paper-retention-recheck":
            assert trade_ctx is not None
            phase_paper_retention_recheck(
                trade_ctx, args.order_id, args.order_code, report
            )
        elif args.phase == "real-reads":
            assert trade_ctx is not None
            phase_real_reads(trade_ctx, args.password_md5, report)
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()

    record = {
        "phase": report.phase,
        "observations": report.observations,
        "notes": report.notes,
        "failures": report.failures,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, default=str)
        print(f"\nrecord written to {args.json_out}")

    print(f"\n{len(report.failures)} failure(s)")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
