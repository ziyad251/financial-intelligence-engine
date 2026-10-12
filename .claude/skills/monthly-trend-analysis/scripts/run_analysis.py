"""Run the monthly trend analysis pipeline for the monthly-trend-analysis skill.

Thin wrapper around `python -m financial_analysis` that works from any working
directory: it finds the repository root (the directory containing
src/financial_analysis), puts src/ on the import path and runs from the root, so
relative paths and the default out/ directory resolve there. If the interpreter
it was started with lacks the pipeline's dependencies, it re-runs itself with the
repository's .venv interpreter.

    python <skill dir>/scripts/run_analysis.py "data/<workbook>.xlsx" \
        --config config/companies/<company>.json [--period YYYY-MM] [--confirm-company "<name>"]

It applies the skill's input policy: the workbook must be inside the repository's data/
folder (--input-dir is fixed to it and cannot be overridden), and a company config must
be named (--config, or --no-config for an onboarding run). If the workbook path is
omitted, the only workbook in data/ is used; several stop the run with their names listed.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "src" / "financial_analysis" / "__init__.py").is_file():
            return candidate
    sys.exit(f"ERROR: could not find src/financial_analysis above {start}")


REPO_ROOT = find_repo_root(Path(__file__).resolve().parent)

if importlib.util.find_spec("openpyxl") is None:
    for venv_python in (REPO_ROOT / ".venv" / "Scripts" / "python.exe", REPO_ROOT / ".venv" / "bin" / "python"):
        if venv_python.is_file() and Path(sys.executable).resolve() != venv_python.resolve():
            sys.exit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))
    sys.exit("ERROR: openpyxl is not installed. Create the environment first: "
             "python -m venv .venv, then install with: .venv/Scripts/pip install -e .[dev] (Windows) "
             "or .venv/bin/pip install -e .[dev]")

sys.path.insert(0, str(REPO_ROOT / "src"))
# Paths given relative to the caller's directory stay valid after moving to the repo root.
sys.argv[1:] = [str(Path(a).resolve()) if not a.startswith("-") and Path(a).exists() else a for a in sys.argv[1:]]
if any(a == "--input-dir" or a.startswith("--input-dir=") for a in sys.argv[1:]):
    sys.exit("ERROR: the skill only reads workbooks from the repository's data/ folder; --input-dir cannot be set.")
sys.argv[1:] = [*sys.argv[1:], "--input-dir", str(REPO_ROOT / "data"), "--require-config"]
os.chdir(REPO_ROOT)

from financial_analysis.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
