"""Commentary claim checks (claims.py via financial_analysis.trace): good commentary passes, and each kind of
wrong commentary is reported. Every figure below was worked out by hand from FIXTURE_ROWS."""

import json
from pathlib import Path

import pytest

from financial_analysis import claims, highlights
from financial_analysis.cli import main as run_analysis
from financial_analysis.output import source_line
from financial_analysis.trace import content_findings, main as trace_main

from helpers import make_workbook, months_from

# Jun 2025 .. Jun 2026. June 2026: revenue 1,800 (May 2,000: -200, -10.0%; Jun 2025 1,000: +800, +80.0%);
# COGS 400; gross profit 1,400 (May 1,600: -200, -12.5%); gross margin 77.8% (May 80.0%: -2.2 pp);
# opex 1,700 = Rent 500 + Salaries 1,200 (May 1,000: +200, +20.0%); operating income -300 (a loss; May +100,
# change -400). TTM revenue to June 2026: 1,100 + ... + 1,900 = 13,500, + 1,950 + 2,000 + 1,800 = 19,250.
SALES = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 1950, 2000, 1800]
FIXTURE_ROWS = [
    (0, "Income", None), (1, "Sales", SALES), (0, "Total Income", "=SUM({c}8:{c}8)"),
    (0, "Cost of Goods Sold", None), (1, "Materials", [400] * 13), (0, "Total Cost of Goods Sold", "=SUM({c}11:{c}11)"),
    (0, "Operating Expenses", None), (1, "Rent", [500] * 13), (1, "Salaries", [1000] * 12 + [1200]),
    (0, "Total Operating Expenses", "=SUM({c}14:{c}15)"),
]

