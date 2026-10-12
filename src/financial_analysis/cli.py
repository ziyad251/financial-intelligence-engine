"""Command line entry point.

    python -m financial_analysis <workbook.xlsx> [--config company.json | --no-config] [--period YYYY-MM]
                                 [--out-dir DIR] [--input-dir DIR] [--require-config] [--confirm-company NAME]

Writes DIR/analysis.json (full structured result) and DIR/brief.md (reviewer brief),
then prints the brief. Exit codes: 0 = pass or pass_with_warnings, 2 = validation
failed, 1 = the workbook or config could not be used at all (nothing is written).

The workbook is chosen deterministically (see selection.py): an explicit path must be an
existing .xlsx/.xlsm and, with --input-dir, lie inside that folder. Without a path, the
only eligible workbook in --input-dir is used; several stop the run with their names
listed. A config naming a company is applied only to a workbook whose title names the same
company, unless the user confirmed it with --confirm-company. --require-config (used by the
skill) refuses to run without --config or an explicit --no-config.

Each output folder holds the analysis of one workbook content (sha256). If it already holds
outputs of a different workbook content, for example a different file saved under the same
name, they are first moved, unchanged, into DIR/superseded-<their sha256[:12]>-<UTC time>/.

A failed run never overwrites, renames or deletes the outputs of an earlier
successful run: it writes analysis.failed.json and brief.failed.md instead, so a
failed result cannot be mistaken for a valid one. A successful run whose analysis
differs from the one already in DIR renames an existing commentary.md to
commentary.stale.md, because that commentary describes the earlier figures.
DIR/last_run.json always records the outcome of the latest run, and lists any
earlier outputs that are now stale. It also records the run's analysis_id (a hash of the
analysis content, so it is the same for an unchanged analysis) and when the run happened;
analysis.json itself stays free of timestamps. A set-aside commentary's own source line is
copied into last_run.json, or null when it has none: its origin is then unknown.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .issues import AnalysisError
from .output import analysis_id, render_brief, write_json
from .pipeline import analyze
from .selection import check_company, select_workbook
from .trace import commentary_source

VALID_FILES = ("analysis.json", "brief.md")
FAILED_FILES = ("analysis.failed.json", "brief.failed.md")
EARLIER_OUTPUTS = (*VALID_FILES, "commentary.md")
RUN_FILES = (*VALID_FILES, *FAILED_FILES, "last_run.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="financial_analysis", description="Monthly financial trend analysis")
    parser.add_argument("workbook", type=Path, nargs="?",
                        help="Excel workbook with monthly income statement (and balance sheet); may be omitted only "
                             "with --input-dir holding exactly one workbook")
    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument("--config", type=Path, help="company config JSON (overrides defaults)")
    config_group.add_argument("--no-config", action="store_true", help="explicitly analyse with the default config")
    parser.add_argument("--period", help="analysis month as YYYY-MM (default: latest month in the workbook)")
    parser.add_argument("--out-dir", type=Path, help="output directory (default: out/<workbook name>/<period>)")
    parser.add_argument("--input-dir", type=Path, help="permitted input folder: the workbook must be inside it")
    parser.add_argument("--require-config", action="store_true",
                        help="refuse to run unless --config (naming a company_name) or --no-config is given")
    parser.add_argument("--confirm-company", metavar="NAME",
                        help="the user has confirmed the workbook belongs to this company (the config's company_name)")
    parser.add_argument("--quiet", action="store_true", help="do not print the brief")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.require_config and args.config is None and not args.no_config:
            raise AnalysisError("No company config given. Pass --config config/companies/<company>.json for the "
                                "workbook's company, or --no-config to analyse with the defaults (onboarding only).")
        workbook = select_workbook(args.workbook, args.input_dir)
        config = Config.load(args.config)
        if args.require_config and args.config is not None and not config.company_name:
            raise AnalysisError(f"Company config {args.config} has no company_name, so it cannot be checked against "
                                "the workbook. Add the company's name to the config.")
        result = analyze(workbook, config, args.period)
        company_check = check_company(config.company_name, result["source"]["title_lines"], args.confirm_company)
    except (AnalysisError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.workbook is None:
        print(f"NOTE: no workbook given; using the only workbook in {args.input_dir}: {workbook.name}", file=sys.stderr)

    period = result["periods"]["analysis_period"]
    out_dir = args.out_dir or Path("out") / workbook.stem / period
    out_dir.mkdir(parents=True, exist_ok=True)
    superseded = _supersede_other_workbook(out_dir, result["source"]["sha256"])
    failed = result["status"] == "fail"
    json_name, brief_name = FAILED_FILES if failed else VALID_FILES
    stale = [name for name in EARLIER_OUTPUTS if failed and (out_dir / name).exists()]

    commentary_set_aside = set_aside_source = None
    if not failed and (out_dir / "commentary.md").exists() and not _same_analysis(out_dir / "analysis.json", result):
        # The commentary was written for a different analysis; keep it, but not under the current name.
        set_aside_source = commentary_source((out_dir / "commentary.md").read_text(encoding="utf-8", errors="replace"))
        commentary_set_aside = _set_aside(out_dir / "commentary.md")

    brief = render_brief(result)
    if stale:  # a failed run: say in its own brief that the valid-looking files beside it are from an earlier run
        earlier = _read_json(out_dir / "last_run.json") or {}
        brief += (f"\n> **Earlier outputs in this folder are not from this run:** {', '.join(stale)} describe an earlier "
                  f"run (analysis_id {earlier.get('analysis_id', 'unknown')}, run at {earlier.get('run_at', 'unknown')}). "
                  "Do not use or quote them as current; `last_run.json` lists them under `stale_earlier_outputs`.\n")
    write_json(result, out_dir / json_name)
    (out_dir / brief_name).write_text(brief, encoding="utf-8")
    if not failed:
        for name in FAILED_FILES:  # an earlier failure no longer describes this folder
            (out_dir / name).unlink(missing_ok=True)
    last_run = {"status": result["status"], "exit_code": 2 if failed else 0, "analysis_period": period,
                "workbook": result["source"]["workbook"], "sha256": result["source"]["sha256"],
                "analysis_id": analysis_id(result), "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "analysis_json": json_name, "brief": brief_name, "stale_earlier_outputs": stale,
                "commentary_set_aside": commentary_set_aside, "commentary_set_aside_source": set_aside_source,
                "workbook_path": str(workbook.resolve()), "config": str(args.config) if args.config else None,
                "company_check": company_check, "superseded_outputs": superseded}
    (out_dir / "last_run.json").write_text(json.dumps(last_run, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print(brief)
    print(f"status={result['status']} analysis_json={out_dir / json_name} brief={out_dir / brief_name}",
          file=sys.stderr)
    if failed:
        print("VALIDATION FAILED: no commentary may be written from this run."
              + (f" Earlier outputs in {out_dir} ({', '.join(stale)}) were left untouched but do NOT reflect this "
                 "run; treat them as stale." if stale else ""), file=sys.stderr)
    if superseded:
        print(f"NOTE: {out_dir} held outputs of a different workbook (sha256 {superseded['sha256'] or 'unknown'}); "
              f"they were moved unchanged to {superseded['folder']}.", file=sys.stderr)
    if company_check["result"] == "confirmed_by_user":
        print(f"NOTE: company {company_check['config_company']!r} was confirmed by the user, not by the workbook "
              f"(workbook title names {company_check['workbook_company']!r}).", file=sys.stderr)
    if commentary_set_aside:
        print(f"NOTE: the analysis changed since commentary.md was written, so it was renamed to "
              f"{commentary_set_aside}. Write new commentary from this run's brief.", file=sys.stderr)
    return 2 if failed else 0


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _recorded_sha256(out_dir: Path) -> str | None:
    """The workbook sha256 the folder's existing outputs were produced from, or None if none records it."""
    for name in ("analysis.json", "analysis.failed.json", "last_run.json"):
        try:
            data = json.loads((out_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sha = data.get("sha256") if name == "last_run.json" else data.get("source", {}).get("sha256")
        if isinstance(sha, str):
            return sha
    return None


def _supersede_other_workbook(out_dir: Path, sha256: str) -> dict[str, str | None] | None:
    """If out_dir holds outputs of a different workbook content (or of one it cannot identify), move them,
    unchanged, into a superseded-<sha12>-<UTC time> subfolder so they cannot be read as this workbook's."""
    existing = [p for p in out_dir.iterdir() if p.is_file() and
                (p.name in RUN_FILES or (p.name.startswith("commentary") and p.suffix == ".md"))]
    previous = _recorded_sha256(out_dir)
    if not existing or previous == sha256:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"superseded-{(previous or 'unknown')[:12]}-{stamp}"
    n = 1
    while (target := out_dir / (base if n == 1 else f"{base}-{n}")).exists():
        n += 1
    target.mkdir()
    for path in existing:
        path.rename(target / path.name)
    return {"folder": target.name, "sha256": previous, "files": sorted(p.name for p in existing)}


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
