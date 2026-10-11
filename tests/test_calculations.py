"""Income statement calculations on small hand-built workbooks.

Expected values are worked out by hand in the comments, not taken from the code.
"""

import json
from decimal import Decimal

import pytest

from financial_analysis.config import Config
from financial_analysis.pipeline import analyze
from financial_analysis.periods import Period

from helpers import make_workbook

JAN, FEB, MAR = Period(2026, 1), Period(2026, 2), Period(2026, 3)
MONTHS = ["Jan 2026", "Feb 2026", "Mar 2026"]


def rows(services=(500, 400, 390), opex_total="=SUM({c}16:{c}16)+{c}20+SUM({c}21:{c}22)", payroll_total="=SUM({c}18:{c}19)"):
    # Sheet rows start at 7; the comment gives each row number used in the formulas.
    return [
        (0, "Income", None),                                  # 7
        (1, "Product Sales", [1000, 1100, 1210]),             # 8
        (1, "Services", list(services)),                      # 9
        (0, "Total Income", "=SUM({c}8:{c}9)"),               # 10
        (0, "Cost of Goods Sold", None),                      # 11
        (1, "Materials", [600, 600, 750]),                    # 12
        (0, "Total Cost of Goods Sold", "=SUM({c}12:{c}12)"),  # 13
        (1, "Gross Profit", "=({c}10-{c}13)"),                # 14
        (0, "Operating Expenses", None),                      # 15
        (1, "Rent", [200, 200, 200]),                         # 16
        (1, "Payroll", None),                                 # 17
        (1, "Wages", [300, 350, 300]),                        # 18
        (1, "Payroll Taxes", [30, 35, 30]),                   # 19
        (1, "Total Payroll", payroll_total),                  # 20
        (1, "Software", [70, 15, 80]),                        # 21
        (1, "Interest Income", [-10, -10, -10]),              # 22
        (0, "Total Operating Expenses", opex_total),          # 23
        (1, "Operating Income", "={c}14-{c}23"),              # 24
        (1, "Net Income", "={c}24"),                          # 25
    ]


def config_with(tmp_path, override=None):
    if not override:
        return Config.load()
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(override), encoding="utf-8")
    return Config.load(p)


def monthly(result, metric):
    return result["income_statement"]["monthly"][metric]["values"]


def codes(result, severity=None):
    return {i["code"] for i in result["validation"]["issues"] if severity is None or i["severity"] == severity}


def test_basic_metrics(tmp_path):
    wb = make_workbook(tmp_path / "basic.xlsx", MONTHS, rows())
    r = analyze(wb)
    # Revenue: 1000+500, 1100+400, 1210+390
    assert monthly(r, "revenue") == {"2026-01": 1500.0, "2026-02": 1500.0, "2026-03": 1600.0}
    assert monthly(r, "cogs") == {"2026-01": 600.0, "2026-02": 600.0, "2026-03": 750.0}
    # Gross profit 900, 900, 850; margin 60%, 60%, 850/1600 = 53.125% -> 53.13 (half-up)
    assert monthly(r, "gross_profit") == {"2026-01": 900.0, "2026-02": 900.0, "2026-03": 850.0}
    assert monthly(r, "gross_margin") == {"2026-01": 60.0, "2026-02": 60.0, "2026-03": 53.13}
    # Opex: 200+300+30+70-10 = 590; 200+350+35+15-10 = 590; 200+300+30+80-10 = 600
    assert monthly(r, "opex_total") == {"2026-01": 590.0, "2026-02": 590.0, "2026-03": 600.0}
    # Net income = 900-590, 900-590, 850-600
    assert monthly(r, "net_income") == {"2026-01": 310.0, "2026-02": 310.0, "2026-03": 250.0}
    assert r["status"] == "pass"
    assert all(c["status"] != "fail" for c in r["validation"]["checks"])


def test_reported_and_calculated_are_kept_apart(tmp_path):
    r = analyze(make_workbook(tmp_path / "b.xlsx", MONTHS, rows()))
    reported = r["income_statement"]["reported_totals"]
    assert reported["revenue"]["basis"] == "reported" and reported["revenue"]["label"] == "Total Income"
    assert reported["net_income"]["source_row"] == 25
    assert r["income_statement"]["monthly"]["revenue"]["basis"] == "calculated"


