#!/usr/bin/env python3
"""Validate sanitized diagnostic output while preserving upstream FAIL verdicts."""

from __future__ import annotations

import argparse
import json
import runpy
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("PASS", "FAIL", "INCONCLUSIVE")


def report(
    redirect_path: Path,
    redirect_exit: int,
    matrix_path: Path,
    matrix_exit: int,
    expected_cases: set[str],
) -> str:
    if redirect_exit not in {0, 1} or matrix_exit not in {0, 1}:
        raise ValueError("Characterization runner failed to execute.")
    lines = redirect_path.read_text().splitlines()
    if len(lines) != 3 or not lines[-1].startswith(
        "PASS:" if redirect_exit == 0 else "FAIL:"
    ):
        raise ValueError("Incomplete redirect characterization.")
    redirects = [json.loads(line) for line in lines[:-1]]
    if {row["scenario"] for row in redirects} != {
        "changed-port",
        "changed-host-and-port",
    }:
        raise ValueError("Unexpected redirect inventory.")
    boolean_fields = (
        "control_plane_contacted",
        "redirect_sink_contacted",
        "runtime_key_leaked",
        "client_exited_early",
        "shutdown_required_kill",
    )
    for row in redirects:
        if any(type(row[field]) is not bool for field in boolean_fields):
            raise ValueError("Invalid redirect observations.")
        if (
            not row["control_plane_contacted"]
            or row["client_exited_early"]
            or row["shutdown_required_kill"]
        ):
            raise ValueError("Redirect fixture did not complete.")
    redirect_failures = sum(row["runtime_key_leaked"] for row in redirects)
    if redirect_exit != int(bool(redirect_failures)):
        raise ValueError("Redirect verdict disagrees with exit status.")

    records = [json.loads(line) for line in matrix_path.read_text().splitlines()]
    if len(records) != len(expected_cases) + 1:
        raise ValueError("Incomplete matrix characterization.")
    rows, summary = records[:-1], records[-1]
    if {row["case"] for row in rows} != expected_cases:
        raise ValueError("Missing, duplicate or unexpected matrix case.")
    for row in rows:
        if row["status"] not in STATUSES:
            raise ValueError("Invalid matrix verdict.")
        if type(row["fixture_errors"]) is not int or row["fixture_errors"] != 0:
            raise ValueError("Matrix fixture execution error.")
    counts = Counter(row["status"] for row in rows)
    totals = {status: counts[status] for status in STATUSES}
    if summary != {"summary": totals, "total": len(expected_cases)}:
        raise ValueError("Matrix summary disagrees with case results.")
    if matrix_exit != int(any(row["status"] != "PASS" for row in rows)):
        raise ValueError("Matrix verdicts disagree with exit status.")
    return (
        "## Official-client characterization\n\n"
        f"Redirect reproduction: {2 - redirect_failures} PASS / "
        f"{redirect_failures} FAIL.\n\n"
        f"Direct-client matrix ({len(rows)} cases): "
        + " / ".join(f"{totals[status]} {status}" for status in STATUSES)
        + ".\n\n"
        "These are diagnostic verdicts, including upstream behavioral failures. "
        "Successful report validation is not a passing security verdict. "
        "Managed image integration is a separate required job. "
        "No live OpenAI or ChatGPT acceptance is claimed.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redirect-results", type=Path, required=True)
    parser.add_argument("--redirect-exit", type=int, required=True)
    parser.add_argument("--matrix-results", type=Path, required=True)
    parser.add_argument("--matrix-exit", type=int, required=True)
    args = parser.parse_args()
    try:
        fixture = runpy.run_path(
            str(ROOT / "tests/fixtures/tunnel_compatibility_matrix.py")
        )
        expected = {row[0] for row in fixture["case_definitions"]()}
        print(
            report(
                args.redirect_results,
                args.redirect_exit,
                args.matrix_results,
                args.matrix_exit,
                expected,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        # Raw fixture output is never relayed by this error path.
        print("Characterization evidence is incomplete or invalid.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
