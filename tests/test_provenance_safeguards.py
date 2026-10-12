"""Workbook selection, company identity, output identity and commentary provenance.

Every workbook is a small synthetic file in a temporary directory; the supplied workbook
and the repository's out/ folder are only read (never written) by the last tests, which
skip when they are absent. Expected figures are worked out by hand from the inputs.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest

from financial_analysis.cli import main
from financial_analysis.output import analysis_id, source_line
from financial_analysis.trace import main as trace_main

from helpers import make_workbook

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "monthly-trend-analysis" / "scripts"
ABC_WORKBOOK = ROOT / "data" / "ABC Inc - Financial Statements (1) (1).xlsx"
ABC_CONFIG = ROOT / "config" / "companies" / "abc-inc.json"
MONTHS = ["Jan 2026", "Feb 2026", "Mar 2026"]


def statement(sales):
    return [
        (0, "Income", None),                                     # 7
        (1, "Sales", list(sales)),                               # 8
        (0, "Total Income", "=SUM({c}8:{c}8)"),                  # 9
        (0, "Cost of Goods Sold", None),                         # 10
        (1, "Materials", [20, 20, 20]),                          # 11
        (0, "Total Cost of Goods Sold", "=SUM({c}11:{c}11)"),    # 12
        (0, "Operating Expenses", None),                         # 13
        (1, "Rent", [50, 50, 50]),                               # 14
        (1, "Software", [10, 10, 10]),                           # 15
        (0, "Total Operating Expenses", "=SUM({c}14:{c}15)"),    # 16
    ]


def workbook(path, sales=(100, 110, 120), company=None):
    """A statement workbook; with `company`, the title reads statement / company / period like an export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    make_workbook(path, MONTHS, statement(sales))
    if company is not None:
        wb = openpyxl.load_workbook(path)
        wb.active["A2"], wb.active["A3"] = company, "For the month ended March 31, 2026"
        wb.save(path)
    return path


