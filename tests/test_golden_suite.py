"""The golden evaluation suite (evals/golden_cases) as part of the normal pytest run.

Each case runs the real CLI on a synthetic workbook inside pytest's tmp_path and is graded
against hand-calculated expectations. See evals/golden_cases/README.md. The standalone
command is: python evals/golden_cases/run_golden.py
"""

import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden_cases"
sys.path.insert(0, str(GOLDEN))

from run_golden import load_cases, report, run_case  # noqa: E402

CASES = load_cases()


def test_suite_has_the_required_cases():
    ids = [c["id"] for c in CASES]
    assert len(ids) >= 7 and len(ids) == len(set(ids))
    # Missing and duplicated periods are separate cases on separate fixtures.
    by_id = {c["id"]: c for c in CASES}
    assert by_id["03_missing_month"]["fixture"] != by_id["04_duplicate_period"]["fixture"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case, tmp_path):
    result = run_case(case, workdir=tmp_path)
    assert result.checks > 0
    assert result.passed, "\n" + report([result])
