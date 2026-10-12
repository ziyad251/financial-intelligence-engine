"""Invalid required values, unexpected accounts and formulas without Excel's cached values.

Every workbook is a small synthetic file in a temporary directory (the supplied workbook is only
read, by the last test, which skips when it is absent). Expected figures are worked out by hand
from the inputs below, never read back from the pipeline.

Layout (rows 7-19), months in columns C (Jan), D (Feb), E (Mar):
    Income: Sales 1000/1100/1200, Services 200/210/220  -> revenue 1200 / 1310 / 1420
    Cost of Goods Sold: Materials 300/330/360           -> gross profit 900 / 980 / 1060
    Operating Expenses: Rent 400, Software 50/60/70     -> opex 450 / 460 / 470
"""

import json
from pathlib import Path

import pytest

from financial_analysis.cli import main
from financial_analysis.config import Config
from financial_analysis.output import render_brief, source_line
from financial_analysis.pipeline import analyze
from financial_analysis.trace import main as trace_main

from helpers import make_workbook

ROOT = Path(__file__).resolve().parents[1]
ABC_WORKBOOK = ROOT / "data" / "ABC Inc - Financial Statements (1) (1).xlsx"
MONTHS = ["Jan 2026", "Feb 2026", "Mar 2026"]
SALES, SERVICES, MATERIALS, RENT, SOFTWARE = [1000, 1100, 1200], [200, 210, 220], [300, 330, 360], [400] * 3, [50, 60, 70]


def rows(sales=SALES, services=SERVICES, extra_opex=()):
    opex_rows = [(1, "Rent", RENT), (1, "Software", SOFTWARE), *extra_opex]
    last = 15 + len(opex_rows) - 1
    return [
        (0, "Income", None),                                       # 7
        (1, "Sales", list(sales)),                                 # 8
        (1, "Services", list(services)),                           # 9
        (0, "Total Income", "=SUM({c}8:{c}9)"),                    # 10
        (0, "Cost of Goods Sold", None),                           # 11
        (1, "Materials", MATERIALS),                               # 12
        (0, "Total Cost of Goods Sold", "=SUM({c}12:{c}12)"),      # 13
        (0, "Operating Expenses", None),                           # 14
        *opex_rows,                                                # 15..
        (0, "Total Operating Expenses", f"=SUM({{c}}15:{{c}}{last})"),
    ]


def config(tmp_path, data):
    path = tmp_path / "company.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return Config.load(path), path


ROLLUPS = {"income_statement": {"opex_rollups": [{"name": "Facilities", "match": ["> Rent$"]},
                                                 {"name": "Tools", "match": ["> Software$"]}]}}


def issues(r, code):
    return [i for i in r["validation"]["issues"] if i["code"] == code]


def metric(r, name):
    return next(m for m in r["analysis_period"]["metrics"] if m["metric"] == name)


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    return code, capsys.readouterr().err


# --- 1. text in a required revenue cell -----------------------------------------------------------

def test_text_in_revenue_for_the_analysis_month_blocks_the_run(tmp_path, capsys):
    wb = make_workbook(tmp_path / "t.xlsx", MONTHS, rows(sales=(1000, 1100, "n/a")))
    r = analyze(wb)
    assert r["status"] == "fail"
    [err] = issues(r, "required_metric_unavailable")
    assert err["severity"] == "error" and err["period"] == "2026-03"
    # Revenue and everything built on it are unavailable; COGS and opex are not.
    assert err["details"]["metrics"] == ["revenue", "gross_profit", "operating_income", "net_income"]
    [cause] = err["details"]["causes"]
    assert cause["code"] == "non_numeric_value" and cause["account"] == "Sales" and "E8" in cause["message"]
    assert metric(r, "revenue")["value"] is None and metric(r, "cogs")["value"] == 360.0

    out = tmp_path / "out"
    code, err_text = run(capsys, wb, "--out-dir", out, "--quiet")
    assert code == 2 and "VALIDATION FAILED" in err_text
    assert sorted(p.name for p in out.iterdir()) == ["analysis.failed.json", "brief.failed.md", "last_run.json"]
    brief = (out / "brief.failed.md").read_text(encoding="utf-8")
    assert "Do not write commentary" in brief and "required_metric_unavailable" in brief and "E8" in brief


