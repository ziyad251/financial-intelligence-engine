"""MoM, YoY, TTM, YTD and balance-sheet comparisons. Expected values are hand-calculated."""

from decimal import Decimal as D

from financial_analysis.periods import Period
from financial_analysis.pipeline import analyze
from financial_analysis.trends import balance_sheet_view, change, compare, pp_change, ttm, ttm_ratio, ytd

from helpers import make_workbook, months_from


def series(start: Period, values):
    return {start.shift(i): (None if v is None else D(str(v))) for i, v in enumerate(values)}


def test_change_basic():
    c = change(D(120), D(100))
    assert c["abs_change"] == 20 and c["pct_change"] == 20 and c["note"] is None
    c = change(D(75), D(100))
    assert c["abs_change"] == -25 and c["pct_change"] == -25


def test_change_zero_base_has_no_percentage():
    c = change(D(50), D(0))
    assert c["abs_change"] == 50 and c["pct_change"] is None and c["note"] == "zero_base"
    c = change(D(0), D(0))
    assert c["abs_change"] == 0 and c["pct_change"] is None


def test_change_across_zero_has_no_percentage():
    c = change(D(50), D(-100))
    assert c["abs_change"] == 150 and c["pct_change"] is None and c["note"] == "sign_change"


def test_change_negative_base_uses_absolute_prior():
    # Loss narrows from -200 to -150: +50 on a base of |-200| = +25%
    c = change(D(-150), D(-200))
    assert c["abs_change"] == 50 and c["pct_change"] == 25 and c["note"] == "negative_base"


def test_change_missing_input():
    c = change(None, D(10))
    assert c["abs_change"] is None and c["pct_change"] is None and c["note"] == "missing_value"


def test_margin_change_is_percentage_points():
    c = pp_change(D("53.125"), D(60))
    assert c["pp_change"] == D("-6.875") and "pct_change" not in c


def test_compare_mom_and_yoy():
    s = series(Period(2025, 1), range(1, 15))  # Jan 2025 = 1 ... Feb 2026 = 14
    mom = compare(s, Period(2026, 2), 1)
    assert mom["prior"] == 13 and mom["abs_change"] == 1 and mom["comparison_period"] == "2026-01"
    yoy = compare(s, Period(2026, 2), 12)
    assert yoy["prior"] == 2 and yoy["abs_change"] == 12  # Feb 2025 = 2


def test_compare_missing_period():
    s = {Period(2026, 1): D(10), Period(2026, 3): D(30)}  # Feb missing
    c = compare(s, Period(2026, 3), 1)
    assert c["abs_change"] is None and c["note"] == "comparison_period_not_in_data"


def test_ttm_sums_twelve_months():
    s = series(Period(2025, 1), range(1, 15))
    assert ttm(s, Period(2025, 12)) == (sum(range(1, 13)), None)  # 78
    assert ttm(s, Period(2026, 2)) == (sum(range(3, 15)), None)  # Mar 2025..Feb 2026 = 102


def test_ttm_needs_twelve_months():
    s = series(Period(2025, 1), range(1, 15))
    assert ttm(s, Period(2025, 11)) == (None, "insufficient_history")


def test_ttm_with_gap_or_missing_value():
    s = series(Period(2025, 1), range(1, 15))
    del s[Period(2025, 6)]
    assert ttm(s, Period(2025, 12)) == (None, "comparison_period_not_in_data")
    s = series(Period(2025, 1), [1] * 5 + [None] + [1] * 8)
    assert ttm(s, Period(2026, 2)) == (None, "missing_value")


def test_ttm_margin_is_ratio_of_sums_not_average_of_margins():
    # 11 months: revenue 100, GP 50 (50%). 1 month: revenue 1000, GP 900 (90%).
    # Average of monthly margins = (11*50 + 90)/12 = 53.33%; correct TTM margin = (550+900)/(1100+1000) = 69.05%
    rev = series(Period(2025, 1), [100] * 11 + [1000])
    gp = series(Period(2025, 1), [50] * 11 + [900])
    value, note = ttm_ratio(gp, rev, Period(2025, 12))
    assert note is None
    assert round(value, 4) == round(D(1450) / D(2100) * 100, 4)


def test_ytd_respects_fiscal_year_start():
    s = series(Period(2025, 1), range(1, 15))
    assert ytd(s, Period(2026, 2), 1) == (13 + 14, None)  # Jan + Feb 2026
    assert ytd(s, Period(2025, 6), 4) == (4 + 5 + 6, None)  # Apr..Jun 2025 with April year start


def test_balance_sheet_view_does_not_sum():
    s = series(Period(2025, 1), [1000 + 10 * i for i in range(14)])
    view = balance_sheet_view(s, Period(2026, 2))
    assert view["value"] == 1130  # point-in-time balance, not a 12-month sum
    assert view["mom"]["abs_change"] == 10
    assert view["vs_12_months_earlier"]["prior"] == 1010 and view["vs_12_months_earlier"]["abs_change"] == 120
    assert "ttm" not in view


def test_pipeline_ttm_and_balance_sheet(tmp_path):
    months = months_from(2025, 1, 13)  # Jan 2025 .. Jan 2026
    is_rows = [
        (0, "Income", None),
        (1, "Sales", [100 + i for i in range(13)]),  # 100..112
        (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Operating Expenses", None),
        (1, "Rent", [50] * 13),
        (0, "Total Operating Expenses", "=SUM({c}11:{c}11)"),
    ]
    bs_rows = [
        (0, "Assets", None),
        (1, "Cash", [500 + 10 * i for i in range(13)]),  # 500..620
        (0, "Total Assets", "=SUM({c}8:{c}8)"),
        (0, "Liabilities", None),
        (1, "Accounts Payable", [100] * 13),
        (0, "Total Liabilities", "=SUM({c}11:{c}11)"),
        (0, "Equity", None),
        (1, "Retained Earnings", [400 + 10 * i for i in range(13)]),
        (0, "Total Equity", "=SUM({c}14:{c}14)"),
    ]
    r = analyze(make_workbook(tmp_path / "t.xlsx", months, is_rows, bs_rows))
    ttm_rev = r["income_statement"]["ttm"]["revenue"]
    assert ttm_rev["values"]["2025-11"] is None and ttm_rev["notes"]["2025-11"] == "insufficient_history"
    assert ttm_rev["values"]["2025-12"] == float(sum(range(100, 112)))  # 1266
    assert ttm_rev["values"]["2026-01"] == float(sum(range(101, 113)))  # 1278
    # No gross margin without a COGS section
    assert "gross_margin" not in r["income_statement"]["monthly"]
    assert any(i["code"] == "cogs_section_missing" for i in r["validation"]["issues"])
    bs = r["balance_sheet"]
    assert bs["ttm"].startswith("not_applicable")
    cash = next(x for x in bs["analysis_period"] if x["metric"] == "cash")
    assert cash["value"] == 620.0 and cash["vs_12_months_earlier"]["prior"] == 500.0
    check = next(c for c in r["validation"]["checks"] if c["check"] == "balance_sheet_balances")
    assert check["status"] == "pass"
