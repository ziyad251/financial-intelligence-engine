"""Company configuration: label aliases, account classifications and thresholds.

The defaults work for a QuickBooks-style monthly export with no configuration at all.
A per-company JSON file only needs the keys it overrides; it is deep-merged onto
DEFAULTS. Accounting judgment (which accounts are non-operating, how opex rolls up
into owner-facing categories) belongs in that file, never in code.
"""

from __future__ import annotations

import copy
import difflib
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


class ConfigError(ValueError):
    """A company config that cannot be used as written (unknown key, wrong type, bad regex)."""


INTEGER_KEYS = {"fiscal_year_start_month": (1, 12), "thresholds.max_highlights": (1, None)}


def _validate(override: dict[str, Any], defaults: dict[str, Any], where: str = "") -> None:
    """Check an override against the shape of DEFAULTS, so a typo fails loudly instead of being ignored."""
    for key, value in override.items():
        path = f"{where}.{key}" if where else key
        if key not in defaults:
            close = difflib.get_close_matches(key, list(defaults), n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise ConfigError(f"unknown key '{path}'.{hint} Allowed here: {', '.join(sorted(defaults))}")
        default = defaults[key]
        if path == "income_statement.opex_rollups":
            _validate_rollups(value, path)
        elif isinstance(default, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"'{path}' must be an object, got {value!r}")
            _validate(value, default, path)
        elif isinstance(default, list):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"'{path}' must be a list of strings, got {value!r}")
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"'{path}' must be true or false, got {value!r}")
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ConfigError(f"'{path}' must be a non-negative number, got {value!r}")
            if path in INTEGER_KEYS:
                low, high = INTEGER_KEYS[path]
                if not isinstance(value, int) or value < low or (high is not None and value > high):
                    raise ConfigError(f"'{path}' must be a whole number from {low}"
                                      f"{f' to {high}' if high else ' upwards'}, got {value!r}")
        elif value is not None and not isinstance(value, str):
            raise ConfigError(f"'{path}' must be a string, got {value!r}")


def _validate_rollups(value: Any, path: str) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"'{path}' must be a list of {{\"name\", \"match\"}} objects, got {value!r}")
    for i, rollup in enumerate(value):
        where = f"{path}[{i}]"
        if not isinstance(rollup, dict):
            raise ConfigError(f"'{where}' must be an object with 'name' and 'match', got {rollup!r}")
        extra = set(rollup) - {"name", "match"}
        if extra:
            raise ConfigError(f"'{where}' has unknown key(s) {sorted(extra)}; only 'name' and 'match' are allowed")
        name, patterns = rollup.get("name"), rollup.get("match")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"'{where}.name' must be a non-empty string, got {name!r}")
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) for p in patterns):
            raise ConfigError(f"'{where}.match' ('{name}') must be a non-empty list of regex strings, got {patterns!r}")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"'{where}.match' ('{name}'): invalid regular expression {pattern!r}: {exc}") from exc


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
            try:
                override = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"Invalid company config {path}: not valid JSON ({exc})") from exc
        if not isinstance(override, dict):
            raise ConfigError(f"Invalid company config {path}: the top level must be a JSON object")
        override = {k: v for k, v in override.items() if not k.startswith("_")}  # allow "_comment" keys
        try:
            _validate(override, DEFAULTS)
        except ConfigError as exc:
            raise ConfigError(f"Invalid company config {path}: {exc}") from exc
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