def test_nested_subtotal_not_double_counted(tmp_path):
    # Summing every opex row including 'Total Payroll' (330) would give 920 in Jan, not 590.
    r = analyze(make_workbook(tmp_path / "b.xlsx", MONTHS, rows()))
    assert monthly(r, "opex_total")["2026-01"] == 590.0
    labels = [a["label"] for a in r["income_statement"]["accounts"]]
    assert "Total Payroll" not in labels and "Gross Profit" not in labels
    assert labels.count("Wages") == 1


def test_hard_coded_totals_are_still_recognised_as_totals(tmp_path):
    # Values-only export: totals are typed-in numbers, not formulas.
    hard = rows(opex_total=[590, 590, 600], payroll_total=[330, 385, 330])
    hard[3] = (0, "Total Income", [1500, 1500, 1600])
    hard[6] = (0, "Total Cost of Goods Sold", [600, 600, 750])
    hard[7] = (1, "Gross Profit", [900, 900, 850])
    hard[17] = (1, "Operating Income", [310, 310, 250])
    hard[18] = (1, "Net Income", [310, 310, 250])
    r = analyze(make_workbook(tmp_path / "h.xlsx", MONTHS, hard))
    assert monthly(r, "opex_total")["2026-01"] == 590.0
    assert monthly(r, "net_income")["2026-03"] == 250.0
    assert r["status"] == "pass"


def test_workbook_total_that_double_counts_is_flagged(tmp_path):
    # Broken workbook: Total Operating Expenses sums rows 16..22 *including* Total Payroll (row 20).
    # Reported Jan = 590 + 330 = 920; the pipeline must still report 590 and fail validation.
    r = analyze(make_workbook(tmp_path / "dc.xlsx", MONTHS, rows(opex_total="=SUM({c}16:{c}22)")))
    assert monthly(r, "opex_total")["2026-01"] == 590.0
    assert r["status"] == "fail"
    mismatch = [i for i in r["validation"]["issues"] if i["code"] == "subtotal_mismatch"]
    assert mismatch and mismatch[0]["severity"] == "error"
    jan = next(m for m in mismatch[0]["details"]["mismatches"] if m["period"] == "2026-01")
    assert Decimal(jan["reported"]) == 920 and Decimal(jan["calculated"]) == 590
    assert "derived_mismatch" in codes(r, "error")  # Operating Income is built on the broken total


def test_missing_value_is_not_treated_as_zero(tmp_path):
    r = analyze(make_workbook(tmp_path / "m.xlsx", MONTHS, rows(services=(500, None, 390))))
    rev = monthly(r, "revenue")
    assert rev["2026-02"] is None and rev["2026-01"] == 1500.0 and rev["2026-03"] == 1600.0
    assert monthly(r, "gross_margin")["2026-02"] is None
    assert r["income_statement"]["monthly"]["gross_margin"]["notes"]["2026-02"] == "missing_value"
    assert monthly(r, "net_income")["2026-02"] is None
    issue = next(i for i in r["validation"]["issues"] if i["code"] == "missing_value")
    assert issue["account"] == "Services" and issue["details"]["periods"] == ["2026-02"]
    assert r["status"] == "pass_with_warnings"
    # Mar MoM needs Feb, which is unknown
    rev_row = next(m for m in r["analysis_period"]["metrics"] if m["metric"] == "revenue")
    assert rev_row["mom"]["abs_change"] is None and rev_row["mom"]["note"] == "missing_value"
    # The reported total (SUM treats blank as 0) cannot be reconciled for Feb, so that month is skipped, not failed.
    check = next(c for c in r["validation"]["checks"] if c["check"].endswith(":Income"))
    assert check["status"] == "pass" and check["periods_skipped"] == 1


def test_blank_as_zero_only_when_configured(tmp_path):
    cfg = config_with(tmp_path, {"blank_cells_as_zero": True})
    r = analyze(make_workbook(tmp_path / "m.xlsx", MONTHS, rows(services=(500, None, 390))), cfg)
    assert monthly(r, "revenue")["2026-02"] == 1100.0
    assert "blank_as_zero" in codes(r, "info")


