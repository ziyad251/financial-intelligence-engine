"""Period comparisons: month-over-month, year-over-year, trailing twelve months, year to date.

Income-statement figures are flows, so TTM and YTD are sums of consecutive months.
Balance-sheet figures are point-in-time balances: they are compared month to month
and against twelve months earlier, never summed (see balance_sheet_view).

Every comparison that cannot be made returns None with a `note` explaining why:
  comparison_period_not_in_data  the earlier month is outside or missing from the workbook
  missing_value                  an input month has a blank or unusable value
  zero_base                      the earlier value is zero, so a % change is undefined
  sign_change                    the value crossed zero, so a % change is not meaningful
  negative_base                  % change was calculated against a negative base (|prior|)
  insufficient_history           fewer than 12 months of data end at this month
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .loader import Series
from .periods import Period


def change(current: Decimal | None, prior: Decimal | None) -> dict[str, Any]:
    """Absolute and % change for amounts. % is relative to |prior|."""
    out: dict[str, Any] = {"current": current, "prior": prior, "abs_change": None, "pct_change": None, "note": None}
    if current is None or prior is None:
        out["note"] = "missing_value"
        return out
    out["abs_change"] = current - prior
    if prior == 0:
        out["note"] = "zero_base"
    elif (prior < 0 < current) or (current < 0 < prior):
        out["note"] = "sign_change"
    else:
        out["pct_change"] = (current - prior) / abs(prior) * 100
        if prior < 0:
            out["note"] = "negative_base"
    return out


def pp_change(current: Decimal | None, prior: Decimal | None) -> dict[str, Any]:
    """Change in a percentage metric, in percentage points. No % of a %."""
    out: dict[str, Any] = {"current": current, "prior": prior, "pp_change": None, "note": None}
    if current is None or prior is None:
        out["note"] = "missing_value"
    else:
        out["pp_change"] = current - prior
    return out


def compare(series: Series, period: Period, months_back: int, unit: str = "amount") -> dict[str, Any]:
    base = period.shift(-months_back)
    fn = pp_change if unit == "percent" else change
    if base not in series:
        out = fn(series.get(period), None)
        out["note"] = "comparison_period_not_in_data"
    else:
        out = fn(series.get(period), series[base])
    out["comparison_period"] = base.key
    return out


def window_sum(series: Series, end: Period, months: int) -> tuple[Decimal | None, str | None]:
    window = [end.shift(-i) for i in range(months)]
    available = sorted(series)
    if not available or window[-1] < available[0]:
        return None, "insufficient_history"
    if any(p not in series for p in window):
        return None, "comparison_period_not_in_data"
    values = [series[p] for p in window]
    if any(v is None for v in values):
        return None, "missing_value"
    return sum(values, Decimal(0)), None


def ttm(series: Series, end: Period) -> tuple[Decimal | None, str | None]:
    return window_sum(series, end, 12)


def ytd(series: Series, end: Period, fiscal_year_start_month: int) -> tuple[Decimal | None, str | None]:
    months = (end.month - fiscal_year_start_month) % 12 + 1
    return window_sum(series, end, months)


def ttm_ratio(numerator: Series, denominator: Series, end: Period) -> tuple[Decimal | None, str | None]:
    """TTM margin = TTM numerator / TTM denominator (not an average of monthly margins)."""
    num, reason = ttm(numerator, end)
    den, reason_d = ttm(denominator, end)
    if num is None or den is None:
        return None, reason or reason_d
    if den == 0:
        return None, "zero_denominator"
    if den < 0:
        return None, "negative_denominator"
    return num / den * 100, None


def balance_sheet_view(series: Series, period: Period) -> dict[str, Any]:
    """Point-in-time balance: value, change vs prior month-end and vs 12 months earlier. No TTM sum."""
    return {
        "value": series.get(period),
        "mom": compare(series, period, 1),
        "vs_12_months_earlier": compare(series, period, 12),
    }
