---
name: monthly-trend-analysis
description: Produce an owner-facing monthly financial trend analysis (revenue, margins, operating expenses by category, month-over-month and trailing-twelve-month views, balance-sheet movements) from an Excel workbook of monthly financial statements. Use when asked for a monthly trend analysis, month-end commentary, MoM/TTM review or "what moved this month" for a company workbook. Runs a deterministic Python pipeline for every number; Claude writes the commentary from its validated output.
---

# Monthly financial trend analysis

Python does all of the arithmetic and validation. Your job is to run it, check that its output can be trusted, and turn the result into a short analysis that a reviewer can finish and send to the business owner.

**Never calculate financial figures yourself.** Every number in your commentary must come from the `brief.md` or `analysis.json` saved in the analysis folder you are writing into. If a figure you want is not there, say it was not calculated. Do not work it out, and do not produce it with extra pipeline runs, scripts or files outside that folder (for example a temporary scratchpad): a figure the saved outputs cannot show is not traceable.

## Inputs

| Input | Default | Notes |
|---|---|---|
| Workbook | the `.xlsx` in `data/` | Monthly income statement sheet required; balance sheet optional |
| Company config | `config/companies/<company>.json` | Optional. Holds account classifications and expense categories. See [references/company-config.md](references/company-config.md) |
| Analysis month | latest month in the workbook | `--period YYYY-MM` |

If several workbooks or configs could apply, ask which ones to use. Do not guess.

## Step 1: Run the pipeline

Run this command exactly as shown. It works from any working directory:

```
python "${CLAUDE_SKILL_DIR}/scripts/run_analysis.py" "data/<workbook>.xlsx" --config config/companies/<company>.json --quiet
```

- Workbook and config paths can be relative to the repository root or to the current directory.
- On macOS or Linux, use `python3` if `python` is not available.
- The runner finds the repository root itself. If the Python you started it with lacks `openpyxl`, it re-runs itself with the repository's `.venv`.
- If it reports that `openpyxl` is not installed, the environment has not been created yet. Create it from the repository root: `python -m venv .venv`, then `.venv/Scripts/pip install -e .[dev]` on Windows or `.venv/bin/pip install -e .[dev]` on macOS/Linux.
- Add `--period YYYY-MM` to analyse a month other than the latest.

The pipeline writes these files to `out/<workbook name>/<YYYY-MM>/` under the repository root:

- `brief.md`: every figure you need, already rounded for presentation, plus validation results, assumptions and limitations.
- `analysis.json`: the full structured result at 2 decimal places. It has the monthly series for every metric, category and account, all comparisons, highlights and issues. Its fields are described in [references/output-schema.md](references/output-schema.md).
- `last_run.json`: the status of the latest run, which files it wrote, its `analysis_id` (a hash of the analysis content) and its `run_at` time.

If validation fails, the pipeline writes `brief.failed.md` and `analysis.failed.json` instead and leaves any earlier `analysis.json`, `brief.md` and `commentary.md` in the folder untouched. Those earlier files then describe a previous run, not this one.

If a passing run's analysis differs from the one already saved, an existing `commentary.md` is renamed `commentary.stale.md` (or `-2`, `-3`, ...). A stale commentary describes an earlier analysis: never present it, or quote from it, as current. `last_run.json` -> `commentary_set_aside_source` holds the source line it carried. When that is `null`, the commentary recorded no source and the analysis it came from is **unknown**: say so, and do not guess or reconstruct it.

## Step 2: Check the exit code and validation status

| Exit code / status | What to do |
|---|---|
| `1` (could not analyse) | Report the error message. Usually the workbook is unreadable, a sheet or section was not recognised, or the company config is invalid (the message names the key). Nothing was written. Stop. |
| `2` / `fail` | **Do not write commentary.** Read `brief.failed.md` and report each error from its Validation section: what failed, the months affected and the workbook row. Say that any `analysis.json`, `brief.md` or `commentary.md` in the folder is from an earlier run and is stale (`last_run.json` lists them). Ask the reviewer to resolve the errors. |
| `0` / `pass_with_warnings` | Continue, but open with the warnings and caveat every figure they affect. |
| `0` / `pass` | Continue. |

