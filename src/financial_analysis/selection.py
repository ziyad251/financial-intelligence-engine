"""Which workbook and which company: deterministic selection and identity checks.

Workbook selection never guesses. An explicit path must exist, be an Excel workbook
(.xlsx / .xlsm) and, when a permitted input folder is set, sit inside it. Without a
path, a workbook is chosen only when the input folder holds exactly one eligible
file; several candidates stop the run with their names listed, so the caller must
pick one. Directory listing order is never used to choose.

Company identity: a config that names a company (`company_name`) is only applied to a
workbook whose title names the same company. When the workbook names a different
company, or names none that can be read, the run stops unless the user has confirmed
the company explicitly (`--confirm-company "<company_name>"`). Nothing is inferred
from the config's file name.
"""

from __future__ import annotations

import re
from pathlib import Path

from .issues import AnalysisError

WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


def is_eligible(path: Path) -> bool:
    """An Excel workbook file that is not an Office lock file (~$...) or a hidden file."""
    return (path.is_file() and path.suffix.lower() in WORKBOOK_SUFFIXES
            and not path.name.startswith(("~$", ".")))


def eligible_workbooks(input_dir: Path) -> list[Path]:
    """Eligible workbooks directly inside `input_dir`, sorted by name (for listing, never for choosing)."""
    if not input_dir.is_dir():
        raise AnalysisError(f"Input folder not found: {input_dir}")
    return sorted((p for p in input_dir.iterdir() if is_eligible(p)), key=lambda p: p.name.casefold())


def select_workbook(workbook: Path | None, input_dir: Path | None) -> Path:
    """The workbook to analyse, or AnalysisError saying why none can be chosen safely."""
    if workbook is None:
        if input_dir is None:
            raise AnalysisError("No workbook given. Pass the workbook path explicitly.")
        candidates = eligible_workbooks(input_dir)
        if not candidates:
            raise AnalysisError(f"No Excel workbook (.xlsx/.xlsm) found in {input_dir}. Pass the workbook path explicitly.")
        if len(candidates) > 1:
            names = "\n".join(f"  - {p.name}" for p in candidates)
            raise AnalysisError(f"{len(candidates)} workbooks found in {input_dir}; none was selected. "
                                f"Choose one and pass its path explicitly:\n{names}")
        workbook = candidates[0]

    if not workbook.exists():
        raise AnalysisError(f"Workbook not found: {workbook}")
    if not workbook.is_file():
        raise AnalysisError(f"Workbook path is not a file: {workbook}")
    if workbook.suffix.lower() not in WORKBOOK_SUFFIXES:
        raise AnalysisError(f"Not an Excel workbook: {workbook.name} (expected {' or '.join(WORKBOOK_SUFFIXES)}). "
                            "Re-export it from Excel or the accounting system.")
    if input_dir is not None:
        try:
            workbook.resolve().relative_to(input_dir.resolve())
        except ValueError:
            raise AnalysisError(f"Workbook {workbook} is outside the permitted input folder {input_dir}. "
                                "Copy it into that folder and pass the new path.") from None
    return workbook


def _company_key(name: str) -> str:
    """Comparable form of a company name: case, spacing and punctuation ignored ('ABC, Inc.' == 'abc inc')."""
    return re.sub(r"[^0-9a-z]+", "", name.casefold())


def workbook_company(title_lines: list[str]) -> str | None:
    """The company named in the statement title, by the pipeline's existing convention: the second of at
    least three title lines (statement name, company, period). None when the title cannot say."""
    return title_lines[1] if len(title_lines) > 2 else None


def check_company(config_company: str | None, title_lines: list[str], confirmed: str | None) -> dict[str, str | None]:
    """Check that the config's company is the workbook's company. Returns a record for last_run.json, or
    raises AnalysisError when the run must stop. `confirmed` is the company the user confirmed, if any."""
    in_workbook = workbook_company(title_lines)
    record = {"config_company": config_company, "workbook_company": in_workbook, "confirmed_company": confirmed}
    if confirmed is not None and (config_company is None or _company_key(confirmed) != _company_key(config_company)):
        raise AnalysisError(f"--confirm-company {confirmed!r} does not match the config's company_name "
                            f"{config_company!r}. Confirm the company the config was written for, or choose another config.")
    if config_company is None:
        return {**record, "result": "not_checked_no_company_in_config"}
    if in_workbook is not None and _company_key(in_workbook) == _company_key(config_company):
        return {**record, "result": "matched_workbook_title"}
    if confirmed is not None:
        return {**record, "result": "confirmed_by_user"}
    if in_workbook is None:
        raise AnalysisError(f"The workbook's title does not name a company, so it cannot be confirmed that the config "
                            f"for {config_company!r} applies. Ask the user; if they confirm, re-run with "
                            f"--confirm-company \"{config_company}\".")
    raise AnalysisError(f"Company mismatch: the config is for {config_company!r} but the workbook's title names "
                        f"{in_workbook!r}. Use that company's config. Only if the user confirms the workbook really is "
                        f"{config_company!r}, re-run with --confirm-company \"{config_company}\".")
