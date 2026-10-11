"""Reconciliation and data-quality checks.

Each check returns a summary entry (name, status, what was compared) and adds
Issues for anything that failed. A failed check on a figure the commentary relies
on (section totals, gross profit, net income, balance sheet balancing) is an ERROR:
the analysis must not be narrated until a reviewer resolves it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .calculations import BalanceSheetModel, IncomeStatementModel, combine, sum_series
from .config import Config, label_in
from .issues import ERROR, INFO, WARNING, Issue
from .loader import LoadedWorkbook, Series, Statement


def _diffs(calculated: Series, reported: Series, tolerance: Decimal) -> tuple[list[dict[str, Any]], int, int]:
    """Return (mismatches, periods compared, periods skipped because an input was missing)."""
    mismatches, compared, skipped = [], 0, 0
    for p in sorted(set(calculated) | set(reported)):
        c, r = calculated.get(p), reported.get(p)
        if c is None or r is None:
            skipped += 1
            continue
        compared += 1
        if abs(c - r) > tolerance:
            mismatches.append({"period": p.key, "calculated": str(c), "reported": str(r), "difference": str(c - r)})
    return mismatches, compared, skipped


def _check(name: str, mismatches: list, compared: int, skipped: int, description: str) -> dict[str, Any]:
    status = "fail" if mismatches else ("skipped" if compared == 0 else "pass")
    return {"check": name, "status": status, "description": description,
            "periods_compared": compared, "periods_skipped": skipped, "mismatches": len(mismatches)}


def check_subtotals(stmt: Statement, critical_groups: set[str], tolerance: Decimal,
                    issues: list[Issue]) -> list[dict[str, Any]]:
    """Every 'Total X' row in the workbook vs the sum of the leaf accounts under X."""
    results = []
    for group in stmt.all_groups():
        if group.total is None:
            continue
        calculated = sum_series(group.leaves(), stmt.periods)
        mismatches, compared, skipped = _diffs(calculated, group.total.values, tolerance)
        results.append(_check(f"subtotal:{stmt.sheet}:{group.label}", mismatches, compared, skipped,
                              f"'{group.total.label}' (row {group.total.sheet_row}) equals the sum of its "
                              f"{len(group.leaves())} accounts"))
        if mismatches:
            severity = ERROR if group.label in critical_groups else WARNING
            issues.append(Issue("subtotal_mismatch", severity,
                                f"'{group.total.label}' does not equal the sum of its accounts in {len(mismatches)} month(s); "
                                "possible double-counting, hidden rows or a broken formula in the workbook",
                                sheet=stmt.sheet, account=group.total.label, details={"mismatches": mismatches}))
    return results


def check_income_statement(model: IncomeStatementModel, sheet: str, tolerance: Decimal,
                           issues: list[Issue]) -> list[dict[str, Any]]:
    """Reported derived lines (gross profit, operating income, net income) vs pipeline calculations."""
    results = []
    for metric in ("gross_profit", "operating_income", "net_income"):
        rep = model.reported.get(metric)
        if rep is None or metric not in model.metrics:
            results.append({"check": f"derived:{metric}", "status": "skipped",
                            "description": f"No reported {metric.replace('_', ' ')} line to reconcile against"})
            continue
        mismatches, compared, skipped = _diffs(model.metrics[metric], rep.values, tolerance)
        results.append(_check(f"derived:{metric}", mismatches, compared, skipped,
                              f"Reported '{rep.label}' (row {rep.source_row}) equals the pipeline's {metric.replace('_', ' ')}"))
        if mismatches:
            issues.append(Issue("derived_mismatch", ERROR,
                                f"Reported '{rep.label}' differs from the calculated {metric.replace('_', ' ')} "
                                f"in {len(mismatches)} month(s)", sheet=sheet, account=rep.label,
                                details={"mismatches": mismatches}))

    # Internal consistency: opex categories must add back to total opex (each account in exactly one category).
    if "opex_total" in model.metrics:
        cat_total = combine(model.periods, [(1, s) for s in model.categories.values()])
        mismatches, compared, skipped = _diffs(cat_total, model.metrics["opex_total"], tolerance)
        results.append(_check("opex_categories_complete", mismatches, compared, skipped,
                              "Operating-expense categories add up to total operating expenses"))
        if mismatches:
            issues.append(Issue("category_mismatch", ERROR, "Operating-expense categories do not add up to the total",
                                sheet=sheet, details={"mismatches": mismatches}))
    return results


def check_negative_values(model: IncomeStatementModel, config: Config, sheet: str, issues: list[Issue]) -> dict[str, Any]:
    """Negative revenue or expense postings usually mean a reversal, credit or reclass a reviewer should confirm."""
    expected = config.income_statement["expected_negative_accounts"]
    flagged = 0
    for line in model.all_lines():
        if label_in(line.label, expected):
            continue
        negative = sorted(p for p, v in line.values.items() if v is not None and v < 0)
        if not negative:
            continue
        flagged += 1
        kind = "revenue" if line.section in ("revenue", "other_income") else "expense"
        issues.append(Issue("negative_value", INFO,
                            f"{kind.capitalize()} account '{line.label}' is negative in "
                            f"{', '.join(p.label for p in negative)} (possible reversal, credit or reclass)",
                            sheet=sheet, account=line.label,
                            details={"periods": [p.key for p in negative],
                                     "values": {p.key: str(line.values[p]) for p in negative}}))
    return {"check": "negative_values", "status": "pass" if flagged == 0 else "flagged",
            "description": "Revenue and expense accounts with negative postings (not configured as expected-negative)",
            "accounts_flagged": flagged}


def check_balance_sheet(bs: BalanceSheetModel, sheet: str, tolerance: Decimal, issues: list[Issue]) -> dict[str, Any]:
    needed = ("total_assets", "total_liabilities", "total_equity")
    if any(k not in bs.metrics for k in needed):
        return {"check": "balance_sheet_balances", "status": "skipped",
                "description": "Assets, liabilities or equity group not found"}
    le = combine(bs.periods, [(1, bs.metrics["total_liabilities"]), (1, bs.metrics["total_equity"])])
    mismatches, compared, skipped = _diffs(bs.metrics["total_assets"], le, tolerance)
    if mismatches:
        issues.append(Issue("balance_sheet_out_of_balance", ERROR,
                            f"Assets do not equal liabilities plus equity in {len(mismatches)} month(s)", sheet=sheet,
                            details={"mismatches": mismatches}))
    return _check("balance_sheet_balances", mismatches, compared, skipped, "Total assets = total liabilities + total equity")


def check_earnings_rollforward(model: IncomeStatementModel, bs: BalanceSheetModel, config: Config, sheet: str,
                               tolerance: Decimal, issues: list[Issue]) -> dict[str, Any]:
    """Change in the balance sheet's current-year-earnings account each month = income statement net income."""
    desc = "Monthly change in current-year earnings (balance sheet) equals net income (income statement)"
    if bs.current_year_earnings is None:
        return {"check": "earnings_rollforward", "status": "skipped", "description": desc + " - account not found"}
    cye = bs.current_year_earnings.values
    ni = model.metrics["net_income"]
    fy_start = config.fiscal_year_start_month
    implied: Series = {}
    for p in sorted(cye):
        if p not in ni:
            continue
        if p.month == fy_start:
            implied[p] = cye[p]
        elif p.shift(-1) in cye:
            prev = cye[p.shift(-1)]
            implied[p] = None if cye[p] is None or prev is None else cye[p] - prev
    mismatches, compared, skipped = _diffs(implied, {p: ni[p] for p in implied}, tolerance)
    if mismatches:
        issues.append(Issue("earnings_rollforward_mismatch", WARNING,
                            f"Balance-sheet '{bs.current_year_earnings.label}' movement differs from net income in "
                            f"{len(mismatches)} month(s); statements may be from different closes or the fiscal year "
                            "start is configured wrongly", sheet=sheet, details={"mismatches": mismatches}))
    return _check("earnings_rollforward", mismatches, compared, skipped, desc)


