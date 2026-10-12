"""Commentary traceability: every number must come from the saved analysis outputs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from financial_analysis.cli import main as run_analysis
from financial_analysis.output import analysis_id, source_line
from financial_analysis.trace import commentary_source, main as trace_main, source_problems, untraced_numbers

from helpers import make_workbook, months_from

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / ".claude" / "skills" / "monthly-trend-analysis" / "scripts" / "check_commentary.py"

# Jan 2025 .. Jun 2026. Total 23,600; largest MoM increase Nov 2025 +400 (+40.0%), decrease Jun 2025 -300.
SALES_18 = [1000, 1100, 1050, 1200, 1200, 900, 950, 1300, 1250, 1000, 1400, 1500,
            1450, 1600, 1550, 1700, 1650, 1800]

TRACED = """# Review, Jan 2025 to Jun 2026

## 1. Revenue

Revenue totalled 23,600 over the 18 months (2025-01 to 2026-06). The largest month-over-month
increase was Nov 2025, +400 (+40.0%), from 1,000 to 1,400; the largest decrease was Jun 2025,
-300 (-25.0%). TTM revenue to June 2026 was 17,150.
"""


@pytest.fixture
def out(tmp_path, capsys):
    wb = make_workbook(tmp_path / "Client.xlsx", months_from(2025, 1, 18), [
        (0, "Income", None), (1, "Sales", SALES_18), (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Operating Expenses", None), (1, "Rent", [10] * 18), (0, "Total Operating Expenses", "=SUM({c}11:{c}11)"),
    ])
    folder = tmp_path / "out"
    assert run_analysis([str(wb), "--out-dir", str(folder), "--quiet"]) == 0
    capsys.readouterr()
    return folder


def outputs(folder):
    return (json.loads((folder / "analysis.json").read_text(encoding="utf-8")),
            (folder / "brief.md").read_text(encoding="utf-8"))


def with_source(text, folder):
    """The commentary as the skill writes it: ending with the source line printed in brief.md."""
    return text + "\n" + source_line(outputs(folder)[0]) + "\n"


def test_commentary_built_from_saved_outputs_traces(out):
    assert untraced_numbers(TRACED, *outputs(out)) == []


def test_figures_not_in_the_saved_outputs_are_reported(out):
    text = TRACED + "\nA side calculation gave 12,345 and growth of 61.9%.\n"
    assert [token for token, _ in untraced_numbers(text, *outputs(out))] == ["12,345", "61.9"]


def test_headings_years_iso_months_and_list_numbers_are_ignored(out):
    text = "# 2026 review 999\n\n1. In 2025 and 2026 (2025-11, 2026-06-30) revenue rose.\n2. See item 3.\n"
    assert untraced_numbers(text, *outputs(out)) == []


def test_trace_cli_exit_codes(out, capsys):
    (out / "commentary.md").write_text(with_source(TRACED, out), encoding="utf-8")
    assert trace_main([str(out)]) == 0
    assert "0 untraced number(s)" in capsys.readouterr().out

    (out / "commentary.md").write_text(with_source(TRACED + "Cash burn was 98,765.\n", out), encoding="utf-8")
    assert trace_main([str(out)]) == 3
    assert "UNTRACED 98,765" in capsys.readouterr().out

    assert trace_main([str(out / "missing-folder")]) == 1


def test_skill_check_script_runs_from_any_directory(out, tmp_path):
    (out / "commentary.md").write_text(with_source(TRACED + "Unsupported: 4,242.\n", out), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = subprocess.run([sys.executable, str(CHECK_SCRIPT), str(out)], cwd=elsewhere,
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 3, proc.stderr
    assert "UNTRACED 4,242" in proc.stdout


# --- provenance: the commentary names the analysis it was written from --------------------------

def test_brief_prints_the_source_line_for_the_saved_analysis(out):
    analysis, brief = outputs(out)
    aid = analysis_id(analysis)
    assert len(aid) == 64
    assert f"`Source: analysis 2026-06 · workbook sha256 {analysis['source']['sha256']} · analysis_id {aid}`" in brief


def test_matching_source_line_passes_and_its_hashes_are_not_figures(out):
    analysis, brief = outputs(out)
    text = with_source(TRACED, out)
    assert source_problems(text, analysis) == []
    assert untraced_numbers(text, analysis, brief) == []  # the digits inside the two hashes are ignored
    assert commentary_source(text) == {"analysis_period": "2026-06", "workbook_sha256": analysis["source"]["sha256"],
                                       "analysis_id": analysis_id(analysis)}


def test_missing_wrong_period_wrong_hash_or_duplicate_source_line_fails(out, capsys):
    analysis, _ = outputs(out)
    good = source_line(analysis)
    assert source_problems(TRACED, analysis)[0].startswith("no source line")
    assert source_problems(TRACED + good.replace("analysis 2026-06", "analysis 2026-05"), analysis) == [
        "analysis_period is 2026-05, but the saved analysis has 2026-06"]
    assert source_problems(TRACED + good.replace(analysis["source"]["sha256"], "0" * 64), analysis) == [
        f"workbook_sha256 is {'0' * 64}, but the saved analysis has {analysis['source']['sha256']}"]
    wrong_id = good.replace(analysis_id(analysis), "f" * 64)
    assert [p.split(" is ")[0] for p in source_problems(TRACED + wrong_id, analysis)] == ["analysis_id"]
    assert source_problems(TRACED + good + "\n" + good, analysis) == ["more than one source line"]
    assert commentary_source(TRACED + good + "\n" + good) is None

    # The CLI reports a source problem with exit 4, even when every number traces.
    (out / "commentary.md").write_text(TRACED + wrong_id + "\n", encoding="utf-8")
    assert trace_main([str(out)]) == 4
    printed = capsys.readouterr().out
    assert "SOURCE analysis_id is" in printed and "does not match" in printed


def test_source_line_from_an_earlier_analysis_fails_after_the_analysis_changes(out, tmp_path, capsys):
    text = with_source(TRACED, out)
    assert source_problems(text, outputs(out)[0]) == []
    # Same file name and period, one figure changed (Jun 2026 1800 -> 1801).
    wb = make_workbook(tmp_path / "Client.xlsx", months_from(2025, 1, 18), [
        (0, "Income", None), (1, "Sales", SALES_18[:-1] + [1801]), (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Operating Expenses", None), (1, "Rent", [10] * 18), (0, "Total Operating Expenses", "=SUM({c}11:{c}11)"),
    ])
    assert run_analysis([str(wb), "--out-dir", str(out), "--quiet"]) == 0
    capsys.readouterr()
    assert [p.split(" is ")[0] for p in source_problems(text, outputs(out)[0])] == ["workbook_sha256", "analysis_id"]
