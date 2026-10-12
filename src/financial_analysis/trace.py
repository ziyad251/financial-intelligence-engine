"""Check that every number in a commentary appears in the saved analysis outputs.

    python -m financial_analysis.trace <out folder>    (reads commentary.md, analysis.json, brief.md)

A number is traced when it matches, ignoring its sign and thousands separators, a value
in analysis.json at presentation rounding (whole units, 1 dp or 2 dp) or a number printed
in brief.md. Headings, years (e.g. 2026), ISO month keys (2026-06) and single digits (list
numbering) are ignored.

The commentary must also end with the source line printed in brief.md:
    Source: analysis <YYYY-MM> · workbook sha256 <64 hex> · analysis_id <64 hex>
and every value in it must match the saved analysis.json (its period, its workbook hash and
the analysis_id computed from it). The digits in that line are not checked as figures.

The folder itself must be consistent: brief.md must print the same source line as
analysis.json (so the numbers are traced against one analysis), and when last_run.json
exists the latest run must not have failed and must describe that same analysis (period,
workbook sha256, analysis_id). last_run.json can only reject: the commentary is accepted on
the strength of analysis.json, from which the analysis_id is recomputed.

Exit codes: 0 = source line matches and every number traced, 4 = source line missing or
not matching the saved analysis, or the folder's outputs are stale or inconsistent (listed),
3 = untraced numbers found (listed), 1 = an input file is missing or unreadable.

This proves a figure came from the outputs, not that it is used correctly: a number
quoted with the wrong sign, month or label still traces.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .output import analysis_id, source_line

NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")
ISO_MONTH = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
IGNORED = re.compile(r"(19|20)\d\d|\d")  # years and single digits
HASH = re.compile(r"\b[0-9a-f]{64}\b")  # workbook sha256 and analysis_id: identifiers, not figures
SOURCE = re.compile(r"^Source: analysis (\d{4}-\d{2}) · workbook sha256 ([0-9a-f]{64}) · analysis_id ([0-9a-f]{64})[ \t]*$",
                    re.MULTILINE)


def _canonical(token: str) -> str:
    return token.lstrip("+-").replace(",", "")


def known_numbers(analysis: Any, brief: str) -> set[str]:
    known = {_canonical(t) for t in NUMBER.findall(ISO_MONTH.sub(" ", HASH.sub(" ", brief)))}

    def walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            for fmt in ("{:.0f}", "{:.1f}", "{:.2f}"):
                known.add(fmt.format(abs(value)))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(analysis)
    return known


def untraced_numbers(commentary: str, analysis: Any, brief: str) -> list[tuple[str, str]]:
    """(number as written, surrounding text) for every number not found in the outputs."""
    body = ISO_MONTH.sub(" ", HASH.sub(" ", re.sub(r"^#.*$", "", commentary, flags=re.MULTILINE)))
    known = known_numbers(analysis, brief)
    found = []
    for m in NUMBER.finditer(body):
        token = _canonical(m.group())
        if token in known or IGNORED.fullmatch(token):
            continue
        found.append((m.group(), body[max(0, m.start() - 50): m.end() + 20].replace("\n", " ").strip()))
    return found


def commentary_source(commentary: str) -> dict[str, str] | None:
    """The commentary's source line as {analysis_period, workbook_sha256, analysis_id}, or None if it has
    none (or more than one, which cannot say which analysis it describes)."""
    found = SOURCE.findall(commentary)
    if len(found) != 1:
        return None
    period, sha, aid = found[0]
    return {"analysis_period": period, "workbook_sha256": sha, "analysis_id": aid}


def source_problems(commentary: str, analysis: Any) -> list[str]:
    """Why the commentary's source line does not identify this saved analysis (empty if it does)."""
    if len(SOURCE.findall(commentary)) > 1:
        return ["more than one source line"]
    src = commentary_source(commentary)
    if src is None:
        return ["no source line (expected 'Source: analysis <YYYY-MM> · workbook sha256 <hex> · analysis_id <hex>')"]
    expected = {"analysis_period": analysis["periods"]["analysis_period"],
                "workbook_sha256": analysis["source"]["sha256"], "analysis_id": analysis_id(analysis)}
    return [f"{key} is {src[key]}, but the saved analysis has {value}"
            for key, value in expected.items() if src[key] != value]


def folder_problems(analysis: Any, brief: str, last_run: Any) -> list[str]:
    """Why the saved outputs in a folder cannot be treated as one current analysis (empty if they can).
    `last_run` is the parsed last_run.json, or None when the folder has none."""
    problems = []
    if analysis.get("status") == "fail":
        problems.append("analysis.json records a failed validation")
    if source_line(analysis) not in brief:
        problems.append("brief.md does not print the source line of analysis.json: they are from different runs")
    if last_run is None:
        return problems
    if last_run.get("status") == "fail" or last_run.get("exit_code") not in (0, None):
        problems.append(f"the latest run failed ({last_run.get('analysis_json')}); analysis.json is from an earlier "
                        "run and is stale")
    expected = {"analysis_period": analysis["periods"]["analysis_period"], "sha256": analysis["source"]["sha256"],
                "analysis_id": analysis_id(analysis)}
    problems += [f"last_run.json {key} is {last_run.get(key)}, but analysis.json has {value}: the latest run "
                 "produced a different analysis" for key, value in expected.items() if last_run.get(key) != value]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="financial_analysis.trace", description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="out/<workbook>/<YYYY-MM> folder holding the saved outputs")
    parser.add_argument("--commentary", type=Path, help="commentary file (default: <folder>/commentary.md)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    commentary_path = args.commentary or args.folder / "commentary.md"
    try:
        commentary = commentary_path.read_text(encoding="utf-8")
        analysis = json.loads((args.folder / "analysis.json").read_text(encoding="utf-8"))
        brief = (args.folder / "brief.md").read_text(encoding="utf-8")
        last_run_path = args.folder / "last_run.json"
        last_run = json.loads(last_run_path.read_text(encoding="utf-8")) if last_run_path.exists() else None
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    problems = source_problems(commentary, analysis) + folder_problems(analysis, brief, last_run)
    for problem in problems:
        print(f"SOURCE {problem}")
    found = untraced_numbers(commentary, analysis, brief)
    for token, context in found:
        print(f"UNTRACED {token}: ...{context}...")
    print(f"{commentary_path.name}: source line {'does not match' if problems else 'matches'} the current saved analysis; "
          f"{len(found)} untraced number(s) against analysis.json and brief.md in {args.folder}")
    return 4 if problems else 3 if found else 0


if __name__ == "__main__":
    sys.exit(main())