def test_text_outside_the_analysis_month_keeps_the_warning_policy(tmp_path):
    wb = make_workbook(tmp_path / "t.xlsx", MONTHS, rows(sales=(1000, "n/a", 1200)))
    r = analyze(wb)
    assert r["status"] == "pass_with_warnings" and not issues(r, "required_metric_unavailable")
    assert [i["severity"] for i in issues(r, "non_numeric_value")] == ["warning"]
    assert metric(r, "revenue")["value"] == 1420.0                   # 1200 + 220
    assert metric(r, "revenue")["mom"]["note"] == "missing_value"    # Feb is unknown, so no MoM


def test_an_earlier_clean_month_can_still_be_analysed(tmp_path):
    r = analyze(make_workbook(tmp_path / "t.xlsx", MONTHS, rows(sales=(1000, 1100, "n/a"))), period="2026-02")
    assert r["status"] == "pass_with_warnings" and not issues(r, "required_metric_unavailable")
    assert metric(r, "revenue")["value"] == 1310.0                   # 1100 + 210
    assert metric(r, "gross_profit")["value"] == 980.0               # 1310 - 330


# --- 2. a missing required value -------------------------------------------------------------------

def test_blank_required_value_in_the_analysis_month_blocks_the_run(tmp_path):
    r = analyze(make_workbook(tmp_path / "b.xlsx", MONTHS, rows(services=(200, 210, None))))
    assert r["status"] == "fail"
    [err] = issues(r, "required_metric_unavailable")
    assert "revenue" in err["details"]["metrics"]
    assert [(c["code"], c["account"]) for c in err["details"]["causes"]] == [("missing_value", "Services")]


def test_blank_in_an_earlier_month_stays_a_warning(tmp_path):
    r = analyze(make_workbook(tmp_path / "b.xlsx", MONTHS, rows(services=(None, 210, 220))))
    assert r["status"] == "pass_with_warnings" and not issues(r, "required_metric_unavailable")
    assert r["income_statement"]["monthly"]["revenue"]["values"]["2026-01"] is None


def test_blank_counts_as_zero_only_when_configured(tmp_path):
    cfg, _ = config(tmp_path, {"blank_cells_as_zero": True})
    r = analyze(make_workbook(tmp_path / "b.xlsx", MONTHS, rows(services=(200, 210, None))), cfg)
    assert not issues(r, "required_metric_unavailable")
    assert metric(r, "revenue")["value"] == 1200.0                   # 1200 + 0


# --- 3. an unexpected expense account --------------------------------------------------------------

def test_unexpected_account_is_a_named_warning_and_is_not_classified(tmp_path):
    # Core figures are reported only when the config classifies accounts; non_cash is a memo overlay that
    # does not move anything out of core, so core opex here must equal opex as presented.
    cfg, _ = config(tmp_path, {"income_statement": {**ROLLUPS["income_statement"],
                                                    "account_classes": {"non_cash": ["Software"]}}})
    r = analyze(make_workbook(tmp_path / "u.xlsx", MONTHS, rows(extra_opex=[(1, "Crypto Losses", [0, 0, 25])])), cfg)
    assert r["status"] == "pass_with_warnings"
    [w] = issues(r, "uncategorised_opex")
    assert w["severity"] == "warning" and w["account"] == "Crypto Losses" and "Crypto Losses" in w["message"]
    assert w["details"]["source_row"] == 17 and w["details"]["path"] == "Operating Expenses > Crypto Losses"
    assert w["details"]["values"] == {"2026-03": 25.0} and w["details"]["classification"] is None
    # No classification is invented: the account stays in the totals as presented and in core.
    account = next(a for a in r["income_statement"]["accounts"] if a["label"] == "Crypto Losses")
    assert account["classification"] is None and account["category"] == "Other operating expenses"
    assert metric(r, "opex_total")["value"] == 495.0                 # 400 + 70 + 25
    assert metric(r, "core_opex")["value"] == 495.0                  # nothing classified non-operating
    cats = {c["name"]: c["values"]["2026-03"] for c in r["income_statement"]["opex_categories"]}
    assert cats == {"Facilities": 400.0, "Tools": 70.0, "Other operating expenses": 25.0}

    brief = render_brief(r)
    section = brief.split("## Accounts needing classification", 1)[1].split("\n## ", 1)[0]
    assert "| Crypto Losses | 17 | Operating Expenses > Crypto Losses | 25 | 1: Mar 2026 |" in section
    assert "**unconfirmed**" in section


