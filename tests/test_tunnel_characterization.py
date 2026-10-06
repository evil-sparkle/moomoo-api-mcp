"""Reporting must distinguish upstream findings from broken fixture execution."""

import json

import pytest

from scripts.report_tunnel_characterization import report


def fixture_reports(tmp_path, *, status="FAIL"):
    redirects = [
        {
            "scenario": scenario,
            "control_plane_contacted": True,
            "redirect_sink_contacted": True,
            "runtime_key_leaked": True,
            "client_exited_early": False,
            "shutdown_required_kill": False,
        }
        for scenario in ("changed-port", "changed-host-and-port")
    ]
    redirect = tmp_path / "redirect.jsonl"
    redirect.write_text(
        "\n".join(json.dumps(row) for row in redirects) + "\nFAIL: diversion\n"
    )
    rows = [{"case": "case-1", "status": status, "fixture_errors": 0}]
    summary = {
        "summary": {
            verdict: int(verdict == status)
            for verdict in ("PASS", "FAIL", "INCONCLUSIVE")
        },
        "total": 1,
    }
    matrix = tmp_path / "matrix.jsonl"
    matrix.write_text("\n".join(json.dumps(row) for row in [*rows, summary]) + "\n")
    return redirect, matrix


@pytest.mark.parametrize("status", ["PASS", "FAIL", "INCONCLUSIVE"])
def test_valid_diagnostic_report_preserves_all_verdicts(tmp_path, status):
    redirect, matrix = fixture_reports(tmp_path, status=status)
    text = report(redirect, 1, matrix, int(status != "PASS"), {"case-1"})
    assert "0 PASS / 2 FAIL" in text
    assert f"1 {status}" in text
    assert "not a passing security verdict" in text


@pytest.mark.parametrize("runner", ["redirect", "matrix"])
def test_unexpected_runner_exit_is_not_treated_as_an_upstream_finding(tmp_path, runner):
    redirect, matrix = fixture_reports(tmp_path)
    with pytest.raises(ValueError, match="execute"):
        report(
            redirect,
            125 if runner == "redirect" else 1,
            matrix,
            2 if runner == "matrix" else 1,
            {"case-1"},
        )


@pytest.mark.parametrize(
    "damage",
    ["fixture-error", "missing", "duplicate", "unknown", "summary", "status", "json"],
)
def test_untrustworthy_matrix_evidence_fails_reporting(tmp_path, damage):
    redirect, matrix = fixture_reports(tmp_path)
    rows = [json.loads(line) for line in matrix.read_text().splitlines()]
    if damage == "fixture-error":
        rows[0]["fixture_errors"] = 1
    elif damage == "missing":
        rows.pop(0)
    elif damage == "duplicate":
        rows.insert(0, rows[0])
    elif damage == "unknown":
        rows[0]["case"] = "unexpected"
    elif damage == "summary":
        rows[-1]["summary"]["FAIL"] = 0
    elif damage == "status":
        rows[0]["status"] = "SKIP"
    matrix.write_text(
        "not-json" if damage == "json" else "\n".join(json.dumps(row) for row in rows)
    )
    with pytest.raises(ValueError):
        report(redirect, 1, matrix, 1, {"case-1"})


def test_exit_status_cannot_disagree_with_reported_verdicts(tmp_path):
    redirect, matrix = fixture_reports(tmp_path)
    with pytest.raises(ValueError, match="exit status"):
        report(redirect, 1, matrix, 0, {"case-1"})


def test_incomplete_redirect_execution_fails_reporting(tmp_path):
    redirect, matrix = fixture_reports(tmp_path)
    lines = redirect.read_text().splitlines()
    row = json.loads(lines[0])
    row["control_plane_contacted"] = False
    lines[0] = json.dumps(row)
    redirect.write_text("\n".join(lines))
    with pytest.raises(ValueError, match="did not complete"):
        report(redirect, 1, matrix, 1, {"case-1"})
