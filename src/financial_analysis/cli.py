"""Command line entry point.

    python -m financial_analysis <workbook.xlsx> [--config company.json] [--period YYYY-MM] [--out-dir DIR]

Writes DIR/analysis.json (full structured result) and DIR/brief.md (reviewer brief),
then prints the brief. Exit codes: 0 = pass or pass_with_warnings, 2 = validation
failed, 1 = the workbook or config could not be used at all (nothing is written).

A failed run never overwrites, renames or deletes the outputs of an earlier
successful run: it writes analysis.failed.json and brief.failed.md instead, so a
failed result cannot be mistaken for a valid one. A successful run whose analysis
differs from the one already in DIR renames an existing commentary.md to
commentary.stale.md, because that commentary describes the earlier figures.
DIR/last_run.json always records the outcome of the latest run, and lists any
earlier outputs that are now stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config
from .issues import AnalysisError
from .output import render_brief, write_json
from .pipeline import analyze

VALID_FILES = ("analysis.json", "brief.md")
FAILED_FILES = ("analysis.failed.json", "brief.failed.md")
EARLIER_OUTPUTS = (*VALID_FILES, "commentary.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="financial_analysis", description="Monthly financial trend analysis")
    parser.add_argument("workbook", type=Path, help="Excel workbook with monthly income statement (and balance sheet)")
    parser.add_argument("--config", type=Path, help="company config JSON (overrides defaults)")
    parser.add_argument("--period", help="analysis month as YYYY-MM (default: latest month in the workbook)")
    parser.add_argument("--out-dir", type=Path, help="output directory (default: out/<workbook name>/<period>)")
    parser.add_argument("--quiet", action="store_true", help="do not print the brief")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        config = Config.load(args.config)
        result = analyze(args.workbook, config, args.period)
    except (AnalysisError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    period = result["periods"]["analysis_period"]
    out_dir = args.out_dir or Path("out") / args.workbook.stem / period
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = result["status"] == "fail"
    json_name, brief_name = FAILED_FILES if failed else VALID_FILES
    stale = [name for name in EARLIER_OUTPUTS if failed and (out_dir / name).exists()]

    commentary_set_aside = None
    if not failed and (out_dir / "commentary.md").exists() and not _same_analysis(out_dir / "analysis.json", result):
        # The commentary was written for a different analysis; keep it, but not under the current name.
        commentary_set_aside = _set_aside(out_dir / "commentary.md")

    brief = render_brief(result)
    write_json(result, out_dir / json_name)
    (out_dir / brief_name).write_text(brief, encoding="utf-8")
    if not failed:
        for name in FAILED_FILES:  # an earlier failure no longer describes this folder
            (out_dir / name).unlink(missing_ok=True)
    last_run = {"status": result["status"], "exit_code": 2 if failed else 0, "analysis_period": period,
                "workbook": result["source"]["workbook"], "sha256": result["source"]["sha256"],
                "analysis_json": json_name, "brief": brief_name, "stale_earlier_outputs": stale,
                "commentary_set_aside": commentary_set_aside}
    (out_dir / "last_run.json").write_text(json.dumps(last_run, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print(brief)
    print(f"status={result['status']} analysis_json={out_dir / json_name} brief={out_dir / brief_name}",
          file=sys.stderr)
    if failed:
        print("VALIDATION FAILED: no commentary may be written from this run."
              + (f" Earlier outputs in {out_dir} ({', '.join(stale)}) were left untouched but do NOT reflect this "
                 "run; treat them as stale." if stale else ""), file=sys.stderr)
    if commentary_set_aside:
        print(f"NOTE: the analysis changed since commentary.md was written, so it was renamed to "
              f"{commentary_set_aside}. Write new commentary from this run's brief.", file=sys.stderr)
    return 2 if failed else 0


def _same_analysis(previous: Path, result: dict) -> bool:
    """True if `previous` holds exactly this result, so commentary written from it still applies."""
    try:
        return json.loads(previous.read_text(encoding="utf-8")) == json.loads(json.dumps(result))
    except (OSError, ValueError):
        return False


def _set_aside(commentary: Path) -> str:
    """Rename commentary.md to commentary.stale.md (or .stale-2.md, ...) without overwriting anything."""
    n = 1
    while (target := commentary.with_name("commentary.stale.md" if n == 1 else f"commentary.stale-{n}.md")).exists():
        n += 1
    commentary.rename(target)
    return target.name
