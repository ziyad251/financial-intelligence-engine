"""Company configuration: label aliases, account classifications and thresholds.

The defaults work for a QuickBooks-style monthly export with no configuration at all.
A per-company JSON file only needs the keys it overrides; it is deep-merged onto
DEFAULTS. Accounting judgment (which accounts are non-operating, how opex rolls up
into owner-facing categories) belongs in that file, never in code.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "company_name": None,
    "currency": "USD",
    "fiscal_year_start_month": 1,
    # Blank cells in a line item are missing data unless explicitly declared to mean zero.
    "blank_cells_as_zero": False,
    "reconciliation_tolerance": 0.01,
    "income_statement": {
        "sheet_names": ["Income Statement", "Profit and Loss", "Profit & Loss", "P&L", "PnL"],
        "sections": {
            "revenue": ["Income", "Revenue", "Revenues", "Sales", "Total Revenue"],
            "cogs": ["Cost of Goods Sold", "Cost of Sales", "Cost of Revenue", "COGS"],
            "opex": ["Operating Expenses", "Expenses", "Expense"],
            "other_income": ["Other Income"],
            "other_expense": ["Other Expenses", "Other Expense"],
        },
        "derived_rows": {
            "gross_profit": ["Gross Profit"],
            "operating_income": ["Operating Income", "Net Operating Income", "Operating Profit", "Operating Loss"],
            "net_income": ["Net Income", "Net Profit", "Net Loss", "Net Earnings"],
        },
        # Accounts presented inside operating expenses that are not operating in nature.
        "account_classes": {"non_operating": [], "income_tax": [], "non_cash": []},
        # Accounts whose normal balance is negative (e.g. interest income booked as a contra-expense).
        "expected_negative_accounts": [],
        # [{"name": ..., "match": [regex, ...]}] matched against "Section > Group > Account".
        # Empty list = one category per direct child of the operating-expenses section.
        "opex_rollups": [],
    },
    "balance_sheet": {
        "sheet_names": ["Balance Sheet", "Statement of Financial Position"],
        "groups": {
            "assets": ["Assets", "Total Assets"],
            "liabilities": ["Liabilities"],
            "equity": ["Equity", "Shareholders' Equity", "Stockholders' Equity", "Owner's Equity"],
            "current_assets": ["Current Assets"],
            "current_liabilities": ["Current Liabilities"],
            "cash": ["Cash and Cash Equivalents", "Bank Accounts", "Cash"],
        },
        "current_year_earnings_accounts": ["Current Year Earnings", "Net Income"],
    },
    "thresholds": {
        "materiality_abs": 5000,
        "materiality_pct_of_revenue": 0.02,
        "min_mom_pct": 0.20,
        "margin_move_pp": 2.0,
        "one_off_min_ratio_to_median": 1.0,
        "bs_materiality_abs": 25000,
        "max_highlights": 20,
    },
}


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("�", "-")).strip().casefold()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict[str, Any], source: str | None = None):
        self.data = data
        self.source = source
        self.opex_rollups: list[tuple[str, list[re.Pattern[str]]]] = []
        for rollup in data["income_statement"]["opex_rollups"]:
            if not rollup.get("name") or not rollup.get("match"):
                raise ValueError(f"opex_rollups entries need 'name' and 'match': {rollup!r}")
            self.opex_rollups.append((rollup["name"], [re.compile(p, re.IGNORECASE) for p in rollup["match"]]))

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            return cls(copy.deepcopy(DEFAULTS))
        with open(path, encoding="utf-8") as fh:
            override = json.load(fh)
        override = {k: v for k, v in override.items() if not k.startswith("_")}  # allow "_comment" keys
        return cls(_deep_merge(DEFAULTS, override), source=str(path))

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def public_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)


def label_in(label_or_key: str, names: list[str]) -> bool:
    wanted = {normalize_label(n) for n in names}
    return normalize_label(label_or_key) in wanted