## Step 3: Read the results

Read all of `brief.md`. Use `analysis.json` only when you need something the brief leaves out, such as one account's monthly history or the `note` explaining why a comparison is missing. Search it for the key you need rather than reading the whole file.

For questions about the whole period rather than one month (total revenue, the highest- or lowest-revenue month, the largest month-over-month moves), use the "Revenue by month, full period" section of the brief, or `income_statement.revenue_history` in `analysis.json`. They hold the full-period total, every month's revenue with its prior month and MoM change, the largest MoM increase and decrease, and the highest and lowest month (`highest_month`, `lowest_month`; ties go to the earliest month). If the total is `n/a`, a month is missing or blank, and no total exists. If `extremes_note` is set (the brief says "extremes incomplete"), the highest and lowest month cover only the months with a value: say so. Never pick the highest or lowest month yourself from a table.

For how a TTM figure moved over time, use the "TTM trend" section of the brief (`income_statement.ttm` in `analysis.json`). Its rows are twelve-month totals and ratios, one per month-end; the "Monthly trend" table holds the single-month figures.

## Step 4: Write the analysis

Write roughly 350–600 words in Markdown, using these sections:

1. **Headline**: two or three sentences on the month: revenue, gross margin, core operating result and cash, each with its direction and size.
2. **Revenue**: the month-over-month (MoM) and year-over-year (YoY) change, and which streams drove it. Use the stream table and highlights. Give the TTM revenue and stream mix.
3. **Margins**: the gross margin and core operating margin (MoM in percentage points, YoY, TTM). Say which cost lines moved the gross margin.
4. **Operating expenses**: the categories that moved materially. Mention an account only when it explains a category's move or is flagged "Unusual".
5. **Trailing twelve months**: the TTM revenue, gross profit, core operating income and margins, and how this month compares with the TTM run-rate where the brief supports it.
6. **Cash and balance sheet** (if there is a balance sheet): the change in cash, working capital and liabilities, from the balance-sheet section only.
7. **For the reviewer to confirm**: one question per material movement whose cause the data cannot show.
8. **Data notes**: the validation status, the key assumptions (especially which accounts the "core" figures exclude) and the limitations. Keep this short.

### Rules

- **Facts and explanations are separate.** State what moved as fact, with figures. Any possible reason must be labelled and framed as something to confirm, for example: "Possible explanation (unconfirmed): …" or "Was this a one-off …?". The only basis for a possible explanation is what the data shows: account names, timing, or a repeat of the same pattern in earlier months (see "Unusual months across the full history"). Never present a cause as fact, and never invent events, customers, contracts or people.
- **Cover the highlights.** Every headline-metric and margin highlight must be addressed. Line-item highlights may be grouped. If you leave one out, it should be because a larger highlight already explains it.
- **Core vs as presented.** When the config classifies accounts, lead with the core figures and say once what they exclude. Net income includes those items. For example, Dec 2025 at ABC Inc has a large income-tax benefit inside operating expenses, so mention it whenever net income is compared across that month.
- **"As presented" follows the workbook.** Whenever you give an operating figure as presented (operating expenses, operating income or operating margin), say that it follows the workbook's own layout and includes the accounts listed in the brief's "As presented" note (for ABC Inc: interest and the income-tax benefit). Do not call such a figure simply "operating" without that qualifier.
- **Classifications are the config's, not yours.** Describe accounts using the classification the brief and `analysis.json` report, and say it is a configured judgment awaiting accounting-team confirmation where it matters. Never reclassify an account, move it between "core" and "as presented", or rename a category in the commentary. If a classification looks wrong, raise it under "For the reviewer to confirm".
- **Monthly vs TTM.** Label every figure as a single month or as TTM (twelve months to a month-end), and never let one stand for the other. "Improved each month" about a TTM series describes the rolling total, not the months themselves.
- **Core operating margin, both views.** Give the monthly core operating margin trend (from the "Monthly trend" table) and the TTM core operating margin trend (from the "TTM trend" table) as separate statements. If they move in different directions, say so.
- **Describe a TTM series by its path, not just its ends.** Check the "TTM trend" table before writing "rose" or "fell" between two dates. If the series peaked or bottomed in between, say where and what happened after. For example, at ABC Inc for June 2026 the table shows TTM gross profit increasing overall from Dec 2025, peaking in May 2026 and declining in June 2026. Write it that way only when the saved table shows it.
- **No unsupported causes.** A cause may appear only as a labelled, unconfirmed possibility under the first rule. Do not explain a movement with anything the saved outputs do not show.
- **Percentages.** Changes in margins are in percentage points, not %. A `*` means the % was calculated against a negative base. For losses, prefer the absolute change ("the loss narrowed by 8,344"). Where the brief shows `n/a (reason)`, give the reason (for example, "up from zero") and never put a % there.
- **Balance sheet.** These are month-end balances. Never add them up across months or describe them as TTM.
- **Rounding.** Use the figures exactly as the brief rounds them: whole currency units, and percentages to one decimal place.
- **Missing data.** Months listed under missing periods or missing values have no comparisons. Say that, and don't fill the gap.
- **Highlights are not exhaustive.** They come from fixed materiality rules, listed under Assumptions. Do not describe a sub-threshold movement as significant.

