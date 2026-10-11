"""Run the monthly trend analysis pipeline for the monthly-trend-analysis skill.

Thin wrapper around `python -m financial_analysis` that works from any working
directory: it puts the repository's src/ on the import path and runs from the
repository root, so relative paths and the default out/ directory resolve there.

    <repo>/.venv/Scripts/python.exe skills/monthly-trend-analysis/scripts/run_analysis.py \
        "data/<workbook>.xlsx" --config config/companies/<company>.json [--period YYYY-MM]
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
# Paths given relative to the caller's directory stay valid after moving to the repo root.
sys.argv[1:] = [str(Path(a).resolve()) if not a.startswith("-") and Path(a).exists() else a for a in sys.argv[1:]]
os.chdir(REPO_ROOT)

from financial_analysis.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
