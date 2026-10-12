"""Full-period revenue total and the month-over-month revenue series in analysis.json.

Expected values are worked out by hand in the comments, not taken from the code.
"""

import json

from financial_analysis.config import Config
from financial_analysis.output import render_brief
from financial_analysis.periods import Period
from financial_analysis.pipeline import analyze
from financial_analysis.trends import extremes

from helpers import make_workbook, months_from

#            Jan25 Feb25 Mar25 Apr25 May25 Jun25 Jul25 Aug25 Sep25 Oct25 Nov25 Dec25
SALES_18 = [1000, 1100, 1050, 1200, 1200, 900, 950, 1300, 1250, 1000, 1400, 1500,
            1450, 1600, 1550, 1700, 1650, 1800]
#           Jan26 Feb26 Mar26 Apr26 May26 Jun26


def statement(sales):
    return [
        (0, "Income", None),
        (1, "Sales", list(sales)),
        (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Operating Expenses", None),
        (1, "Rent", [10] * len(sales)),
        (0, "Total Operating Expenses", "=SUM({c}11:{c}11)"),
    ]


def history(tmp_path, sales, months):
    r = analyze(make_workbook(tmp_path / "wb.xlsx", months, statement(sales)), Config.load())
    return r, r["income_statement"]["revenue_history"]


def by_period(hist):
    return {row["period"]: row for row in hist["monthly"]}


def test_full_period_total_and_mom_series(tmp_path):
    r, hist = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    # 1000+1100+1050+1200+1200+900+950+1300+1250+1000+1400+1500 = 13850 for 2025;
    # 1450+1600+1550+1700+1650+1800 = 9750 for 2026 H1; 13850 + 9750 = 23600.
    assert hist["total"] == 23600.0 and hist["total_note"] is None
    assert (hist["first_period"], hist["last_period"], hist["months_in_data"]) == ("2025-01", "2026-06", 18)
    assert [row["value"] for row in hist["monthly"]] == [float(v) for v in SALES_18]
    assert [row["abs_change"] for row in hist["monthly"]] == [
        None, 100.0, -50.0, 150.0, 0.0, -300.0, 50.0, 350.0, -50.0, -250.0, 400.0, 100.0,
        -50.0, 150.0, -50.0, 150.0, -50.0, 150.0]
    rows = by_period(hist)
    assert rows["2025-01"]["prior_value"] is None and rows["2025-01"]["note"] == "comparison_period_not_in_data"
    assert rows["2025-01"]["prior_period"] == "2024-12"
    # 100/1000 = 10%; -50/1100 = -4.545% -> -4.55; 0/1200 = 0%; -300/1200 = -25%; 400/1000 = 40%
    assert rows["2025-02"]["pct_change"] == 10.0 and rows["2025-03"]["pct_change"] == -4.55
    assert rows["2025-05"]["pct_change"] == 0.0 and rows["2025-06"]["pct_change"] == -25.0
    assert rows["2025-11"]["pct_change"] == 40.0
    # -50/1600 = -3.125% -> -3.13 (half-up, away from zero)
    assert rows["2026-03"]["prior_value"] == 1600.0 and rows["2026-03"]["pct_change"] == -3.13
    assert rows["2026-06"]["prior_period"] == "2026-05" and rows["2026-06"]["prior_value"] == 1650.0


def test_largest_mom_increase_and_decrease(tmp_path):
    _, hist = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    inc, dec = hist["largest_mom_increase"], hist["largest_mom_decrease"]
    # Nov 2025: 1000 -> 1400 (+400, +40%). Jun 2025: 1200 -> 900 (-300, -25%).
    assert (inc["period"], inc["prior_value"], inc["value"], inc["abs_change"], inc["pct_change"]) == \
        ("2025-11", 1000.0, 1400.0, 400.0, 40.0)
    assert (dec["period"], dec["prior_value"], dec["value"], dec["abs_change"], dec["pct_change"]) == \
        ("2025-06", 1200.0, 900.0, -300.0, -25.0)


def test_largest_moves_ties_go_to_the_earliest_month_and_absent_moves_are_none(tmp_path):
    _, hist = history(tmp_path, [100, 200, 100, 200], months_from(2026, 1, 4))
    # +100 in Feb and Apr: the earliest (Feb) wins. Only one decrease: Mar, -100.
    assert hist["largest_mom_increase"]["period"] == "2026-02"
    assert hist["largest_mom_decrease"]["period"] == "2026-03"
    _, falling = history(tmp_path, [300, 200, 100], months_from(2026, 1, 3))
    assert falling["largest_mom_increase"] is None
    assert falling["largest_mom_decrease"]["abs_change"] == -100.0  # -100 twice: Feb is earliest
    assert falling["largest_mom_decrease"]["period"] == "2026-02"


def test_zero_base_sign_change_and_negative_base(tmp_path):
    _, hist = history(tmp_path, [0, 50, -10, 20, -5, -15], months_from(2026, 1, 6))
    rows = by_period(hist)
    # Feb: 0 -> 50, +50 but no % from a zero base.
    assert (rows["2026-02"]["abs_change"], rows["2026-02"]["pct_change"], rows["2026-02"]["note"]) == (50.0, None, "zero_base")
    # Mar: 50 -> -10 and Apr: -10 -> 20 cross zero, so no %.
    assert (rows["2026-03"]["abs_change"], rows["2026-03"]["pct_change"], rows["2026-03"]["note"]) == (-60.0, None, "sign_change")
    assert (rows["2026-04"]["abs_change"], rows["2026-04"]["pct_change"], rows["2026-04"]["note"]) == (30.0, None, "sign_change")
    # Jun: -5 -> -15, -10 against |-5| = -200%, flagged as a negative base.
    assert (rows["2026-06"]["abs_change"], rows["2026-06"]["pct_change"], rows["2026-06"]["note"]) == (-10.0, -200.0, "negative_base")
    # 0 + 50 - 10 + 20 - 5 - 15 = 40
    assert hist["total"] == 40.0
    # Increases: Feb +50, Apr +30 -> Feb. Decreases: Mar -60, May -25, Jun -10 -> Mar.
    assert hist["largest_mom_increase"]["period"] == "2026-02" and hist["largest_mom_decrease"]["period"] == "2026-03"


def test_missing_month_gives_no_total_and_no_bridged_comparison(tmp_path):
    _, hist = history(tmp_path, [100, 110, 130], ["Jan 2026", "Feb 2026", "Apr 2026"])
    assert hist["total"] is None and hist["total_note"] == "comparison_period_not_in_data"
    assert hist["months_in_data"] == 3
    apr = by_period(hist)["2026-04"]
    # Mar is missing: Apr is not compared with Feb.
    assert apr["prior_period"] == "2026-03" and apr["prior_value"] is None and apr["abs_change"] is None
    assert apr["note"] == "comparison_period_not_in_data"
    assert hist["largest_mom_increase"]["period"] == "2026-02"  # the only valid comparison: +10


def test_blank_month_gives_no_total_and_no_comparisons_through_it(tmp_path):
    _, hist = history(tmp_path, [100, None, 130], months_from(2026, 1, 3))
    rows = by_period(hist)
    assert hist["total"] is None and hist["total_note"] == "missing_value"
    assert rows["2026-02"]["value"] is None and rows["2026-02"]["note"] == "missing_value"
    assert rows["2026-03"]["prior_value"] is None and rows["2026-03"]["note"] == "missing_value"
    assert hist["largest_mom_increase"] is None and hist["largest_mom_decrease"] is None


def test_existing_ttm_semantics_are_unchanged(tmp_path):
    r, hist = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    # TTM to Jun 2026 = Jul 2025..Jun 2026 = 950+1300+1250+1000+1400+1500+1450+1600+1550+1700+1650+1800 = 17150,
    # not the 18-month total of 23600.
    assert r["income_statement"]["ttm"]["revenue"]["values"]["2026-06"] == 17150.0
    revenue = next(m for m in r["analysis_period"]["metrics"] if m["metric"] == "revenue")
    assert revenue["ttm"] == 17150.0 and revenue["ytd"] == 9750.0
    assert revenue["mom"]["abs_change"] == hist["monthly"][-1]["abs_change"] == 150.0


def test_brief_shows_the_full_period_section(tmp_path):
    r, _ = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    brief = render_brief(r)
    assert "## Revenue by month, full period (Jan 2025 to Jun 2026)" in brief
    assert "- Total revenue, 18 months: 23,600" in brief
    assert "- Largest MoM increase: Nov 2025, +400 (+40.0%), from 1,000 in Oct 2025 to 1,400" in brief
    assert "- Largest MoM decrease: Jun 2025, -300 (-25.0%), from 1,200 in May 2025 to 900" in brief
    assert "| Jan 2025 | 1,000 | n/a | n/a (no data) | n/a (no data) |" in brief

    r, _ = history(tmp_path, [0, 50, -10], months_from(2026, 1, 3))
    brief = render_brief(r)
    assert "| Feb 2026 | 50 | 0 | +50 | n/a (from zero) |" in brief
    assert "| Mar 2026 | -10 | 50 | -60 | n/a (sign change) |" in brief


# --- highest and lowest revenue month --------------------------------------------------------------

def test_highest_and_lowest_month(tmp_path):
    _, hist = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    # Largest value in SALES_18 is 1800 (Jun 2026); smallest is 900 (Jun 2025). No month is blank.
    assert hist["highest_month"] == {"period": "2026-06", "value": 1800.0}
    assert hist["lowest_month"] == {"period": "2025-06", "value": 900.0}
    assert hist["extremes_note"] is None


def test_extremes_ties_go_to_the_earliest_month(tmp_path):
    _, hist = history(tmp_path, [100, 200, 50, 200, 50], months_from(2026, 1, 5))
    # 200 in Feb and Apr -> Feb; 50 in Mar and May -> Mar.
    assert hist["highest_month"] == {"period": "2026-02", "value": 200.0}
    assert hist["lowest_month"] == {"period": "2026-03", "value": 50.0}
    _, flat = history(tmp_path, [70, 70, 70], months_from(2026, 1, 3))
    # Every month equal: Jan is both the highest and the lowest.
    assert flat["highest_month"] == flat["lowest_month"] == {"period": "2026-01", "value": 70.0}


def test_extremes_skip_blank_months_and_say_they_are_incomplete(tmp_path):
    _, hist = history(tmp_path, [100, None, 300, 50], months_from(2026, 1, 4))
    # Feb is blank and skipped: Mar 300 is highest, Apr 50 lowest, flagged as incomplete.
    assert hist["highest_month"] == {"period": "2026-03", "value": 300.0}
    assert hist["lowest_month"] == {"period": "2026-04", "value": 50.0}
    assert hist["extremes_note"] == "missing_value"


def test_extremes_with_a_month_missing_from_the_data(tmp_path):
    _, hist = history(tmp_path, [100, 110, 130], ["Jan 2026", "Feb 2026", "Apr 2026"])
    # Mar is not in the workbook at all, so the extremes cover the months present and are flagged.
    assert hist["highest_month"] == {"period": "2026-04", "value": 130.0}
    assert hist["lowest_month"] == {"period": "2026-01", "value": 100.0}
    assert hist["extremes_note"] == "comparison_period_not_in_data"


def test_extremes_are_none_when_every_month_is_blank(tmp_path):
    # Unit level: a revenue series with no values at all.
    periods = [Period.from_key(k) for k in ("2026-01", "2026-02", "2026-03")]
    assert extremes({p: None for p in periods}, periods) == {
        "highest_month": None, "lowest_month": None, "extremes_note": "missing_value"}

    # Through the pipeline: each month has one blank revenue account, so revenue is blank in every month.
    wb = make_workbook(tmp_path / "blank.xlsx", months_from(2026, 1, 3), [
        (0, "Income", None),
        (1, "Sales", [100, None, 100]),
        (1, "Fees", [None, 50, None]),
        (0, "Total Income", "=SUM({c}8:{c}9)"),
        (0, "Operating Expenses", None),
        (1, "Rent", [10, 10, 10]),
        (0, "Total Operating Expenses", "=SUM({c}12:{c}12)"),
    ])
    r = analyze(wb, Config.load())
    hist = r["income_statement"]["revenue_history"]
    assert list(r["income_statement"]["monthly"]["revenue"]["values"].values()) == [None, None, None]
    assert (hist["highest_month"], hist["lowest_month"], hist["extremes_note"]) == (None, None, "missing_value")
    brief = render_brief(r)
    assert "- Highest-revenue month: n/a (missing input)" in brief
    assert "- Lowest-revenue month: n/a (missing input)" in brief


def test_brief_shows_extremes_and_the_ttm_trend_table(tmp_path):
    r, _ = history(tmp_path, SALES_18, months_from(2025, 1, 18))
    brief = render_brief(r)
    assert "- Highest-revenue month: Jun 2026, 1,800" in brief
    assert "- Lowest-revenue month: Jun 2025, 900" in brief
    # No accounts are classified, so there are no core metrics and no "as presented" note.
    assert "## TTM trend (twelve months to each month)" in brief
    ttm_section = brief.split("## TTM trend")[1].split("##")[0]
    assert "| TTM to | TTM revenue |" in ttm_section
    # TTM to Dec 2025 = 13850 (calendar 2025); TTM to Jun 2026 = 17150. No TTM before 12 months (Nov 2025).
    assert "| Dec 2025 | 13,850 |" in ttm_section and "| Jun 2026 | 17,150 |" in ttm_section
    assert "| Nov 2025 |" not in ttm_section
    assert "**As presented**" not in brief

    r, _ = history(tmp_path, [100, None, 300, 50], months_from(2026, 1, 4))
    brief = render_brief(r)
    assert "- Highest-revenue month: Mar 2026, 300 (months with a value only; extremes incomplete: missing input)" in brief
    assert "## TTM trend" not in brief  # fewer than 12 months: no TTM rows at all


def test_brief_separates_monthly_and_ttm_core_margin_and_explains_as_presented(tmp_path):
    # 12 months of Sales 1000; opex = Rent 300 plus Interest Income -20 and Income Tax Benefit -100.
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"income_statement": {"account_classes": {
        "non_operating": ["Interest Income"], "income_tax": ["Income Tax Benefit"]}}}), encoding="utf-8")
    wb = make_workbook(tmp_path / "wb.xlsx", months_from(2025, 7, 12), [
        (0, "Income", None),
        (1, "Sales", [1000] * 12),
        (0, "Total Income", "=SUM({c}8:{c}8)"),
        (0, "Operating Expenses", None),
        (1, "Rent", [300] * 12),
        (1, "Interest Income", [-20] * 12),
        (1, "Income Tax Benefit", [-100] * 12),
        (0, "Total Operating Expenses", "=SUM({c}11:{c}13)"),
    ])
    r = analyze(wb, Config.load(cfg))
    # Classifications are reported exactly as configured; the accounts stay in the workbook's opex section.
    classes = {a["label"]: (a["section"], a["classification"]) for a in r["income_statement"]["accounts"]}
    assert classes == {"Sales": ("revenue", None), "Rent": ("opex", None),
                       "Interest Income": ("opex", "non_operating"), "Income Tax Benefit": ("opex", "income_tax")}
    brief = render_brief(r)

    # Monthly: core opex 300, core income 1000 - 300 = 700; net income 1000 - (300 - 20 - 100) = 820;
    # core margin 700 / 1000 = 70.0%. TTM (Jun 2026): 12 x 700 = 8400 / 12000 = 70.0%.
    monthly_header = brief.split("## Monthly trend")[1].split("\n")[2]
    assert monthly_header.endswith("| Core operating margin |")
    assert "| Jun 2026 | 1,000 | 300 | 700 | 820 | 70.0% |" in brief.split("## Monthly trend")[1]
    assert ("| TTM to | TTM revenue | TTM core operating income | TTM core operating margin |\n|---|---|---|---|\n"
            "| Jun 2026 | 12,000 | 8,400 | 70.0% |") in brief

    note = next(line for line in brief.splitlines() if line.startswith("> **As presented**"))
    assert "'Operating Expenses' section includes: Interest Income (configured as non_operating); " \
           "Income Tax Benefit (configured as income_tax)" in note
    assert "*as presented* include these accounts" in note and "**Core** figures exclude them" in note
    assert "accounting judgment to be confirmed by the accounting team" in note
