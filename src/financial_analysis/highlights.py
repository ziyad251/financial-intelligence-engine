"""Deterministic selection of the movements an owner should hear about.

Rules (thresholds come from config.thresholds):
  materiality(period) = max(materiality_abs, materiality_pct_of_revenue * |revenue(period)|)

  headline metric change   |abs change| >= materiality                         (MoM and YoY)
  margin change            |pp change| >= margin_move_pp                       (MoM and YoY)
  line-item change         |abs change| >= materiality and
                           (|% change| >= min_mom_pct or % undefined)           (MoM; YoY for streams/categories)
  unusual vs history       |value - median of all other months| >= materiality and
                           >= one_off_min_ratio_to_median * |median|

Highlights state what moved and by how much. They never state why: causes are for
the reviewer, and the skill must present any explanation as a question or hypothesis.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from .calculations import METRIC_INFO, IncomeStatementModel, Line
from .config import Config
from .loader import Series
from .periods import Period
from .trends import compare

FAVOURABLE_WHEN_UP = {"revenue", "gross_profit", "operating_income", "core_operating_income", "net_income",
                      "other_income", "gross_margin", "operating_margin", "core_operating_margin", "net_margin"}


def headline_metrics(model: IncomeStatementModel) -> tuple[str, ...]:
    """Core (reclassified) figures when account classes are configured, as-presented figures otherwise."""
    if model.classification_configured:
        return ("revenue", "gross_profit", "core_opex", "core_operating_income", "net_income")
    return ("revenue", "gross_profit", "opex_total", "operating_income", "net_income")


def margin_metrics(model: IncomeStatementModel) -> tuple[str, ...]:
    return ("gross_margin", "core_operating_margin" if model.classification_configured else "operating_margin")


def materiality(model: IncomeStatementModel, period: Period, thresholds: dict[str, Any]) -> Decimal:
    base = Decimal(str(thresholds["materiality_abs"]))
    revenue = model.metrics["revenue"].get(period)
    if revenue is None:
        return base
    return max(base, Decimal(str(thresholds["materiality_pct_of_revenue"])) * abs(revenue))


def _effect(up_is_good: bool, delta: Decimal) -> str:
    if delta == 0:
        return "neutral"
    return "favourable" if (delta > 0) == up_is_good else "unfavourable"


def _line_scope(line: Line) -> str:
    return {"revenue": "revenue_stream", "cogs": "cogs_account", "opex": "opex_account",
            "other_income": "other_income_account", "other_expense": "other_expense_account"}[line.section]


def _median_other(series: Series, period: Period) -> Decimal | None:
    others = [v for p, v in series.items() if p != period and v is not None]
    if len(others) < 3:
        return None
    return Decimal(str(statistics.median(others)))


def _items(model: IncomeStatementModel) -> list[tuple[str, str, str, Series, bool]]:
    """(scope, key, label, series, up_is_good) for every line-level series."""
    out = []
    for line in model.all_lines():
        if line.section == "opex" and model.category_members.get(line.category) == [line.label]:
            continue  # an account that is its own category is tested once, as the category
        out.append((_line_scope(line), line.key, line.label, line.values, line.section in ("revenue", "other_income")))
    for name, series in model.categories.items():
        out.append(("opex_category", f"category:{name}", name, series, False))
    return out


def outliers_for_period(model: IncomeStatementModel, period: Period, thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    mat = materiality(model, period, thresholds)
    ratio = Decimal(str(thresholds["one_off_min_ratio_to_median"]))
    found = []
    for line in model.all_lines():  # accounts only: categories are aggregates of these
        scope, key, label, series = _line_scope(line), line.key, line.label, line.values
        up_good = line.section in ("revenue", "other_income")
        value, median = series.get(period), _median_other(series, period)
        if value is None or median is None:
            continue
        deviation = value - median
        if abs(deviation) >= mat and abs(deviation) >= ratio * abs(median):
            found.append({"type": "unusual_vs_history", "scope": scope, "key": key, "label": label,
                          "period": period.key, "comparison": "median_of_other_months",
                          "current": value, "baseline": median, "abs_change": deviation, "pct_change": None,
                          "direction": "increase" if deviation > 0 else "decrease",
                          "profit_effect": _effect(up_good, deviation), "materiality_threshold": mat})
    return found


def build_highlights(model: IncomeStatementModel, period: Period, config: Config) -> tuple[list[dict[str, Any]], int]:
    """Highlights for the analysis period, most important first. Returns (highlights, number dropped by the cap)."""
    th = config.thresholds
    mat = materiality(model, period, th)
    min_pct = Decimal(str(th["min_mom_pct"])) * 100
    pp = Decimal(str(th["margin_move_pp"]))
    headline, margins, lines = [], [], []

    for metric in headline_metrics(model):
        if metric not in model.metrics:
            continue
        for months, kind in ((1, "mom_change"), (12, "yoy_change")):
            c = compare(model.metrics[metric], period, months)
            if c["abs_change"] is not None and abs(c["abs_change"]) >= mat:
                headline.append({"type": kind, "scope": "metric", "key": metric, "label": METRIC_INFO[metric][0],
                                 "period": period.key, "comparison": c["comparison_period"], "current": c["current"],
                                 "baseline": c["prior"], "abs_change": c["abs_change"], "pct_change": c["pct_change"],
                                 "note": c["note"], "direction": "increase" if c["abs_change"] > 0 else "decrease",
                                 "profit_effect": _effect(metric in FAVOURABLE_WHEN_UP, c["abs_change"]),
                                 "materiality_threshold": mat})

    for metric in margin_metrics(model):
        if metric not in model.metrics:
            continue
        for months, kind in ((1, "mom_change"), (12, "yoy_change")):
            c = compare(model.metrics[metric], period, months, unit="percent")
            if c["pp_change"] is not None and abs(c["pp_change"]) >= pp:
                margins.append({"type": kind, "scope": "margin", "key": metric, "label": METRIC_INFO[metric][0],
                                "period": period.key, "comparison": c["comparison_period"], "current": c["current"],
                                "baseline": c["prior"], "pp_change": c["pp_change"],
                                "direction": "increase" if c["pp_change"] > 0 else "decrease",
                                "profit_effect": _effect(True, c["pp_change"]), "threshold_pp": pp})

    for scope, key, label, series, up_good in _items(model):
        checks = [(1, "mom_change")]
        if scope in ("revenue_stream", "opex_category"):
            checks.append((12, "yoy_change"))
        for months, kind in checks:
            c = compare(series, period, months)
            delta = c["abs_change"]
            if delta is None or abs(delta) < mat:
                continue
            if c["pct_change"] is not None and abs(c["pct_change"]) < min_pct:
                continue
            lines.append({"type": kind, "scope": scope, "key": key, "label": label, "period": period.key,
                          "comparison": c["comparison_period"], "current": c["current"], "baseline": c["prior"],
                          "abs_change": delta, "pct_change": c["pct_change"], "note": c["note"],
                          "direction": "increase" if delta > 0 else "decrease",
                          "profit_effect": _effect(up_good, delta), "materiality_threshold": mat})
    lines += outliers_for_period(model, period, th)

    by_size = lambda h: (-abs(h.get("abs_change") or h.get("pp_change") or 0), h["type"], h["key"])
    ordered = sorted(headline, key=by_size) + sorted(margins, key=by_size) + sorted(lines, key=by_size)
    cap = int(th["max_highlights"])
    for i, h in enumerate(ordered, 1):
        h["rank"] = i
    return ordered[:cap], max(0, len(ordered) - cap)


def history_outliers(model: IncomeStatementModel, config: Config, limit: int = 15) -> list[dict[str, Any]]:
    """Unusual account values across every month in the workbook (one-offs, seasonal peaks)."""
    found = [o for p in model.periods for o in outliers_for_period(model, p, config.thresholds)]
    found.sort(key=lambda h: (-abs(h["abs_change"]), h["period"], h["key"]))
    return found[:limit]


def balance_sheet_highlights(metrics: dict[str, Series], labels: dict[str, str], period: Period,
                             config: Config) -> list[dict[str, Any]]:
    """Month-end balance movements (vs prior month-end and 12 months earlier) of at least bs_materiality_abs."""
    threshold = Decimal(str(config.thresholds["bs_materiality_abs"]))
    found = []
    for metric, series in metrics.items():
        for months, kind in ((1, "mom_change"), (12, "yoy_change")):
            c = compare(series, period, months)
            if c["abs_change"] is None or abs(c["abs_change"]) < threshold:
                continue
            found.append({"type": kind, "scope": "balance_sheet", "key": metric, "label": labels[metric],
                          "period": period.key, "comparison": c["comparison_period"], "current": c["current"],
                          "baseline": c["prior"], "abs_change": c["abs_change"], "pct_change": c["pct_change"],
                          "note": c["note"], "direction": "increase" if c["abs_change"] > 0 else "decrease",
                          "materiality_threshold": threshold})
    found.sort(key=lambda h: (h["type"], -abs(h["abs_change"]), h["key"]))
    return found