def run_validation(wb: LoadedWorkbook, model: IncomeStatementModel, bs: BalanceSheetModel | None,
                   config: Config, issues: list[Issue]) -> list[dict[str, Any]]:
    tolerance = Decimal(str(config.reconciliation_tolerance))
    critical = {label for label in model.sections_present.values() if label}
    checks = check_subtotals(wb.income_statement, critical, tolerance, issues)
    checks += check_income_statement(model, wb.income_statement.sheet, tolerance, issues)
    checks.append(check_negative_values(model, config, wb.income_statement.sheet, issues))
    if bs is not None and wb.balance_sheet is not None:
        bs_critical = {label for label in bs.groups_found.values() if label}
        checks += check_subtotals(wb.balance_sheet, bs_critical, tolerance, issues)
        checks.append(check_balance_sheet(bs, wb.balance_sheet.sheet, tolerance, issues))
        checks.append(check_earnings_rollforward(model, bs, config, wb.balance_sheet.sheet, tolerance, issues))

    missing = [i for i in issues if i.code == "missing_value"]
    checks.append({"check": "missing_values", "status": "flagged" if missing else "pass",
                   "description": "Blank account cells (not treated as zero)", "accounts_flagged": len(missing)})
    gaps = [i for i in issues if i.code == "missing_period"]
    checks.append({"check": "period_continuity", "status": "flagged" if gaps else "pass",
                   "description": "Months are consecutive with no gaps", "missing_periods": sorted({i.period for i in gaps})})
    return checks
