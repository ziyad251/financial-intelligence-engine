"""Command line entry point.

    python -m financial_analysis <workbook.xlsx> [--config company.json] [--period YYYY-MM] [--out-dir DIR]

Writes DIR/analysis.json (full structured result) and DIR/brief.md (reviewer brief),
then prints the brief. Exit codes: 0 = pass or pass_with_warnings, 2 = validation
failed (outputs still written, commentary must not be produced), 1 = the workbook
could not be analysed at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .issues import AnalysisError
from .output import render_brief, write_json
from .pipeline import analyze


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
    brief = render_brief(result)
    write_json(result, out_dir / "analysis.json")
    (out_dir / "brief.md").write_text(brief, encoding="utf-8")

    if not args.quiet:
        print(brief)
    print(f"status={result['status']} analysis_json={out_dir / 'analysis.json'} brief={out_dir / 'brief.md'}",
          file=sys.stderr)
    return 2 if result["status"] == "fail" else 0
