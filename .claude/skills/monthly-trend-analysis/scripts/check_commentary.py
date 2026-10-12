"""Check commentary.md against the saved analysis outputs: its source line must name the saved
analysis, and every number in it must appear in analysis.json or brief.md.

Thin wrapper around `python -m financial_analysis.trace` that works from any working
directory. The output folder can be given relative to the current directory or to the
repository root. It needs only the standard library, so no virtual environment.

    python <skill dir>/scripts/check_commentary.py "out/<workbook>/<YYYY-MM>"

Exit codes: 0 = source line matches and every number traced, 4 = source line missing or not
matching the saved analysis, 3 = untraced numbers (listed), 1 = files missing.
"""

import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "src" / "financial_analysis" / "__init__.py").is_file():
            return candidate
    sys.exit(f"ERROR: could not find src/financial_analysis above {start}")


REPO_ROOT = find_repo_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(REPO_ROOT / "src"))
argv = [a if a.startswith("-") or Path(a).exists() or not (REPO_ROOT / a).exists() else str(REPO_ROOT / a)
        for a in sys.argv[1:]]

from financial_analysis.trace import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(argv))
