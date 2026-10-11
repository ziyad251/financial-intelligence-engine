"""Deterministic financial calculations built only from leaf accounts.

Every calculated figure here is a sum of leaf rows (never of subtotals), so no
account is counted twice however the workbook nests its groups. Reported
subtotals are kept alongside, for validation to reconcile against.

A missing input makes the dependent result None for that month: a total is never
calculated from a partial set of accounts. Ratios with a zero or negative
denominator are None with a reason rather than an invented percentage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .config import Config, label_in, normalize_label
from .issues import ERROR, INFO, WARNING, AnalysisError, Issue
from .loader import DERIVED, LEAF, SUBTOTAL, Group, Row, Series, Statement
from .periods import Period

ZERO = Decimal(0)

IS_SECTIONS = ("revenue", "cogs", "opex", "other_income", "other_expense")
EXPENSE_SECTIONS = ("cogs", "opex", "other_expense")

# metric -> (label, unit). Amounts are period flows; ratios are percentages of revenue.
METRIC_INFO: dict[str, tuple[str, str]] = {
    "revenue": ("Revenue", "amount"),
    "cogs": ("Cost of goods sold", "amount"),
    "gross_profit": ("Gross profit", "amount"),
    "opex_total": ("Operating expenses (as presented)", "amount"),
    "non_operating_in_opex": ("Non-operating items inside operating expenses", "amount"),
    "core_opex": ("Core operating expenses", "amount"),
    "operating_income": ("Operating income (as presented)", "amount"),
    "core_operating_income": ("Core operating income", "amount"),
    "other_income": ("Other income", "amount"),
    "other_expense": ("Other expenses", "amount"),
    "net_income": ("Net income", "amount"),
    "non_cash_expenses": ("Non-cash expenses", "amount"),
    "gross_margin": ("Gross margin", "percent"),
    "operating_margin": ("Operating margin (as presented)", "percent"),
    "core_operating_margin": ("Core operating margin", "percent"),
    "net_margin": ("Net margin", "percent"),
    "opex_to_revenue": ("Operating expenses as % of revenue", "percent"),
}

# ratio metric -> (numerator, denominator)
RATIOS: dict[str, tuple[str, str]] = {
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "core_operating_margin": ("core_operating_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "opex_to_revenue": ("opex_total", "revenue"),
}

BS_METRIC_INFO: dict[str, str] = {
    "cash": "Cash and cash equivalents",
    "current_assets": "Current assets",
    "current_liabilities": "Current liabilities",
    "working_capital": "Working capital (current assets - current liabilities)",
    "total_assets": "Total assets",
    "total_liabilities": "Total liabilities",
    "total_equity": "Total equity",
}


def sum_series(rows: list[Row] | list["Line"], periods: list[Period]) -> Series:
    """Sum of rows (or lines) per period; None for any period where an input is missing."""
    out: Series = {}
    for p in periods:
        values = [r.values.get(p) for r in rows]
        out[p] = None if any(v is None for v in values) else sum(values, ZERO)
    return out


def combine(periods: list[Period], terms: list[tuple[int, Series]]) -> Series:
    """Signed sum of series per period: combine(ps, [(1, a), (-1, b)]) = a - b."""
    out: Series = {}
    for p in periods:
        values = [s.get(p) for _, s in terms]
        out[p] = None if any(v is None for v in values) else sum((sign * v for (sign, _), v in zip(terms, values)), ZERO)
    return out


def safe_ratio(numerator: Decimal | None, denominator: Decimal | None) -> tuple[Decimal | None, str | None]:
    if numerator is None or denominator is None:
        return None, "missing_value"
    if denominator == 0:
        return None, "zero_denominator"
    if denominator < 0:
        return None, "negative_denominator"
    return numerator / denominator, None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_label(text)).strip("-") or "line"


@dataclass
class Line:
    key: str
    label: str
    section: str
    path: str
    source_row: int
    values: Series
    category: str | None = None
    classification: str | None = None  # non_operating | income_tax | non_cash


@dataclass
class ReportedLine:
    label: str
    source_row: int
    values: Series


@dataclass
class IncomeStatementModel:
    periods: list[Period]
    sections_present: dict[str, str | None]  # section -> group label in the workbook
    lines: dict[str, list[Line]]  # section -> leaf lines
    metrics: dict[str, Series]
    ratio_reasons: dict[str, dict[Period, str]]
    reported: dict[str, ReportedLine]
    categories: dict[str, Series]
    category_members: dict[str, list[str]]
    classification_configured: bool
    rollups_configured: bool
    unmapped: list[Row] = field(default_factory=list)

    def all_lines(self) -> list[Line]:
        return [line for section in IS_SECTIONS for line in self.lines.get(section, [])]


@dataclass
class BalanceSheetModel:
    periods: list[Period]
    metrics: dict[str, Series]
    groups_found: dict[str, str | None]
    current_year_earnings: ReportedLine | None


def _section_groups(stmt: Statement, config: Config, issues: list[Issue]) -> dict[str, Group | None]:
    aliases = config.income_statement["sections"]
    found: dict[str, Group | None] = {}
    claimed: dict[int, str] = {}
    for section in IS_SECTIONS:
        candidates = [g for g in stmt.find_groups(aliases.get(section, [])) if id(g) not in claimed]
        if len(candidates) > 1 and len(candidates[0].path) == len(candidates[1].path):
            issues.append(Issue("ambiguous_section", ERROR,
                                f"Several groups match the {section} section: {[g.label for g in candidates]}",
                                sheet=stmt.sheet))
        found[section] = candidates[0] if candidates else None
        if candidates:
            claimed[id(candidates[0])] = section
    # A section nested inside another mapped section (e.g. "Expenses" inside "Income") is a mapping error.
    for section, group in found.items():
        if group is None:
            continue
        for other, parent in found.items():
            if other != section and parent is not None and any(g is group for g in parent.walk()):
                issues.append(Issue("nested_section", ERROR,
                                    f"The {section} section '{group.label}' sits inside the {other} section '{parent.label}'",
                                    sheet=stmt.sheet))
    return found


def _classify(lines: list[Line], config: Config, issues: list[Issue], sheet: str) -> bool:
    classes: dict[str, list[str]] = config.income_statement["account_classes"]
    configured = any(classes.get(k) for k in classes)
    for cls, names in classes.items():
        for name in names:
            matches = [line for line in lines if normalize_label(line.label) == normalize_label(name)]
            if not matches:
                issues.append(Issue("config_account_not_found", WARNING,
                                    f"Configured {cls} account '{name}' is not in the income statement", sheet=sheet))
            for line in matches:
                if cls == "non_cash":
                    continue  # non_cash is an overlay; it does not change where the account sits
                if line.section != "opex":
                    issues.append(Issue("classification_not_applied", WARNING,
                                        f"'{line.label}' is classified {cls} but sits in {line.section}; "
                                        "only operating-expense accounts are reclassified", sheet=sheet, account=line.label))
                    continue
                line.classification = cls
    return configured


def _assign_categories(lines: list[Line], opex_group: Group | None, config: Config, issues: list[Issue],
                       sheet: str) -> tuple[list[str], bool]:
    """Assign every opex line to exactly one category. Returns (category order, rollups_configured)."""
    rollups = config.opex_rollups
    order: list[str] = []
    if rollups:
        other = "Other operating expenses"
        unmatched = []
        for line in lines:
            for name, patterns in rollups:
                if any(p.search(line.path) for p in patterns):
                    line.category = name
                    break
            else:
                line.category = other
                unmatched.append(line.label)
        order = [name for name, _ in rollups]
        if unmatched:
            order.append(other)
            issues.append(Issue("uncategorised_opex", INFO,
                                f"{len(unmatched)} operating-expense accounts match no configured category and are "
                                f"reported under '{other}'", sheet=sheet, details={"accounts": unmatched}))
        return order, True
    # Default: one category per direct child of the operating-expense section.
    child_of: dict[int, str] = {}
    if opex_group is not None:
        for child in opex_group.children:
            for leaf in child.leaves():
                child_of[leaf.sheet_row] = child.label
    for line in lines:
        line.category = child_of.get(line.source_row, line.label)
        if line.category not in order:
            order.append(line.category)
    return order, False


def build_income_statement(stmt: Statement, config: Config, issues: list[Issue]) -> IncomeStatementModel:
    periods = stmt.periods
    groups = _section_groups(stmt, config, issues)
    if groups["revenue"] is None:
        raise AnalysisError(f"Sheet '{stmt.sheet}': no revenue section found. Top-level groups: "
                            f"{[g.label for g in stmt.groups]}. Add the label to income_statement.sections.revenue.")

    lines: dict[str, list[Line]] = {}
    seen_rows: set[int] = set()
    for section, group in groups.items():
        if group is None:
            continue
        section_lines = []
        keys: set[str] = set()
        for leaf in group.leaves():
            if leaf.sheet_row in seen_rows:
                continue
            seen_rows.add(leaf.sheet_row)
            key = f"{section}:{slug(leaf.label)}"
            if key in keys:
                issues.append(Issue("duplicate_account", WARNING,
                                    f"'{leaf.label}' appears more than once in {section}; rows are kept separately",
                                    sheet=stmt.sheet, account=leaf.label))
                key = f"{key}-r{leaf.sheet_row}"
            keys.add(key)
            section_lines.append(Line(key, leaf.label, section, leaf.path_label, leaf.sheet_row, leaf.values))
        lines[section] = section_lines

    unmapped = [r for r in stmt.leaves() if r.sheet_row not in seen_rows]
    if unmapped:
        issues.append(Issue("unmapped_accounts", WARNING,
                            f"{len(unmapped)} accounts are outside every recognised section and are excluded from the metrics",
                            sheet=stmt.sheet, details={"accounts": [r.path_label for r in unmapped]}))
    for section in ("cogs", "opex"):
        if groups[section] is None:
            issues.append(Issue(f"{section}_section_missing", WARNING,
                                f"No {section} section found; metrics that depend on it are not calculated",
                                sheet=stmt.sheet))

    all_lines = [line for s in IS_SECTIONS for line in lines.get(s, [])]
    classification_configured = _classify(all_lines, config, issues, stmt.sheet)
    opex_lines = lines.get("opex", [])
    order, rollups_configured = _assign_categories(opex_lines, groups["opex"], config, issues, stmt.sheet)

    def section_sum(section: str) -> Series | None:
        if groups[section] is None:
            return None
        return sum_series(lines[section], periods)

    m: dict[str, Series] = {}
    m["revenue"] = section_sum("revenue")
    cogs = section_sum("cogs")
    opex = section_sum("opex")
    if cogs is not None:
        m["cogs"] = cogs
        m["gross_profit"] = combine(periods, [(1, m["revenue"]), (-1, cogs)])
    if opex is not None:
        m["opex_total"] = opex
        reclassified = [line for line in opex_lines if line.classification in ("non_operating", "income_tax")]
        m["non_operating_in_opex"] = sum_series(reclassified, periods)
        m["core_opex"] = combine(periods, [(1, opex), (-1, m["non_operating_in_opex"])])
    cost_terms = [(-1, s) for s in (cogs, opex) if s is not None]
    m["operating_income"] = combine(periods, [(1, m["revenue"]), *cost_terms])
    if opex is not None:
        m["core_operating_income"] = combine(periods, [(1, m["operating_income"]), (1, m["non_operating_in_opex"])])
    ni_terms = [(1, m["operating_income"])]
    for section, sign in (("other_income", 1), ("other_expense", -1)):
        s = section_sum(section)
        if s is not None:
            m[section] = s
            ni_terms.append((sign, s))
    m["net_income"] = combine(periods, ni_terms)
    non_cash = [line for line in all_lines
                if label_in(line.label, config.income_statement["account_classes"].get("non_cash", []))]
    if non_cash:
        m["non_cash_expenses"] = sum_series(non_cash, periods)

    reasons: dict[str, dict[Period, str]] = {}
    for ratio, (num, den) in RATIOS.items():
        if num not in m or den not in m:
            continue
        m[ratio], reasons[ratio] = {}, {}
        for p in periods:
            value, reason = safe_ratio(m[num][p], m[den][p])
            m[ratio][p] = value * 100 if value is not None else None
            if reason:
                reasons[ratio][p] = reason

    categories = {name: sum_series([l for l in opex_lines if l.category == name], periods) for name in order}
    members = {name: [l.label for l in opex_lines if l.category == name] for name in order}

    reported: dict[str, ReportedLine] = {}
    for section, group in groups.items():
        if group is not None and group.total is not None:
            reported[section] = ReportedLine(group.total.label, group.total.sheet_row, group.total.values)
    for metric, aliases in config.income_statement["derived_rows"].items():
        rows = stmt.find_rows(aliases, (DERIVED, SUBTOTAL))
        if rows:
            reported[metric] = ReportedLine(rows[0].label, rows[0].sheet_row, rows[0].values)

    return IncomeStatementModel(
        periods=periods,
        sections_present={s: (g.label if g else None) for s, g in groups.items()},
        lines=lines, metrics=m, ratio_reasons=reasons, reported=reported,
        categories=categories, category_members=members,
        classification_configured=classification_configured, rollups_configured=rollups_configured,
        unmapped=unmapped,
    )


def build_balance_sheet(stmt: Statement, config: Config, issues: list[Issue]) -> BalanceSheetModel:
    periods = stmt.periods
    aliases = config.balance_sheet["groups"]
    found: dict[str, Group | None] = {}
    for key, names in aliases.items():
        groups = stmt.find_groups(names)
        found[key] = groups[0] if groups else None

    m: dict[str, Series] = {}
    names = {"cash": "cash", "current_assets": "current_assets", "current_liabilities": "current_liabilities",
             "assets": "total_assets", "liabilities": "total_liabilities", "equity": "total_equity"}
    for key, metric in names.items():
        if found.get(key) is not None:
            m[metric] = sum_series(found[key].leaves(), periods)
        elif single := stmt.find_rows(aliases.get(key, []), (LEAF,)):  # e.g. cash held in one account, not a group
            m[metric] = sum_series(single[:1], periods)
        else:
            issues.append(Issue("bs_group_missing", INFO, f"Balance sheet group for {key} not found; {metric} not reported",
                                sheet=stmt.sheet))
    if "current_assets" in m and "current_liabilities" in m:
        m["working_capital"] = combine(periods, [(1, m["current_assets"]), (-1, m["current_liabilities"])])

    cye_rows = stmt.find_rows(config.balance_sheet["current_year_earnings_accounts"], (LEAF,))
    cye = ReportedLine(cye_rows[0].label, cye_rows[0].sheet_row, cye_rows[0].values) if cye_rows else None
    return BalanceSheetModel(periods, m, {k: (g.label if g else None) for k, g in found.items()}, cye)
