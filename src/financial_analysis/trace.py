"""Check a commentary against the saved analysis outputs: its provenance and its financial claims.

    python -m financial_analysis.trace <out folder>    (reads commentary.md, analysis.json, brief.md)

Content (claims.py has the details). Each sentence that names a metric, account, expense category or
balance-sheet figure has its numbers checked against that item's figures in analysis.json: the
right item, a month the sentence names or the analysis month, the written precision and unit, the
sign, the direction words and profit/loss wording. Numbers in forward-looking sentences, and causes
stated as fact without a figure from the analysis, are reported. A number in a sentence that names
nothing measurable must be printed in brief.md. Headings, years (e.g. 2026), ISO month keys
(2026-06), list numbering and, in sentences that name nothing, single digits are ignored.

The commentary must also end with the source line printed in brief.md:
    Source: analysis <YYYY-MM> · workbook sha256 <64 hex> · analysis_id <64 hex>
and every value in it must match the saved analysis.json (its period, its workbook hash and
the analysis_id computed from it). The digits in that line are not checked as figures.

The folder itself must be consistent: brief.md must print the same source line as
analysis.json (so the numbers are checked against one analysis), and when last_run.json
exists the latest run must not have failed and must describe that same analysis (period,
workbook sha256, analysis_id). last_run.json can only reject: the commentary is accepted on
the strength of analysis.json, from which the analysis_id is recomputed.

Exit codes: 0 = source line matches and no finding, 4 = source line missing or not matching
the saved analysis, or the folder's outputs are stale or inconsistent (listed), 3 = content
findings (listed: UNTRACED, MISATTRIBUTED, CONTRADICTS, FORECAST, UNSUPPORTED_CAUSE),
1 = an input file is missing or unreadable.

This is a guard, not a proof: it reads a fixed vocabulary, so a wrong claim without a number, or
with no recognisable name in its sentence, can still pass. The reviewer remains the final check.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .claims import Finding, check_claims
from .output import analysis_id, source_line

SOURCE = re.compile(r"^Source: analysis (\d{4}-\d{2}) · workbook sha256 ([0-9a-f]{64}) · analysis_id ([0-9a-f]{64})[ \t]*$",
                    re.MULTILINE)


def content_findings(commentary: str, analysis: Any, brief: str) -> list[Finding]:
    """Every content problem in the commentary (see claims.py)."""
    return check_claims(commentary, analysis, brief)


def untraced_numbers(commentary: str, analysis: Any, brief: str) -> list[tuple[str, str]]:
    """(number as written, its sentence) for every number in a sentence that names nothing measurable and
    is not printed in brief.md. The other findings come from content_findings."""
    return [(f.token, f.context) for f in check_claims(commentary, analysis, brief) if f.kind == "untraced"]


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
    findings = content_findings(commentary, analysis, brief)
    for f in findings:
        print(f"{f.kind.upper()} {f.token}: {f.reason}. In: \"{f.context}\"")
    untraced = sum(f.kind == "untraced" for f in findings)
    print(f"{commentary_path.name}: source line {'does not match' if problems else 'matches'} the current saved analysis; "
          f"{untraced} untraced number(s), {len(findings) - untraced} claim finding(s) against analysis.json and "
          f"brief.md in {args.folder}")
    return 4 if problems else 3 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