### Before you return the analysis, check:

- [ ] Every number appears in `brief.md` or `analysis.json`, at the same rounding.
- [ ] No sentence states a cause as fact.
- [ ] Every headline-metric and margin highlight is covered.
- [ ] Any warnings or errors are visible at the top.
- [ ] The core-vs-presented distinction and the limitations are stated, and every "as presented" operating figure says it follows the workbook and includes the configured items.
- [ ] Every figure is labelled monthly or TTM; the monthly and TTM core operating margin trends are given separately; no TTM series is described by its ends alone when it peaked or bottomed in between.
- [ ] No account classification or category was changed or reinterpreted.
- [ ] The last line is the source line copied from the top of `brief.md`.

Save the analysis as `commentary.md` in the same `out/` folder. Its last line must be the source line printed near the top of `brief.md`, copied exactly:

```
Source: analysis <YYYY-MM> · workbook sha256 <64 hex characters> · analysis_id <64 hex characters>
```

It records which analysis the commentary was written from, so a later reader can tell whether it is still current. Then check it mechanically:

```
python "${CLAUDE_SKILL_DIR}/scripts/check_commentary.py" "out/<workbook name>/<YYYY-MM>"
```

Exit `0` means the source line matches the saved analysis and every number was found in the saved outputs. Exit `4` means the source line is missing, duplicated or names a different period, workbook or analysis: copy it again from the current `brief.md` (if the analysis changed, rewrite the commentary from the new brief). Exit `3` lists each untraced number: replace it with a figure from the outputs, or say the figure was not calculated, and run the check again. Do not return the analysis until the check passes. The check proves a number came from the outputs, not that it is quoted with the right sign, month or label, so keep the rules above. Then show the analysis in the reply.

## Using the skill for another company

The pipeline locates sections, subtotals and months by structure and by configurable labels. It contains no account names, so a new company needs a config file, not a code change:

1. Run the pipeline with no `--config`. Check the Validation section for `unmapped_accounts`, `group_without_total`, `unmatched_total`, `ignored_column` and failed subtotal checks.
2. Create `config/companies/<company>.json` with only the keys you need to override: sheet or section aliases, account classes, expense-category rollups and thresholds. See [references/company-config.md](references/company-config.md).
3. Re-run until validation passes and every expense account sits in a sensible category. The accounting team must sign off the classifications and categories.

## Maintenance

- Code: `src/financial_analysis/`, split into `loader` → `calculations` → `trends` / `validation` / `highlights` → `pipeline` → `output`.
- Tests: `.venv/Scripts/python.exe -m pytest`.
- When you change a calculation or a highlight rule, update [references/output-schema.md](references/output-schema.md) and the tests together.
