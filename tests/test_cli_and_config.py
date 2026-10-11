"""Command-line behaviour, config validation and failed-run output safety.

Every workbook here is a small synthetic file in a temporary directory. Expected
values are worked out by hand in the comments.
"""

import json

import pytest

from financial_analysis.cli import main
from financial_analysis.config import Config, ConfigError
from financial_analysis.pipeline import analyze

from helpers import make_workbook

MONTHS = ["Jan 2026", "Feb 2026", "Mar 2026"]


def rows(opex_total="=SUM({c}14:{c}15)", sales=(100, 110, 120)):
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
        (0, "Total Operating Expenses", opex_total),             # 16
    ]


def write_config(tmp_path, data, name="cfg.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    return code, capsys.readouterr().err


def snapshot(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir()}


# --- unreadable workbooks: exit 1, one readable line, nothing written --------------------------

@pytest.mark.parametrize("name,content", [
    ("corrupt.xlsx", b"this is not a zip file"),
    ("truncated.xlsx", b"PK\x03\x04 truncated"),
    ("export.csv", b"Account,Jan 2026\nSales,100\n"),
])
def test_unreadable_workbook_fails_cleanly(tmp_path, capsys, name, content):
    wb = tmp_path / name
    wb.write_bytes(content)
    out = tmp_path / "out"
    code, err = run(capsys, wb, "--out-dir", out, "--quiet")
    assert code == 1
    assert err.startswith(f"ERROR: Could not open workbook {name}")
    assert "Traceback" not in err
    assert not out.exists()


def test_missing_workbook_fails_cleanly(tmp_path, capsys):
    code, err = run(capsys, tmp_path / "nope.xlsx", "--quiet", "--out-dir", tmp_path / "out")
    assert code == 1 and err.startswith("ERROR: Workbook not found")


# --- config validation: exit 1 with the offending key named -------------------------------------

@pytest.mark.parametrize("config,expected", [
    ({"income_statment": {}}, "unknown key 'income_statment'. Did you mean 'income_statement'?"),
    ({"income_statement": {"opex_rollup": []}}, "unknown key 'income_statement.opex_rollup'"),
    ({"income_statement": {"account_classes": {"non_operatng": ["Rent"]}}},
     "unknown key 'income_statement.account_classes.non_operatng'"),
    ({"income_statement": {"opex_rollups": [{"name": "Bad", "match": ["(unclosed"]}]}},
     "'income_statement.opex_rollups[0].match' ('Bad'): invalid regular expression '(unclosed'"),
    ({"income_statement": {"opex_rollups": [{"name": "Bad", "match": "> Rent$"}]}},
     "must be a non-empty list of regex strings"),
    ({"income_statement": {"opex_rollups": [{"name": "X", "match": ["a"], "matches": ["b"]}]}},
     "has unknown key(s) ['matches']"),
    ({"thresholds": {"materiality_abs": "5000"}}, "'thresholds.materiality_abs' must be a non-negative number"),
    ({"thresholds": {"max_highlights": 2.5}}, "'thresholds.max_highlights' must be a whole number"),
    ({"blank_cells_as_zero": "yes"}, "'blank_cells_as_zero' must be true or false"),
    ({"fiscal_year_start_month": 13}, "'fiscal_year_start_month' must be a whole number from 1 to 12"),
    ({"income_statement": {"sheet_names": "P&L"}}, "'income_statement.sheet_names' must be a list of strings"),
    ({"currency": 840}, "'currency' must be a string"),
    ("{not json", "not valid JSON"),
    ("[1, 2]", "the top level must be a JSON object"),
])
def test_invalid_config_is_rejected_with_the_key_named(tmp_path, capsys, config, expected):
    cfg = write_config(tmp_path, config)
    with pytest.raises(ConfigError) as exc:
        Config.load(cfg)
    assert expected in str(exc.value) and str(cfg) in str(exc.value)

    wb = make_workbook(tmp_path / "wb.xlsx", MONTHS, rows())
    code, err = run(capsys, wb, "--config", cfg, "--out-dir", tmp_path / "out", "--quiet")
    assert code == 1 and err.startswith("ERROR: Invalid company config") and expected in err
    assert "Traceback" not in err and not (tmp_path / "out").exists()


def test_valid_configs_still_load(tmp_path):
    assert Config.load(write_config(tmp_path, {
        "_comment": "comments are allowed at the top level",
        "company_name": None, "fiscal_year_start_month": 4, "blank_cells_as_zero": False,
        "thresholds": {"materiality_abs": 2500.5, "max_highlights": 10},
        "income_statement": {"opex_rollups": [{"name": "People", "match": ["> Payroll >", "Wages$"]}]},
    })).fiscal_year_start_month == 4


# --- category rules that match nothing: a warning, not an error --------------------------------

def test_category_rule_matching_no_account_warns(tmp_path):
    cfg = Config.load(write_config(tmp_path, {"income_statement": {"opex_rollups": [
        {"name": "Facilities", "match": ["> Rent$"]},
        {"name": "Travel", "match": ["> Travel$"]},      # no Travel account in this workbook
        {"name": "Tools", "match": ["> Software$"]},
    ]}}))
    r = analyze(make_workbook(tmp_path / "wb.xlsx", MONTHS, rows()), cfg)
    warning = [i for i in r["validation"]["issues"] if i["code"] == "empty_opex_category"]
    assert len(warning) == 1 and warning[0]["severity"] == "warning" and warning[0]["account"] == "Travel"
    assert r["status"] == "pass_with_warnings"  # usable, but the reviewer must look
    cats = {c["name"]: c for c in r["income_statement"]["opex_categories"]}
    assert cats["Travel"]["accounts"] == [] and cats["Travel"]["values"]["2026-01"] == 0.0
    assert cats["Facilities"]["values"]["2026-01"] == 50.0 and cats["Tools"]["values"]["2026-01"] == 10.0
    check = next(c for c in r["validation"]["checks"] if c["check"] == "opex_categories_complete")
    assert check["status"] == "pass"  # 50 + 0 + 10 = 60 = total opex


def test_no_empty_category_warning_when_every_rule_matches(tmp_path):
    cfg = Config.load(write_config(tmp_path, {"income_statement": {"opex_rollups": [
        {"name": "Facilities", "match": ["> Rent$"]}, {"name": "Tools", "match": ["> Software$"]}]}}))
    r = analyze(make_workbook(tmp_path / "wb.xlsx", MONTHS, rows()), cfg)
    assert "empty_opex_category" not in {i["code"] for i in r["validation"]["issues"]}
    assert r["status"] == "pass"


# --- exit codes and failed-run output safety ------------------------------------------------------

def test_exit_codes_and_outputs_for_a_passing_run(tmp_path, capsys):
    out = tmp_path / "out"
    code, err = run(capsys, make_workbook(tmp_path / "Client.xlsx", MONTHS, rows()), "--out-dir", out, "--quiet")
    assert code == 0 and "status=pass " in err
    assert sorted(p.name for p in out.iterdir()) == ["analysis.json", "brief.md", "last_run.json"]
    last = json.loads((out / "last_run.json").read_text(encoding="utf-8"))
    assert last["status"] == "pass" and last["exit_code"] == 0 and last["stale_earlier_outputs"] == []


def test_failed_run_preserves_earlier_valid_outputs(tmp_path, capsys):
    wb, out = tmp_path / "Client.xlsx", tmp_path / "out"
    make_workbook(wb, MONTHS, rows())
    original = wb.read_bytes()  # rebuilding would change the file's timestamps and hash, so keep the exact bytes
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 0
    (out / "commentary.md").write_text("commentary from the passing run\n", encoding="utf-8")
    before = snapshot(out)

    # Same file name, but the reported total is now 5 too high (60 + 5), so subtotal and validation fail.
    make_workbook(wb, MONTHS, rows(opex_total="=SUM({c}14:{c}15)+5"))
    code, err = run(capsys, wb, "--out-dir", out, "--quiet")
    assert code == 2
    assert "VALIDATION FAILED" in err and "treat them as stale" in err

    after = snapshot(out)
    for name in ("analysis.json", "brief.md", "commentary.md"):
        assert after[name] == before[name], f"{name} was modified by a failed run"
    failed = json.loads(after["analysis.failed.json"])
    assert failed["status"] == "fail"
    assert "subtotal_mismatch" in {i["code"] for i in failed["validation"]["issues"]}
    assert b"**Status: FAIL**" in after["brief.failed.md"]
    last = json.loads(after["last_run.json"])
    assert last["status"] == "fail" and last["exit_code"] == 2 and last["analysis_json"] == "analysis.failed.json"
    assert last["stale_earlier_outputs"] == ["analysis.json", "brief.md", "commentary.md"]

    # Exit 1 (unreadable workbook) writes nothing, even when given the same folder.
    wb.write_bytes(b"corrupted")
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 1
    assert snapshot(out) == after

    # A later passing run on the original workbook replaces the valid outputs and clears the failure files.
    wb.write_bytes(original)
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 0
    names = {p.name for p in out.iterdir()}
    assert "analysis.failed.json" not in names and "brief.failed.md" not in names
    assert json.loads((out / "last_run.json").read_text(encoding="utf-8"))["status"] == "pass"
    # Same figures as the original valid run, so its commentary still applies and is kept.
    assert (out / "commentary.md").read_bytes() == before["commentary.md"]


def test_first_run_failing_lists_no_stale_outputs(tmp_path, capsys):
    out = tmp_path / "out"
    code, err = run(capsys, make_workbook(tmp_path / "Client.xlsx", MONTHS, rows(opex_total="=SUM({c}14:{c}15)+5")),
                    "--out-dir", out, "--quiet")
    assert code == 2 and "stale" not in err
    assert sorted(p.name for p in out.iterdir()) == ["analysis.failed.json", "brief.failed.md", "last_run.json"]


# --- commentary from an earlier analysis is set aside, never shown as current -------------------

def test_changed_figures_set_aside_old_commentary(tmp_path, capsys):
    wb, out = tmp_path / "Client.xlsx", tmp_path / "out"
    make_workbook(wb, MONTHS, rows())
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 0
    (out / "commentary.md").write_text("March revenue was 120.\n", encoding="utf-8")

    # Still valid, but March sales are now 999, so the commentary describes figures that no longer exist.
    make_workbook(wb, MONTHS, rows(sales=(100, 110, 999)))
    code, err = run(capsys, wb, "--out-dir", out, "--quiet")
    assert code == 0 and "renamed to commentary.stale.md" in err
    assert not (out / "commentary.md").exists()
    assert (out / "commentary.stale.md").read_text(encoding="utf-8") == "March revenue was 120.\n"
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["income_statement"]["monthly"]["revenue"]["values"]["2026-03"] == 999.0
    assert json.loads((out / "last_run.json").read_text(encoding="utf-8"))["commentary_set_aside"] == "commentary.stale.md"

    # A second change never overwrites the first set-aside commentary.
    (out / "commentary.md").write_text("March revenue was 999.\n", encoding="utf-8")
    make_workbook(wb, MONTHS, rows(sales=(100, 110, 500)))
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 0
    assert (out / "commentary.stale.md").read_text(encoding="utf-8") == "March revenue was 120.\n"
    assert (out / "commentary.stale-2.md").read_text(encoding="utf-8") == "March revenue was 999.\n"


def test_unchanged_rerun_keeps_commentary(tmp_path, capsys):
    wb, out = tmp_path / "Client.xlsx", tmp_path / "out"
    make_workbook(wb, MONTHS, rows())
    assert run(capsys, wb, "--out-dir", out, "--quiet")[0] == 0
    (out / "commentary.md").write_text("March revenue was 120.\n", encoding="utf-8")
    code, err = run(capsys, wb, "--out-dir", out, "--quiet")
    assert code == 0 and "renamed" not in err
    assert (out / "commentary.md").read_text(encoding="utf-8") == "March revenue was 120.\n"
    assert not (out / "commentary.stale.md").exists()
    assert json.loads((out / "last_run.json").read_text(encoding="utf-8"))["commentary_set_aside"] is None