VALID = """# Claims Co, June 2026

June 2026 revenue was 1,800, down 200 (-10.0%) on May and up 800 (+80.0%) on June 2025.
Gross margin was 77.8%, down 2.2 pp on May. Salaries rose 200 (+20.0%) on May.
The operating loss was 300 in June, against an operating profit of 100 in May.
TTM revenue to June 2026 was 19,250.
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("claims")
    wb = make_workbook(tmp / "Claims Co.xlsx", months_from(2025, 6, 13), FIXTURE_ROWS)
    folder = tmp / "out"
    assert run_analysis([str(wb), "--out-dir", str(folder), "--quiet"]) == 0
    return folder


def findings(folder: Path, text: str) -> list[tuple[str, str]]:
    analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    brief = (folder / "brief.md").read_text(encoding="utf-8")
    return [(f.kind, f.token) for f in content_findings(text, analysis, brief)]


def check_cli(folder: Path, text: str, capsys) -> tuple[int, str]:
    analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    (folder / "commentary.md").write_text(text + "\n" + source_line(analysis) + "\n", encoding="utf-8")
    code = trace_main([str(folder)])
    return code, capsys.readouterr().out


# --- valid commentary passes ---------------------------------------------------------------------

def test_correct_commentary_passes(out, capsys):
    assert findings(out, VALID) == []
    code, printed = check_cli(out, VALID, capsys)
    assert code == 0, printed
    assert "0 untraced number(s), 0 claim finding(s)" in printed


def test_one_sentence_with_several_independent_correct_claims_passes(out):
    # Five figures of one metric in one sentence: value, MoM amount and %, YoY amount and %, each with the
    # right sign and direction word.
    text = "June 2026 revenue was 1,800, down 200 (-10.0%) on May and up 800 (+80.0%) on June 2025."
    assert findings(out, text) == []


def test_labelled_hypothesis_and_question_are_not_findings(out):
    text = ("Revenue fell 200 on May. Possible explanation (unconfirmed): the fall may be due to a large customer "
            "pausing orders.\n\nWas the 200 fall in revenue caused by a customer pausing orders?\n")
    assert findings(out, text) == []


def test_row_references_and_counts_are_not_read_as_figures(out):
    # The skill names an account's workbook row; Salaries is on row 15. A row no account is on is reported.
    assert findings(out, "Salaries (row 15) rose 200 on May.") == []
    assert findings(out, "Salaries (row 99) rose 200 on May.") == [("untraced", "99")]
    assert findings(out, "Revenue rose in 9 of the last 12 months.") == []


def test_cause_quantified_from_the_analysis_is_an_attribution_not_an_invention(out):
    text = "Operating income fell 400 on May, driven by Salaries, up 200, and gross profit, down 200."
    assert findings(out, text) == []


# --- wrong commentary fails ----------------------------------------------------------------------

def test_reversed_revenue_trend_fails(out):
    assert findings(out, "June 2026 revenue rose 200 (+10.0%) on May.") == [("contradicts", "200"),
                                                                           ("contradicts", "+10.0")]


def test_reversed_expense_trend_fails(out):
    assert findings(out, "Salaries fell 200 on May.") == [("contradicts", "200")]


def test_loss_described_as_profit_fails(out):
    assert findings(out, "The operating result was a profit of 300 in June.") == [("contradicts", "300")]
    assert findings(out, "Net profit was 300 in June.") == [("contradicts", "300")]


def test_unsupported_numerical_forecast_fails(out):
    text = "We expect revenue to grow 12% next quarter and to reach 2,500 by December."
    assert findings(out, text) == [("forecast", "12"), ("forecast", "2,500")]


def test_invented_causal_explanation_fails(out):
    text = "Revenue fell 200 on May because a major customer cancelled its contract."
    assert findings(out, text) == [("unsupported_cause", "because")]
    # Naming a line item without its figure is not enough to make a cause a fact.
    assert findings(out, "Gross profit fell 200 on May because the Materials supplier raised prices.") == [
        ("unsupported_cause", "because")]


def test_figure_of_another_metric_or_month_is_misattributed(out):
    assert findings(out, "Revenue was 1,700 in June.") == [("misattributed", "1,700")]  # that is opex
    assert findings(out, "Revenue was 2,000 in June.") == [("misattributed", "2,000")]  # that is May
    # 12.5% is gross profit's MoM change, not a gross margin: a number found elsewhere does not count.
    assert findings(out, "Gross margin was 12.5% in June.") == [("misattributed", "12.5")]


def test_comparison_month_value_of_a_highlight_is_not_accepted_as_the_analysis_month(out):
    # Gross margin is highlighted (MoM and YoY), so its May (80.0%) and Jun 2025 (60.0%) values are the highlights'
    # baselines. They are figures of those months: quoted as June's they used to pass.
    assert findings(out, "Gross margin was 80.0% in June.") == [("misattributed", "80.0")]
    assert findings(out, "Gross margin was 60.0% in June.") == [("misattributed", "60.0")]
    assert findings(out, "Gross margin was 80.0% in May.") == []
    assert findings(out, "Gross margin was 60.0% in June 2025.") == []


def test_month_over_month_after_a_figure_is_not_read_as_a_count(out):
    # "200 month over month" is an amount; it used to be skipped as a count of months, so its direction went unchecked.
    assert findings(out, "Salaries fell 200 month over month.") == [("contradicts", "200")]
    assert findings(out, "June 2026 revenue rose 200 month-on-month.") == [("contradicts", "200")]
    assert findings(out, "June 2026 revenue fell 800 year over year.") == [("contradicts", "800")]
    assert findings(out, "Salaries rose 200 month over month.") == []


def test_hyphenated_period_length_is_a_count(out):
    assert findings(out, "TTM revenue for the 12-month window to June 2026 was 19,250.") == []


def test_cli_rejects_each_bad_example_and_names_the_finding(out, capsys):
    bad = {
        "June 2026 revenue rose 200 (+10.0%) on May.": "CONTRADICTS 200",
        "Salaries fell 200 on May.": "CONTRADICTS 200",
        "Net profit was 300 in June.": "CONTRADICTS 300",
        "We expect revenue to grow 12% next quarter.": "FORECAST 12",
        "Revenue fell 200 on May because a major customer cancelled its contract.": "UNSUPPORTED_CAUSE because",
        "Revenue was 2,000 in June.": "MISATTRIBUTED 2,000",
    }
    for text, expected in bad.items():
        code, printed = check_cli(out, VALID + "\n" + text, capsys)
        assert code == 3 and expected in printed, (text, printed)


def test_stale_or_mismatched_analysis_id_fails(out, capsys):
    analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    good = source_line(analysis)
    stale = good.rsplit(" ", 1)[0] + " " + "0" * 64
    (out / "commentary.md").write_text(VALID + "\n" + stale + "\n", encoding="utf-8")
    assert trace_main([str(out)]) == 4
    assert "SOURCE analysis_id is" in capsys.readouterr().out


def test_favourable_metrics_match_the_highlight_rules():
    # claims.py keeps its own copy so the check script needs only the standard library.
    assert claims.FAVOURABLE_WHEN_UP == highlights.FAVOURABLE_WHEN_UP


# --- the supplied workbook ------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "data" / "ABC Inc - Financial Statements (1) (1).xlsx"
CONFIG = ROOT / "config" / "companies" / "abc-inc.json"


@pytest.mark.skipif(not WORKBOOK.exists(), reason="ABC workbook not available")
def test_audit_example_of_misleading_commentary_is_rejected_on_the_supplied_workbook(tmp_path):
    # The kind of commentary the 2026-10-12 audit showed the old presence-only check accepting (exit 0): real
    # figures quoted with the wrong direction or profit/loss wording. The figures come from the run's own
    # analysis.json at brief rounding, so no client figure is written into the repository.
    assert run_analysis([str(WORKBOOK), "--config", str(CONFIG), "--out-dir", str(tmp_path), "--quiet"]) == 0
    rows = {r["metric"]: r for r in json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
            ["analysis_period"]["metrics"]}
    revenue, margin, net = rows["revenue"], rows["gross_margin"], rows["net_income"]
    # Each wrong sentence below says the opposite of the data, so check the data's direction first.
    assert revenue["mom"]["pct_change"] < 0 and margin["mom"]["pp_change"] > 0 and net["value"] < 0
    mom_pct, rev = f"{abs(revenue['mom']['pct_change']):.1f}", f"{revenue['value']:,.0f}"
    pp, gm, loss = f"{margin['mom']['pp_change']:.1f}", f"{margin['value']:.1f}", f"{abs(net['value']):,.0f}"
    text = (f"Revenue rose {mom_pct}% month over month to {rev}, driven by the new enterprise contract signed with "
            f"a major customer.\nGross margin fell {pp} pp to {gm}% because the hosting provider raised prices.\n"
            "We expect revenue to grow 12% next quarter and to reach 250 customers.\n"
            f"Net income improved to a profit of {loss}.\n")
    assert findings(tmp_path, text) == [
        ("contradicts", mom_pct), ("unsupported_cause", "driven by"),
        ("contradicts", pp), ("unsupported_cause", "because"),
        ("forecast", "12"), ("forecast", "250"),
        ("contradicts", loss),
    ]