def test_without_category_rules_no_account_is_unexpected(tmp_path):
    # With no rules there is no list of expected accounts; each group is its own category (documented default).
    r = analyze(make_workbook(tmp_path / "u.xlsx", MONTHS, rows(extra_opex=[(1, "Crypto Losses", [0, 0, 25])])))
    assert not issues(r, "uncategorised_opex") and r["status"] == "pass"
    assert "Accounts needing classification" not in render_brief(r)


# --- 4. a legitimate missing month follows the existing warning policy -----------------------------

def test_missing_month_is_still_a_warning(tmp_path):
    months = ["Jan 2026", "Feb 2026", "Apr 2026"]  # March absent
    r = analyze(make_workbook(tmp_path / "g.xlsx", months, rows()))
    assert r["status"] == "pass_with_warnings" and not issues(r, "required_metric_unavailable")
    assert [i["period"] for i in issues(r, "missing_period")] == ["2026-03"]
    assert r["periods"]["analysis_period"] == "2026-04"
    assert metric(r, "revenue")["value"] == 1420.0                   # third column: 1200 + 220
    assert metric(r, "revenue")["mom"]["note"] == "comparison_period_not_in_data"


# --- 5. a normal workbook -------------------------------------------------------------------------

def test_normal_run_passes_with_independent_figures(tmp_path, capsys):
    out = tmp_path / "out"
    code, _ = run(capsys, make_workbook(tmp_path / "n.xlsx", MONTHS, rows()), "--out-dir", out, "--quiet")
    assert code == 0
    r = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert r["status"] == "pass" and not issues(r, "required_metric_unavailable") and not issues(r, "uncategorised_opex")
    revenue, cogs, opex = 1200 + 220, 360, 400 + 70
    assert metric(r, "revenue")["value"] == revenue
    assert metric(r, "gross_profit")["value"] == revenue - cogs
    assert metric(r, "gross_margin")["value"] == round((revenue - cogs) / revenue * 100, 2)   # 74.65
    assert metric(r, "operating_income")["value"] == revenue - cogs - opex                    # 590
    assert metric(r, "revenue")["mom"]["abs_change"] == revenue - 1310
    assert metric(r, "revenue")["mom"]["pct_change"] == round((revenue - 1310) / 1310 * 100, 2)  # 8.40
    assert "Accounts needing classification" not in (out / "brief.md").read_text(encoding="utf-8")


# --- 6. a failed run never makes earlier outputs look current --------------------------------------

def test_blocked_month_leaves_earlier_outputs_marked_as_earlier(tmp_path, capsys):
    # One workbook (same bytes throughout): February is clean, March has text in Sales.
    wb, out = make_workbook(tmp_path / "Client.xlsx", MONTHS, rows(sales=(1000, 1100, "n/a"))), tmp_path / "out"
    assert run(capsys, wb, "--period", "2026-02", "--out-dir", out, "--quiet")[0] == 0
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    (out / "commentary.md").write_text("February revenue was 1,310.\n\n" + source_line(analysis) + "\n", encoding="utf-8")
    assert trace_main([str(out)]) == 0
    earlier_id = json.loads((out / "last_run.json").read_text(encoding="utf-8"))["analysis_id"]
    before = {n: (out / n).read_bytes() for n in ("analysis.json", "brief.md", "commentary.md")}

    code, err = run(capsys, wb, "--period", "2026-03", "--out-dir", out, "--quiet")
    assert code == 2 and "treat them as stale" in err
    assert {n: (out / n).read_bytes() for n in before} == before          # preserved, unchanged
    failed_brief = (out / "brief.failed.md").read_text(encoding="utf-8")
    assert "Earlier outputs in this folder are not from this run" in failed_brief and earlier_id in failed_brief
    last = json.loads((out / "last_run.json").read_text(encoding="utf-8"))
    assert last["status"] == "fail" and last["stale_earlier_outputs"] == ["analysis.json", "brief.md", "commentary.md"]
    capsys.readouterr()
    assert trace_main([str(out)]) == 4                                    # the old commentary is no longer accepted
    assert "the latest run failed" in capsys.readouterr().out