def test_text_in_numeric_cell_is_missing_with_warning(tmp_path):
    r = analyze(make_workbook(tmp_path / "t.xlsx", MONTHS, rows(services=(500, "n/a", 390))))
    assert monthly(r, "revenue")["2026-02"] is None
    assert "non_numeric_value" in codes(r, "warning")


def test_zero_revenue_gives_no_margin(tmp_path):
    zero = [
        (0, "Income", None), (1, "Sales", [0, 100]), (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Cost of Goods Sold", None), (1, "Materials", [10, 40]), (0, "Total Cost of Goods Sold", "=SUM({c}11:{c}11)"),
    ]
    r = analyze(make_workbook(tmp_path / "z.xlsx", ["Jan 2026", "Feb 2026"], zero))
    gm = r["income_statement"]["monthly"]["gross_margin"]
    assert gm["values"]["2026-01"] is None and gm["notes"]["2026-01"] == "zero_denominator"
    assert gm["values"]["2026-02"] == 60.0  # (100-40)/100
    rev = next(m for m in r["analysis_period"]["metrics"] if m["metric"] == "revenue")
    assert rev["mom"]["abs_change"] == 100.0 and rev["mom"]["pct_change"] is None and rev["mom"]["note"] == "zero_base"


def test_core_figures_exclude_configured_non_operating_accounts(tmp_path):
    cfg = config_with(tmp_path, {"income_statement": {"account_classes": {"non_operating": ["Interest Income"]},
                                                     "expected_negative_accounts": ["Interest Income"]}})
    r = analyze(make_workbook(tmp_path / "c.xlsx", MONTHS, rows()), cfg)
    # Interest income of -10 sits inside opex: core opex = 590 + 10 = 600, core operating income = 310 - 10 = 300
    assert monthly(r, "non_operating_in_opex")["2026-01"] == -10.0
    assert monthly(r, "core_opex")["2026-01"] == 600.0
    assert monthly(r, "core_operating_income")["2026-01"] == 300.0
    assert monthly(r, "net_income")["2026-01"] == 310.0  # net income is unchanged by reclassification
    assert "negative_value" not in codes(r)


def test_unknown_configured_account_is_reported(tmp_path):
    cfg = config_with(tmp_path, {"income_statement": {"account_classes": {"non_operating": ["Intrest Income"]}}})
    r = analyze(make_workbook(tmp_path / "c.xlsx", MONTHS, rows()), cfg)
    assert "config_account_not_found" in codes(r, "warning")


def test_opex_categories_cover_every_account_once(tmp_path):
    cfg = config_with(tmp_path, {"income_statement": {"opex_rollups": [
        {"name": "People", "match": ["> Payroll >"]},
        {"name": "Facilities", "match": ["> Rent$"]},
    ]}})
    r = analyze(make_workbook(tmp_path / "c.xlsx", MONTHS, rows()), cfg)
    cats = {c["name"]: c for c in r["income_statement"]["opex_categories"]}
    assert cats["People"]["accounts"] == ["Wages", "Payroll Taxes"]
    assert cats["People"]["values"]["2026-01"] == 330.0
    assert cats["Facilities"]["values"]["2026-01"] == 200.0
    # Software (70) + Interest Income (-10) are unmatched
    assert cats["Other operating expenses"]["values"]["2026-01"] == 60.0
    assert "uncategorised_opex" in codes(r, "info")
    check = next(c for c in r["validation"]["checks"] if c["check"] == "opex_categories_complete")
    assert check["status"] == "pass"


def test_default_categories_are_direct_children_of_opex(tmp_path):
    r = analyze(make_workbook(tmp_path / "d.xlsx", MONTHS, rows()))
    names = [c["name"] for c in r["income_statement"]["opex_categories"]]
    assert names == ["Rent", "Payroll", "Software", "Interest Income"]


def test_output_is_deterministic(tmp_path):
    wb = make_workbook(tmp_path / "d.xlsx", MONTHS, rows())
    assert analyze(wb) == analyze(wb)


def test_unknown_period_is_rejected(tmp_path):
    from financial_analysis.issues import AnalysisError
    with pytest.raises(AnalysisError):
        analyze(make_workbook(tmp_path / "d.xlsx", MONTHS, rows()), period="2025-12")