def config(tmp_path, data, name="company.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    return code, capsys.readouterr().err


def load(folder, name="analysis.json"):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def revenue(analysis, month="2026-03"):
    return analysis["income_statement"]["monthly"]["revenue"]["values"][month]


def write_commentary(folder, text="March revenue was 120.\n"):
    """Commentary as the skill writes it: ending with the source line of the folder's analysis."""
    (folder / "commentary.md").write_text(text + "\n" + source_line(load(folder)) + "\n", encoding="utf-8")


def trace(capsys, folder):
    code = trace_main([str(folder)])
    return code, capsys.readouterr().out


# --- 1. one eligible workbook: selected deterministically --------------------------------------

def test_single_workbook_in_input_dir_is_selected(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    wb = workbook(data / "Only.xlsx")
    (data / "~$Only.xlsx").write_bytes(b"Excel lock file")  # ignored: Office lock file
    (data / "notes.csv").write_text("not a workbook", encoding="utf-8")  # ignored: not Excel
    (data / "archive").mkdir()  # ignored: folders are not searched
    workbook(data / "archive" / "Old.xlsx")

    code, err = run(capsys, "--input-dir", data, "--out-dir", out, "--quiet")
    assert code == 0 and "using the only workbook" in err and "Only.xlsx" in err
    analysis = load(out)
    assert analysis["source"]["workbook"] == "Only.xlsx" and analysis["source"]["sha256"] == sha(wb)
    assert revenue(analysis) == 120.0  # Sales in March
    assert load(out, "last_run.json")["workbook_path"] == str(wb.resolve())


def test_no_workbook_and_no_input_dir_is_refused(tmp_path, capsys):
    code, err = run(capsys, "--out-dir", tmp_path / "out", "--quiet")
    assert code == 1 and "No workbook given" in err and not (tmp_path / "out").exists()


# --- 2. several eligible workbooks and no explicit choice: stop, list them ---------------------

def test_multiple_workbooks_without_selection_stop(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    for name in ("b-second.xlsx", "A-first.xlsm", "c-third.xlsx"):
        workbook(data / name)
    code, err = run(capsys, "--input-dir", data, "--out-dir", out, "--quiet")
    assert code == 1 and "3 workbooks found" in err and "none was selected" in err
    # Every candidate is listed (in name order, for reading only); none was analysed.
    assert err.index("A-first.xlsm") < err.index("b-second.xlsx") < err.index("c-third.xlsx")
    assert not out.exists()


# --- 3. explicit selection among several: exactly that file is analysed ------------------------

def test_explicit_selection_among_multiple_is_the_one_analysed(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    workbook(data / "A.xlsx", sales=(100, 110, 120))
    chosen = workbook(data / "B.xlsx", sales=(200, 230, 260))
    code, _ = run(capsys, chosen, "--input-dir", data, "--out-dir", out, "--quiet")
    assert code == 0
    analysis = load(out)
    assert analysis["source"]["workbook"] == "B.xlsx" and analysis["source"]["sha256"] == sha(chosen)
    assert revenue(analysis) == 260.0
    mom = next(m for m in analysis["analysis_period"]["metrics"] if m["metric"] == "revenue")["mom"]
    assert mom["abs_change"] == 30.0                        # 260 - 230
    assert mom["pct_change"] == round(30 / 230 * 100, 2)    # 13.04
    gross = next(m for m in analysis["analysis_period"]["metrics"] if m["metric"] == "gross_profit")
    assert gross["value"] == 240.0                          # 260 - 20


@pytest.mark.parametrize("make,message", [
    (lambda d, t: workbook(t / "elsewhere" / "Outside.xlsx"), "outside the permitted input folder"),
    (lambda d, t: d / "Missing.xlsx", "Workbook not found"),
    (lambda d, t: (d / "folder.xlsx").mkdir() or d / "folder.xlsx", "not a file"),
    (lambda d, t: (d / "export.csv").write_text("x") and d / "export.csv", "Not an Excel workbook"),
])
def test_invalid_explicit_workbook_is_refused(tmp_path, capsys, make, message):
    data, out = tmp_path / "data", tmp_path / "out"
    workbook(data / "Valid.xlsx")
    code, err = run(capsys, make(data, tmp_path), "--input-dir", data, "--out-dir", out, "--quiet")
    assert code == 1 and message in err and not out.exists()


# --- 6. company config must match the workbook's company, or be explicitly confirmed ------------

ABC = {"company_name": "ABC Inc"}


def test_matching_company_passes(tmp_path, capsys):
    wb, out = workbook(tmp_path / "data" / "Abc.xlsx", company="ABC, Inc."), tmp_path / "out"
    code, _ = run(capsys, wb, "--config", config(tmp_path, ABC), "--require-config", "--out-dir", out, "--quiet")
    assert code == 0
    check = load(out, "last_run.json")["company_check"]
    assert check["result"] == "matched_workbook_title" and check["workbook_company"] == "ABC, Inc."


def test_company_mismatch_is_rejected(tmp_path, capsys):
    wb, out = workbook(tmp_path / "data" / "Xyz.xlsx", company="XYZ Corp"), tmp_path / "out"
    code, err = run(capsys, wb, "--config", config(tmp_path, ABC), "--out-dir", out, "--quiet")
    assert code == 1 and "Company mismatch" in err and "'ABC Inc'" in err and "'XYZ Corp'" in err
    assert not out.exists()


def test_company_mismatch_proceeds_only_with_explicit_confirmation(tmp_path, capsys):
    wb, out = workbook(tmp_path / "data" / "Xyz.xlsx", company="XYZ Corp"), tmp_path / "out"
    cfg = config(tmp_path, {**ABC, "income_statement": {"account_classes": {"non_operating": ["Interest Expense"]}}})
    # Confirming some other company is not a confirmation of this config.
    code, err = run(capsys, wb, "--config", cfg, "--confirm-company", "XYZ Corp", "--out-dir", out, "--quiet")
    assert code == 1 and "does not match the config's company_name" in err and not out.exists()

    code, err = run(capsys, wb, "--config", cfg, "--confirm-company", "abc inc", "--out-dir", out, "--quiet")
    assert code == 0 and "confirmed by the user" in err
    assert load(out, "last_run.json")["company_check"] == {
        "config_company": "ABC Inc", "workbook_company": "XYZ Corp", "confirmed_company": "abc inc",
        "result": "confirmed_by_user"}
    # Account mappings are still validated with the existing convention: the configured account is absent.
    issues = load(out)["validation"]["issues"]
    assert any(i["code"] == "config_account_not_found" and "Interest Expense" in i["message"] for i in issues)


def test_workbook_without_company_requires_confirmation(tmp_path, capsys):
    wb, out = workbook(tmp_path / "data" / "Untitled.xlsx"), tmp_path / "out"  # title has no company line
    cfg = config(tmp_path, ABC)
    code, err = run(capsys, wb, "--config", cfg, "--out-dir", out, "--quiet")
    assert code == 1 and "does not name a company" in err and '--confirm-company "ABC Inc"' in err
    assert not out.exists()
    assert run(capsys, wb, "--config", cfg, "--confirm-company", "ABC Inc", "--out-dir", out, "--quiet")[0] == 0


def test_require_config_needs_an_explicit_choice(tmp_path, capsys):
    wb, out = workbook(tmp_path / "data" / "Client.xlsx"), tmp_path / "out"
    code, err = run(capsys, wb, "--require-config", "--out-dir", out, "--quiet")
    assert code == 1 and "No company config given" in err and not out.exists()
    # A config that names no company cannot be checked against the workbook.
    code, err = run(capsys, wb, "--require-config", "--config", config(tmp_path, {"currency": "USD"}),
                    "--out-dir", out, "--quiet")
    assert code == 1 and "has no company_name" in err and not out.exists()
    # --no-config is an explicit choice (onboarding): it runs, and records that no company was checked.
    assert run(capsys, wb, "--require-config", "--no-config", "--out-dir", out, "--quiet")[0] == 0
    assert load(out, "last_run.json")["company_check"]["result"] == "not_checked_no_company_in_config"


# --- 8. normal analysis and commentary validation still pass -----------------------------------

def test_normal_run_and_commentary_check_pass(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    wb = workbook(data / "Abc.xlsx", company="ABC Inc")
    code, _ = run(capsys, wb, "--config", config(tmp_path, ABC), "--input-dir", data, "--require-config",
                  "--out-dir", out, "--quiet")
    assert code == 0
    analysis = load(out)
    assert analysis["status"] == "pass" and analysis["company_name"] == "ABC Inc"
    metrics = {m["metric"]: m["value"] for m in analysis["analysis_period"]["metrics"]}
    assert metrics["revenue"] == 120.0 and metrics["gross_profit"] == 100.0       # 120 - 20
    assert metrics["opex_total"] == 60.0 and metrics["operating_income"] == 40.0  # 50 + 10; 100 - 60
    write_commentary(out, "March revenue was 120 and gross profit 100; operating income was 40.\n")
    assert trace(capsys, out)[0] == 0


def test_skill_runner_enforces_the_input_policy(tmp_path):
    """The runner fixes --input-dir to the repository's data/ folder and requires a config choice.
    Each case stops before anything is written (no out/ folder is created for these names)."""
    outside = workbook(tmp_path / "Runner Probe Outside.xlsx")
    cases = [
        ([str(outside)], "No company config given"),
        ([str(outside), "--no-config"], "outside the permitted input folder"),
        ([str(outside), "--no-config", "--input-dir", str(tmp_path)], "--input-dir cannot be set"),
    ]
    for args, message in cases:
        proc = subprocess.run([sys.executable, str(SKILL_SCRIPTS / "run_analysis.py"), *args, "--quiet"],
                              capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 1 and message in proc.stderr, (args, proc.stderr)
    assert not (ROOT / "out" / outside.stem).exists()


@pytest.mark.skipif(not ABC_WORKBOOK.exists(), reason="supplied workbook not present (client data, not committed)")
def test_supplied_workbook_passes_the_company_check(tmp_path, capsys):
    before = sha(ABC_WORKBOOK)
    out = tmp_path / "out"
    code, _ = run(capsys, ABC_WORKBOOK, "--config", ABC_CONFIG, "--input-dir", ROOT / "data", "--require-config",
                  "--out-dir", out, "--quiet")
    assert code == 0
    assert load(out, "last_run.json")["company_check"]["result"] == "matched_workbook_title"
    assert sha(ABC_WORKBOOK) == before  # read only


EXISTING_OUT = ROOT / "out" / ABC_WORKBOOK.stem / "2026-06"


@pytest.mark.skipif(not (EXISTING_OUT / "commentary.md").exists(), reason="no saved commentary for the supplied workbook")
def test_existing_saved_commentary_still_validates(capsys):
    assert trace(capsys, EXISTING_OUT)[0] == 0  # read only
