# Golden evaluation suite

Ten golden cases. Each pairs a small synthetic workbook (a *data state*) with the analysis output the engine must produce for it. The suite runs the **real CLI** (`python -m financial_analysis`), exactly as a user would, and grades the files it writes against expected values worked out **by hand**. It never imports or calls the engine's calculation code.

## Run it

```powershell
.venv\Scripts\python.exe evals/golden_cases/run_golden.py           # all cases; exit 1 if any check fails
.venv\Scripts\python.exe evals/golden_cases/run_golden.py --case 07 # one case (id prefix)
.venv\Scripts\python.exe evals/golden_cases/run_golden.py --keep    # keep the temp folders to inspect
```

The same cases run inside the normal test suite as `tests/test_golden_suite.py` (one pytest test per case, in pytest's `tmp_path`), so `python -m pytest` also fails on a golden regression. Nothing is needed beyond the project's existing dependencies (`openpyxl`, `pytest`). Excel is not required, and the supplied workbook is not used.

Example result (2026-10-12):

```
PASS  01_normal_month               43 checks  ...
...
10 cases: 10 passed, 0 failed; 281 checks, 0 failed
```

## Files

| File | Purpose |
|---|---|
| `cases/NN_*.json` | One case each: the fixture, the company config (or `null` for `--no-config`), the CLI steps and their expected results. Every expected number carries its arithmetic in `calc` |
| `fixtures.py` | openpyxl generators for the synthetic workbooks. Layout as the loader reads the supplied export (title rows, header in row 5, labels in A–C, months from D). Row numbers are commented |
| `run_golden.py` | The runner: builds the fixture in a fresh temp folder, runs each step, checks freshness, grades and prints a PASS/FAIL report |
| `../../tests/test_golden_suite.py` | pytest integration |

The fictional company is "Golden Test Co"; all figures are synthetic.

## The cases

| Case | Data state | What is verified |
|---|---|---|
| `01_normal_month` | 3 clean months, 2 revenue, 2 COGS and 5 opex accounts; Gross Profit, Operating Income and Net Income rows; config with 5 categories and Interest classed non-operating | Revenue 15,000; COGS 5,400; GP 9,600; GM 64.00%; opex 4,850 and by category (3,000 / 1,000 / 500 / 250 / 100); operating and net income 4,750; core opex 4,750 and core margin 32.33%; MoM +1,500 (+11.11%), GM +0.67 pp; YTD 40,500; TTM unavailable (`insufficient_history`); derived-row and subtotal checks pass; provenance (workbook name, SHA-256, company check) |
| `02_mom_and_ttm` | 14 months (Jan 2025–Feb 2026). Sales rise 100 a month to 2,200, then fall to 1,980. Travel is 0 until Feb 2026 | MoM decrease −220 (−10.00%) and increase +100 (+4.76%); YoY +880 (+80.00%); TTM revenue 20,680, GP 15,880, GM 76.79% (ratio of totals), opex 6,150, operating margin 47.05%; TTM series at Dec 2025 (18,600) and Jan 2026 (19,800); Nov 2025 TTM null; full-period total 22,780; highest/lowest month; ties to the earliest month; **zero base**: Travel MoM +150 with no % (`zero_base`) |
| `03_missing_month` | Jan, Feb, Apr, May 2026 (March absent) | `missing_period` warning for 2026-03, status `pass_with_warnings`, exit 0. March has no value. April's MoM is null (`comparison_period_not_in_data`), not +1,300. Full-period total and YTD are null, not computed as if March were 0. May MoM +100 (+7.69%) |
| `04_duplicate_period` | Two columns headed "Mar 2026" (F: 1,200, G: 9,999). No month missing | `duplicate_period` **error** naming columns G and F; `ignored_column` warning for G; status `fail`, exit 2; only `analysis.failed.json`, `brief.failed.md` and `last_run.json` written; brief says "Do not write commentary"; March revenue uses column F only (1,200, not 11,199); commentary check exits 1 |
| `05_unmapped_expense_account` | "Crypto Mining Fees" (row 17, 75 in March) matches no category rule | One `uncategorised_opex` **warning** naming the account, row 17, path, category "Other operating expenses", classification null and values `{2026-03: 75}`. Opex as presented 2,925 includes it; core opex 2,875 keeps it in core; category totals still reconcile; listed in the brief's "Accounts needing classification" table |
| `06_duplicates_and_subtotals` | A nested Payroll group with "Total Payroll"; "Software" on two rows; a stray "Total Marketing" (999) that closes no group | Opex 4,460 = the five accounts only (adding the subtotal would give 7,760, adding the stray total 5,459); both Software rows kept (100 and 60) with a `duplicate_account` warning; `unmatched_total` warning; neither total appears as an account; Payroll and Operating Expenses subtotal checks pass |
| `07_invalid_required_value` | Text "n/a" in Product Sales for March (F8) | Step 1, `--period 2026-02`: passes with a `non_numeric_value` warning naming F8; Feb revenue 1,310, GP 980. Commentary written and accepted (trace exit 0). Step 2, March: `required_metric_unavailable` **error** (revenue, GP, operating and net income; cause: Product Sales), exit 2, revenue null (never a partial sum). The step 1 `analysis.json`, `brief.md` and `commentary.md` are byte-identical; `brief.failed.md` says they are from an earlier run; `last_run.json` lists them as stale; trace exits 4 |
| `08_formula_cache` | No cached formula values (as openpyxl writes them); "Insurance" is `=40+5` | Exactly 4 `formula_evaluated` infos, one per row (not per cell), each with row, kind (`subtotal` or `leaf`), cells and periods. `recalculated_periods` on the reported totals; recalculated values equal the hand sums; Insurance 45 feeds opex 445; the brief says the values are "not figures Excel reported" and names each row once |
| `09_subtotal_mismatch` | Hard-coded "Total Income" 1,250 in March against an account of 1,200 | `subtotal_mismatch` **error** with reported 1,250 and calculated 1,200 for March only; exit 2; revenue taken from the account (1,200), not the reported total |
| `10_unrecognised_section` | An "Overheads" section no section alias recognises: Consulting Fees (row 17: 100, 100, 150) and Office Supplies (all 0); no derived rows to reconcile against | `unmapped_accounts` **error** listing both accounts and, under `accounts_with_amounts`, only Consulting Fees with its row and amounts; exit 2; failed outputs only, brief says "Do not write commentary". The failed analysis shows why: operating income 640 (1,200 − 360 − 200) against a true 490. Before the fix this run passed with a warning (exit 0) |

Cases 03 and 04 use different fixtures (`missing_month` and `duplicate_period`), and the suite asserts that they stay different.

## How a case is graded

Each step runs:

```
python -m financial_analysis <fixture> (--config <case config> | --no-config) --require-config
       --input-dir <temp>/data --out-dir <temp>/out --quiet [step args]
```

The runner then applies, in order:

1. **Exit code**: exact. If it differs, the step's remaining checks are skipped; their outputs would be meaningless.
2. **Freshness (anti-stale guard)**, for every run that writes outputs (exit 0 or 2). `last_run.json` must name this fixture's SHA-256 and this run's exit code, and its `run_at` must not be older than the run's start. The analysis is then read from the file `last_run.json` names (`analysis.json` or `analysis.failed.json`). A stale `analysis.json` left by an earlier passing run therefore can never be graded as this run's result.
3. **Checks** from the case file:

| Type | Comparison |
|---|---|
| `status`, `equals`, `issue_codes`, `issue_count`, `issue`, `files`, `file_contains`, `fixture_sha256`, `unchanged_since_step` | **Exact.** Statuses, issue identifiers and severities, account names, periods, rows, file sets and categorical values must match exactly. `issue_codes` compares the *complete* set of codes at a severity, so a new or vanished error or warning fails the case |
| `approx` | Absolute tolerance **±0.005** (default). The engine rounds amounts to cents and percentages / percentage points to 2 dp, half-up. Expected values are stated at that precision, so ±0.005 accepts only the correctly rounded value |
| `absent` | The path must not exist. Used to show nothing was invented, e.g. no value for a missing month |
| `trace_exit` | Exit code of `python -m financial_analysis.trace` on the output folder: the commentary/provenance check |

Paths use dots and list selectors, for example `analysis_period.metrics[metric=revenue].mom.pct_change` or `validation.checks[check=period_continuity].status`. A selector must match exactly one item. `"in": "last_run"` reads `last_run.json` instead of the analysis.

A failure prints the case, step, check (with its hand calculation), expected value, actual value and reason. For example:

```
FAIL  01_normal_month               43 checks  Normal month: ...
      step 1: analysis:analysis_period.metrics[metric=revenue].value  [Product Sales 12,000 + Service Revenue 3,000]
        expected: "15010 ± 0.005"
        actual:   15000.0
        reason:   outside tolerance
```

## Regression detection: what it has been shown to catch

On 2026-10-12 each of these deliberate engine faults was injected into a scratch copy, and the suite failed (exit 1) on the intended case:

| Injected fault | Failed case |
|---|---|
| TTM window of 11 months instead of 12 | 02 (10 checks) |
| Missing-month detection removed | 03 (6 checks) |
| Duplicate period downgraded to a warning | 04 (exit code) |
| Unexpected account downgraded to info | 05 (3 checks) |
| Required-metric check removed | 07 (exit code, step 2) |
| `last_run.json` not updated by failed runs (stale state) | 04, 07, 09 (freshness guard) |
| The engine before the fail-closed fix for unrecognised sections (HEAD `afb911d`, run 2026-10-12) | 10 (exit code: 0 instead of 2) |

A wrong expected value is caught too (the example above).

## Updating expected results after an intentional change

1. Change the engine and its unit tests first.
2. Run the suite and read every failure. Each must be explained by the intended change; anything else is a regression.
3. For each expected number that changes, **redo the calculation by hand** from the fixture's inputs (the comments in `fixtures.py`) and update both `value` and `calc` in the case file. Never paste a value from the engine's output into a case. The suite is only useful because its expectations are independent.
4. For a new or renamed issue identifier or status, update the exact `issue_codes` sets and the case README row, and say in the review why the categorical outcome changed.
5. Re-run until all cases pass, and have a second person review the changed expectations.

New behaviour gets a new case: add a generator to `fixtures.py` (register it in `FIXTURES`) and a `cases/NN_*.json`.

## Limitations

- **Commentary quality is not graded here.** The golden suite checks only that commentary is accepted or rejected according to provenance (case 07, `trace_exit`). The claim checks (misattributed or contradicted figures, forecasts, unsupported causes) have their own good and bad examples in `tests/test_commentary_claims.py`. Neither judges the prose as a whole: use the rubric below.
- **Excel-cached values are not covered here.** openpyxl cannot write cached formula results, so every fixture exercises the recalculation path. The cached-value path is covered by `tests/test_abc_workbook.py` and `tests/test_data_reliability.py` against the supplied workbook, when it is present.
- **One layout family.** The fixtures follow the supplied QuickBooks-style layout (income statement only). Balance-sheet behaviour, other layouts and section aliases are covered by unit tests, not golden cases.
- **The company mismatch check is not a golden case.** It stops the run before any output is written; `tests/test_provenance_safeguards.py` covers it.
- Expected values were calculated by hand and cross-checked only by the suite passing. A shared misunderstanding between the expectation and the engine would not be caught. Review step 5 above is the mitigation.

## Manual review rubric for commentary (not automated)

Score each item pass/fail when reviewing a `commentary.md` written by the skill. Any fail means the commentary is not sent.

1. **Numerical consistency.** Every figure appears in that folder's `brief.md` / `analysis.json` at the same rounding, with the right sign, month and label (monthly vs TTM). `python -m financial_analysis.trace <folder>` must exit 0 first. It checks each figure against what its sentence names, plus sign, direction and profit/loss wording. It does not catch a wrong claim without a number, or one whose sentence names nothing it recognises.
2. **Trends correctly identified.** Directions match the data (up/down, MoM vs YoY vs TTM), a TTM series is described by its path, not just its ends, and margin moves are in percentage points.
3. **No unsupported causes.** Any explanation is labelled as unconfirmed and framed as a question for the reviewer. No events, customers or contracts are invented.
4. **No fabricated figures.** No number appears that the pipeline did not calculate (e.g. a forecast or a self-computed total).
5. **Owner-focused and complete.** Every headline and margin highlight is covered. Warnings, missing data, unclassified accounts and the core vs as-presented distinction are stated, and the questions for the owner are clear and actionable.
