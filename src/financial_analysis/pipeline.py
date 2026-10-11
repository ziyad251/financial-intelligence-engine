"""End-to-end analysis: workbook -> validated, structured, JSON-ready result.

The result is deterministic for a given workbook, config and analysis period (no
timestamps, stable ordering), so it can be compared field by field in evaluations.
Numbers are kept at full precision until this module rounds them for output:
amounts to 2 decimals (cents), percentages and percentage points to 2 decimals.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__
from .calculations import (BS_METRIC_INFO, METRIC_INFO, RATIOS, IncomeStatementModel, Line,
                           build_balance_sheet, build_income_statement)
from .config import Config
from .highlights import balance_sheet_highlights, build_highlights, headline_metrics, history_outliers, materiality
from .issues import ERROR, INFO, WARNING, AnalysisError, overall_status, sort_issues
from .loader import Series, load_workbook
from .periods import Period
from .trends import balance_sheet_view, compare, ttm, ttm_ratio, ytd
from .validation import run_validation

CENT = Decimal("0.01")


def rnd(value: Any) -> Any:
    """Round Decimals (recursively) to 2 dp, half-up, and convert to float for JSON."""
    if isinstance(value, Decimal):
        return float(value.quantize(CENT, rounding=ROUND_HALF_UP))
    if isinstance(value, dict):
        return {k: rnd(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rnd(v) for v in value]
    return value


def _series(series: Series) -> dict[str, Any]:
    return {p.key: series[p] for p in sorted(series)}


def _ytd_ratio(model: IncomeStatementModel, ratio: str, period: Period, fy_start: int) -> tuple[Decimal | None, str | None]:
    num_key, den_key = RATIOS[ratio]
    num, r1 = ytd(model.metrics[num_key], period, fy_start)
    den, r2 = ytd(model.metrics[den_key], period, fy_start)
    if num is None or den is None:
        return None, r1 or r2
    if den <= 0:
        return None, "zero_denominator" if den == 0 else "negative_denominator"
    return num / den * 100, None


def _metric_row(model: IncomeStatementModel, metric: str, period: Period, fy_start: int) -> dict[str, Any]:
    label, unit = METRIC_INFO[metric]
    series = model.metrics[metric]
    row: dict[str, Any] = {"metric": metric, "label": label, "unit": unit, "value": series.get(period),
                           "value_note": model.ratio_reasons.get(metric, {}).get(period),
                           "mom": compare(series, period, 1, unit), "yoy": compare(series, period, 12, unit)}
    if unit == "percent":
        num, den = RATIOS[metric]
        row["ttm"], row["ttm_note"] = ttm_ratio(model.metrics[num], model.metrics[den], period)
        row["ytd"], row["ytd_note"] = _ytd_ratio(model, metric, period, fy_start)
    else:
        row["ttm"], row["ttm_note"] = ttm(series, period)
        row["ytd"], row["ytd_note"] = ytd(series, period, fy_start)
    return row


def _line_row(key: str, label: str, series: Series, period: Period, ttm_revenue: Decimal | None,
              extra: dict[str, Any]) -> dict[str, Any]:
    t, t_note = ttm(series, period)
    share = None
    if t is not None and ttm_revenue is not None and ttm_revenue > 0:
        share = t / ttm_revenue * 100
    others = sorted(v for p, v in series.items() if p != period and v is not None)
    median = None
    if len(others) >= 3:
        mid = len(others) // 2
        median = others[mid] if len(others) % 2 else (others[mid - 1] + others[mid]) / 2
    return {"key": key, "label": label, **extra, "value": series.get(period),
            "mom": compare(series, period, 1), "yoy": compare(series, period, 12),
            "ttm": t, "ttm_note": t_note, "ttm_pct_of_revenue": share, "median_other_months": median}


def _assumptions(model: IncomeStatementModel, config: Config) -> list[str]:
    out = [
        "Calculated figures are sums of individual accounts; the workbook's subtotal and total rows are used only to reconcile against.",
        "Blank account cells are treated as zero (company config)." if config.blank_cells_as_zero
        else "Blank account cells are treated as missing, never as zero; totals that depend on them are not calculated.",
        "Percentage changes are relative to the absolute value of the earlier figure and are not given when it is zero or the sign changes.",
        "Margins are percentages of revenue; TTM and YTD margins are ratios of the period totals, not averages of monthly margins.",
        "Balance-sheet figures are month-end balances; they are compared month to month and year on year, never summed into TTM.",
        f"Income statement months are calendar months; fiscal year starts in month {config.fiscal_year_start_month} (used for YTD and the earnings roll-forward).",
    ]
    if model.classification_configured:
        reclass = [l.label for l in model.lines.get("opex", []) if l.classification in ("non_operating", "income_tax")]
        out.append("'Core' figures exclude these operating-expense accounts, classified as non-operating or income tax "
                   f"in the company config (an accounting judgment, not data from the workbook): {', '.join(reclass) or 'none found'}.")
    else:
        out.append("No account classification is configured: operating expenses are as presented in the workbook and may "
                   "include interest, tax or other non-operating items.")
    if not model.sections_present.get("other_income") and not model.sections_present.get("other_expense"):
        out.append("The workbook has no Other Income / Other Expense sections, so net income equals operating income as presented.")
    th = config.thresholds
    out.append(f"Materiality for highlights: the greater of {th['materiality_abs']:,} and "
               f"{th['materiality_pct_of_revenue'] * 100:g}% of the month's revenue; line items also need a "
               f"{th['min_mom_pct'] * 100:g}% change; margins need {th['margin_move_pp']:g} percentage points.")
    return out


def _limitations(periods: list[Period], model: IncomeStatementModel) -> list[str]:
    out = [f"{len(periods)} months of data ({periods[0].label} to {periods[-1].label})."]
    ttm_from = next((p for p in periods if ttm(model.metrics["revenue"], p)[0] is not None), None)
    out.append(f"TTM figures are available from {ttm_from.label}." if ttm_from else
               "Fewer than 12 consecutive complete months: no TTM figures.")
    if len(periods) < 24:
        out.append("Less than 24 months of history: one year-on-year comparison per month at most, so seasonality "
                   "cannot be separated reliably from trend, and TTM cannot be compared with the prior TTM.")
    out.append("The workbook holds monthly account totals only (no transactions, budget, headcount or volumes), so the "
               "causes of movements cannot be determined from it; explanations must be confirmed with the business.")
    return out


def analyze(workbook: str | Path, config: Config | None = None, period: str | None = None) -> dict[str, Any]:
    config = config or Config.load()
    wb = load_workbook(workbook, config)
    issues = list(wb.issues)
    model = build_income_statement(wb.income_statement, config, issues)
    bs = build_balance_sheet(wb.balance_sheet, config, issues) if wb.balance_sheet is not None else None
    checks = run_validation(wb, model, bs, config, issues)

    periods = model.periods
    if period is None:
        target = periods[-1]
    else:
        target = Period.from_key(period)
        if target not in periods:
            raise AnalysisError(f"Period {target.key} is not in the workbook. Available: {periods[0].key} to {periods[-1].key}")
    fy = config.fiscal_year_start_month

    metrics_present = [m for m in METRIC_INFO if m in model.metrics]
    if not model.classification_configured:
        metrics_present = [m for m in metrics_present if m not in ("non_operating_in_opex", "core_opex",
                                                                  "core_operating_income", "core_operating_margin")]
    ttm_revenue, _ = ttm(model.metrics["revenue"], target)

    def line_rows(lines: list[Line]) -> list[dict[str, Any]]:
        return [_line_row(l.key, l.label, l.values, target, ttm_revenue,
                          {"section": l.section, "category": l.category, "classification": l.classification,
                           "source_row": l.source_row}) for l in lines]

    ttm_series: dict[str, Any] = {}
    for metric in metrics_present:
        unit = METRIC_INFO[metric][1]
        values, notes = {}, {}
        for p in periods:
            if unit == "percent":
                num, den = RATIOS[metric]
                values[p.key], note = ttm_ratio(model.metrics[num], model.metrics[den], p)
            else:
                values[p.key], note = ttm(model.metrics[metric], p)
            if note:
                notes[p.key] = note
        ttm_series[metric] = {"values": values, "notes": notes}

    highlights, dropped = build_highlights(model, target, config)

    balance_sheet: dict[str, Any] | None = None
    if bs is not None:
        balance_sheet = {
            "sheet": wb.balance_sheet.sheet,
            "groups_found": bs.groups_found,
            "ttm": "not_applicable: balance-sheet figures are point-in-time balances",
            "monthly": {m: {"label": BS_METRIC_INFO[m], "values": _series(s)} for m, s in bs.metrics.items()},
            "analysis_period": [{"metric": m, "label": BS_METRIC_INFO[m], **balance_sheet_view(s, target)}
                                for m, s in bs.metrics.items()],
            "highlights": balance_sheet_highlights(bs.metrics, BS_METRIC_INFO, target, config),
        }

    sorted_issues = sort_issues(issues)
    status = overall_status(sorted_issues)
    counts = {sev: sum(1 for i in sorted_issues if i.severity == sev) for sev in (ERROR, WARNING, INFO)}

    result = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": __version__,
        "source": {
            "workbook": Path(workbook).name,
            "sha256": wb.sha256,
            "sheets": wb.sheet_names,
            "income_statement_sheet": wb.income_statement.sheet,
            "balance_sheet_sheet": wb.balance_sheet.sheet if wb.balance_sheet else None,
            "title_lines": wb.income_statement.title_lines,
        },
        "config": {
            "source": Path(config.source).name if config.source else None,
            "classification_configured": model.classification_configured,
            "opex_rollups_configured": model.rollups_configured,
            "thresholds": config.thresholds,
            "fiscal_year_start_month": fy,
        },
        "company_name": config.company_name or (wb.income_statement.title_lines[1]
                                               if len(wb.income_statement.title_lines) > 2 else None),
        "currency": config.currency,
        "status": status,
        "periods": {
            "available": [p.key for p in periods],
            "analysis_period": target.key,
            "prior_period": target.shift(-1).key if target.shift(-1) in periods else None,
            "same_period_last_year": target.shift(-12).key if target.shift(-12) in periods else None,
            "missing_periods": sorted({i.period for i in sorted_issues if i.code == "missing_period"}),
            "materiality_threshold": materiality(model, target, config.thresholds),
        },
        "income_statement": {
            "sheet": wb.income_statement.sheet,
            "sections": model.sections_present,
            "headline_metrics": list(headline_metrics(model)),
            "monthly": {m: {"label": METRIC_INFO[m][0], "unit": METRIC_INFO[m][1], "basis": "calculated",
                            "values": _series(model.metrics[m]),
                            "notes": {p.key: r for p, r in sorted(model.ratio_reasons.get(m, {}).items())}}
                        for m in metrics_present},
            "ttm": ttm_series,
            "reported_totals": {k: {"label": r.label, "source_row": r.source_row, "basis": "reported",
                                    "values": _series(r.values)} for k, r in model.reported.items()},
            "opex_categories": [{"name": name, "accounts": model.category_members[name],
                                 "values": _series(model.categories[name])} for name in model.categories],
            "accounts": [{"key": l.key, "label": l.label, "section": l.section, "path": l.path,
                          "category": l.category, "classification": l.classification,
                          "source_row": l.source_row, "values": _series(l.values)} for l in model.all_lines()],
        },
        "analysis_period": {
            "period": target.key,
            "metrics": [_metric_row(model, m, target, fy) for m in metrics_present],
            "revenue_streams": line_rows(model.lines.get("revenue", [])),
            "cogs_accounts": line_rows(model.lines.get("cogs", [])),
            "opex_categories": [_line_row(f"category:{n}", n, s, target, ttm_revenue, {"accounts": len(model.category_members[n])})
                                for n, s in model.categories.items()],
            "opex_accounts": line_rows(model.lines.get("opex", [])),
            "other_accounts": line_rows([l for s in ("other_income", "other_expense") for l in model.lines.get(s, [])]),
        },
        "balance_sheet": balance_sheet,
        "highlights": highlights,
        "highlights_dropped": dropped,
        "history_outliers": history_outliers(model, config),
        "validation": {"status": status, "counts": counts, "checks": checks,
                       "issues": [i.to_dict() for i in sorted_issues]},
        "assumptions": _assumptions(model, config),
        "limitations": _limitations(periods, model),
    }
    return rnd(result)

