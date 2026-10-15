"""The monthly-trend-analysis skill's instructions must work for any company without being rewritten.

Company-specific knowledge belongs in config/companies/*.json and reaches Claude through the brief. These
checks fail when the reusable instructions (SKILL.md and its references) name a configured company, one of
its accounts or categories, a specific month or a specific amount, and when they cite a brief section, an
`n/a` reason or an issue code that the pipeline does not produce.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from financial_analysis.config import Config
from financial_analysis.output import NOTE_TEXT
from financial_analysis.pipeline import analyze

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".claude" / "skills" / "monthly-trend-analysis"
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = sorted((SKILL_DIR / "references").glob("*.md"))
CONFIGS = sorted((ROOT / "config" / "companies").glob("*.json"))
DATA = ROOT / "data"

# Generic accounting terms the references may use as examples even though a company has an account so named.
# SKILL.md itself names no account at all.
GENERIC_EXAMPLES = {"interest income", "interest expense"}
LEGAL_SUFFIX = re.compile(r"[\s,.]+(inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|gmbh|pty)\.?$", re.I)
MONTH_YEAR = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(19|20)\d\d\b")
ISO_MONTH = re.compile(r"\b(19|20)\d\d-(0[1-9]|1[0-2])\b")
AMOUNT = re.compile(r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|(?<![\w.])[-+]?\d+\.\d+\s?(?:%|pp\b)")


def _phrase(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)")


def company_terms() -> set[str]:
    """Each configured company's name (with and without its legal suffix) and its config file stem."""
    terms = set()
    for path in CONFIGS:
        terms.add(path.stem.lower())
        name = json.loads(path.read_text(encoding="utf-8")).get("company_name")
        if name:
            terms |= {name.lower(), LEGAL_SUFFIX.sub("", name).lower()}
    return {t for t in terms if len(t) >= 3}


def config_labels() -> set[str]:
    """Account and category labels written into the company configs."""
    labels = set()
    for path in CONFIGS:
        inc = json.loads(path.read_text(encoding="utf-8")).get("income_statement", {})
        for names in inc.get("account_classes", {}).values():
            labels |= set(names)
        labels |= set(inc.get("expected_negative_accounts", []))
        labels |= {rule["name"] for rule in inc.get("opex_rollups", [])}
    return {label.lower() for label in labels}


def workbook_labels() -> set[str]:
    """Account and category labels of each workbook in data/ analysed with its company's config."""
    labels = set()
    workbooks = sorted([*DATA.glob("*.xlsx"), *DATA.glob("*.xlsm")]) if DATA.exists() else []
    for config_path in CONFIGS:
        company = json.loads(config_path.read_text(encoding="utf-8")).get("company_name") or ""
        for workbook in workbooks:
            if LEGAL_SUFFIX.sub("", company).lower() not in workbook.name.lower():
                continue
            inc = analyze(workbook, Config.load(config_path))["income_statement"]
            labels |= {a["label"] for a in inc["accounts"]} | {c["name"] for c in inc["opex_categories"]}
    return {label.lower() for label in labels}


def company_specific_findings(text: str, labels: set[str], allowed: set[str] = frozenset()) -> list[str]:
    """Every company name, account or category label, specific month or specific amount in `text`."""
    low = text.lower()
    found = [f"company: {t}" for t in sorted(company_terms()) if _phrase(t).search(low)]
    found += [f"account or category: {t}" for t in sorted(labels - allowed) if _phrase(t).search(low)]
    found += [f"month: {m.group()}" for m in (*MONTH_YEAR.finditer(text), *ISO_MONTH.finditer(text))]
    found += [f"amount: {m.group()}" for m in AMOUNT.finditer(text)]
    return found


def test_there_is_a_company_config_to_check_against():
    assert CONFIGS and company_terms() and config_labels()


def test_skill_names_no_company_account_month_or_amount():
    labels = config_labels() | workbook_labels()
    assert company_specific_findings(SKILL.read_text(encoding="utf-8"), labels) == []


@pytest.mark.parametrize("path", REFERENCES, ids=lambda p: p.name)
def test_references_name_no_company_or_its_accounts(path):
    labels = config_labels() | workbook_labels()
    text = path.read_text(encoding="utf-8")
    low = text.lower()
    found = [t for t in sorted(company_terms()) if _phrase(t).search(low)]
    found += [t for t in sorted(labels - GENERIC_EXAMPLES) if _phrase(t).search(low)]
    assert found == []


def test_skill_scripts_name_no_company():
    for path in (SKILL_DIR / "scripts").glob("*.py"):
        low = path.read_text(encoding="utf-8").lower()
        assert [t for t in company_terms() if _phrase(t).search(low)] == [], path.name


def test_detector_catches_company_specific_wording():
    """Guards the guard: sentences of the kind this skill once carried are reported."""
    labels = config_labels()
    name = json.loads(CONFIGS[0].read_text(encoding="utf-8"))["company_name"]
    some_label = sorted(labels)[0]
    for sentence in (f"For example, Dec 2025 at {name} has a large benefit.",
                     f"the brief's note (for {name}: {some_label})",
                     "TTM gross profit peaked in 2026-05 at 1,234,567.",
                     "the margin moved 3.1 pp"):
        assert company_specific_findings(sentence, labels), sentence
    assert company_specific_findings("TTM <metric> rose from <first month>, peaking in <month>.", labels) == []


SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src" / "financial_analysis").glob("*.py"))


def test_cited_brief_sections_exist():
    """Every brief section or note the skill quotes by name is one the pipeline writes."""
    text = SKILL.read_text(encoding="utf-8")
    cited = set(re.findall(r'"([A-Z][^"]+)" (?:section|note)\b', text)) | set(re.findall(r'\bunder "([^"]+)"', text))
    assert {"TTM trend", "Monthly trend", "Unusual months across the full history"} <= cited
    cited |= {"No movements met the materiality thresholds", "extremes incomplete"}
    output = (ROOT / "src" / "financial_analysis" / "output.py").read_text(encoding="utf-8")
    assert {c for c in cited if c not in output} == set()


def test_cited_na_reasons_exist():
    reasons = set(re.findall(r"n/a \(([^)]+)\)", SKILL.read_text(encoding="utf-8")))
    assert reasons and reasons <= set(NOTE_TEXT.values()) | {"reason"}


def test_cited_issue_codes_exist():
    codes = {c for c in re.findall(r"`([a-z]+(?:_[a-z]+)+)`", SKILL.read_text(encoding="utf-8"))}
    assert {c for c in codes if f'"{c}"' not in SOURCE and f"'{c}'" not in SOURCE} == set()
