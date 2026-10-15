---
name: monthly-trend-analysis
description: Produce an owner-facing monthly financial trend analysis (revenue, margins, operating expenses by category, month-over-month and trailing-twelve-month views, balance-sheet movements) from an Excel workbook of monthly financial statements. Use when asked for a monthly trend analysis, month-end commentary, MoM/TTM review or "what moved this month" for a company workbook. Runs a deterministic Python pipeline for every number; Claude writes the commentary from its validated output.
---

# Monthly financial trend analysis

Python does all of the arithmetic and validation. Your job is to run it, check that its output can be trusted, and turn the result into a short analysis that a reviewer can finish and send to the business owner.

These instructions apply unchanged to any company. Everything company-specific (the company name, sheet and section labels, account classifications, expense categories, materiality thresholds, currency) lives in the company config and reaches you through the brief. Never carry a fact, account name or pattern over from another company's analysis.

**Never calculate financial figures yourself.** Every number in your commentary must come from the `brief.md` or `analysis.json` saved in the analysis folder you are writing into. If a figure you want is not there, say it was not calculated. Do not work it out, and do not produce it with extra pipeline runs, scripts or files outside that folder (for example a temporary scratchpad): a figure the saved outputs cannot show is not traceable.

## Inputs

| Input | Default | Notes |
|---|---|---|
| Workbook | the only `.xlsx`/`.xlsm` in `data/` | Must be inside the repository's `data/` folder (the runner enforces this). Monthly income statement sheet required; balance sheet optional |
| Company config | none: you must choose | `--config config/companies/<company>.json` for the workbook's company. `--no-config` only for onboarding a new company (see below). See [references/company-config.md](references/company-config.md) |
| Analysis month | latest month in the workbook | `--period YYYY-MM` |

Choosing the inputs:

- **Workbook.** If the user named a workbook, pass that exact path. If not, you may omit the path: the runner uses the only workbook in `data/`, and if there are several it stops (exit 1) and lists them. Then show the user the list and ask which one to use. Never pick one yourself, for example the first one listed. A workbook outside `data/` is refused: ask the user to copy it into `data/`.
- **Config.** Choose the config whose `company_name` is the workbook's company. A config's file name proves nothing. If no config clearly belongs to this company, or several could, ask the user. The runner refuses to run without `--config` or `--no-config`.
- **Company identity.** The runner compares the config's `company_name` with the company named in the workbook's title. If they differ, or the title names no company, it stops with exit 1. Do not add `--confirm-company` on your own. Tell the user what the config says and what the workbook says, and ask them to confirm. Only if they confirm, re-run with `--confirm-company "<the config's company_name>"`, and say in the Data notes that the company was confirmed by the user, not by the workbook.

## Step 1: Run the pipeline

Run this command exactly as shown. It works from any working directory:

```
python "${CLAUDE_SKILL_DIR}/scripts/run_analysis.py" "data/<workbook>.xlsx" --config config/companies/<company>.json --quiet
```

- Workbook and config paths can be relative to the repository root or to the current directory. The workbook must still be inside `data/`; `--input-dir` cannot be changed.
- On macOS or Linux, use `python3` if `python` is not available.
- The runner finds the repository root itself. If the Python you started it with lacks `openpyxl`, it re-runs itself with the repository's `.venv`.
- If it reports that `openpyxl` is not installed, the environment has not been created yet. Create it from the repository root: `python -m venv .venv`, then `.venv/Scripts/pip install -e .[dev]` on Windows or `.venv/bin/pip install -e .[dev]` on macOS/Linux.
- Add `--period YYYY-MM` to analyse a month other than the latest.

The pipeline writes these files to `out/<workbook name>/<YYYY-MM>/` under the repository root:

- `brief.md`: every figure you need, already rounded for presentation, plus validation results, assumptions and limitations.
- `analysis.json`: the full structured result at 2 decimal places. It has the monthly series for every metric, category and account, all comparisons, highlights and issues. Its fields are described in [references/output-schema.md](references/output-schema.md).
- `last_run.json`: the status of the latest run, which files it wrote, its `analysis_id` (a hash of the analysis content), its `run_at` time, the exact workbook path and config used, and the company check (`company_check.result`).

A folder holds the analysis of one workbook content only. If it already holds outputs from a different workbook (for example, another file saved under the same name), the runner first moves them, unchanged, into a `superseded-<sha256 prefix>-<time>/` subfolder and says so. Never use or quote files from a `superseded-*` folder as current.

If validation fails, the pipeline writes `brief.failed.md` and `analysis.failed.json` instead and leaves any earlier `analysis.json`, `brief.md` and `commentary.md` in the folder untouched. Those earlier files then describe a previous run, not this one.

If a passing run's analysis differs from the one already saved, an existing `commentary.md` is renamed `commentary.stale.md` (or `-2`, `-3`, ...). A stale commentary describes an earlier analysis: never present it, or quote from it, as current. `last_run.json` -> `commentary_set_aside_source` holds the source line it carried. When that is `null`, the commentary recorded no source and the analysis it came from is **unknown**: say so, and do not guess or reconstruct it.

## Step 2: Check the exit code and validation status

| Exit code / status | What to do |
|---|---|
| `1` (could not analyse) | Report the error message. Nothing was written. If it lists several workbooks, ask the user to choose one. If it is a company mismatch, or the workbook names no company, ask the user to confirm the company (see Inputs). If no config was given, choose one or ask. Otherwise the workbook is missing, outside `data/`, not an Excel file or unreadable, a sheet or section was not recognised, or the config is invalid (the message names the key). Stop. |
| `2` / `fail` | **Do not write commentary.** Read `brief.failed.md` and report each error from its Validation section: what failed, the months affected and the workbook row. Say that any `analysis.json`, `brief.md` or `commentary.md` in the folder is from an earlier run and is stale (`last_run.json` lists them). Ask the reviewer to resolve the errors. |
| `0` / `pass_with_warnings` | Continue, but open with the warnings and caveat every figure they affect. |
| `0` / `pass` | Continue. |

A `fail` includes `required_metric_unavailable`: a headline amount (revenue, COGS, gross profit, operating expenses, operating or net income) cannot be calculated for the analysis month because a required cell is blank or invalid. Report the cells the error names. The same blank or invalid cell in another month is only a warning (those comparisons are `n/a`), so an earlier month can still be analysed with `--period`.

A `fail` also includes `unmapped_accounts` as an error: accounts with amounts sit in a section the config does not recognise, so no metric includes them. Report each account, its row and its amounts from the error. Do not guess which section it belongs to; the config's `income_statement.sections` must be updated (an accounting decision) or the workbook corrected.

Some issues need specific handling in the commentary. Report each from the brief's own wording; never fill in or reinterpret what it describes:

- `uncategorised_opex` (the brief's "Accounts needing classification" section): an account the config does not recognise. List each one in the Data notes for the reviewer, with its row and the analysis-month value from that section. Say that it sits in "Other operating expenses" and is counted in core operating expenses until the accounting team classifies it, so the core figures may change. Never guess what the account is or how it should be classified.
- `config_account_not_found`, `classification_not_applied`, `empty_opex_category`: the config names an account or category that the workbook does not match, so the core figures or a category may not exclude or contain what the config intends. List each in the Data notes for the reviewer.
- `missing_value`, `non_numeric_value`, `formula_error`, `missing_period`: a month or cell has no usable value. Every comparison, TTM figure or total that depends on it is `n/a` in the brief. Say which months are affected and do not fill the gap.
- `formula_evaluated` (info): Excel had not saved the values of some formula cells, so the pipeline recalculated them. Say in the Data notes that those workbook totals were reconciled against recalculated formulas, not Excel's saved results.

## Step 3: Read the results

Read all of `brief.md`. Use `analysis.json` only when you need something the brief leaves out, such as one account's monthly history or the `note` explaining why a comparison is missing. Search it for the key you need rather than reading the whole file.

**Note what this analysis contains before you write.** The brief prints only what the workbook and config support:

| If the brief... | Then |
|---|---|
| has no core rows (no classification configured) | Use the operating figures as presented, omit every "core" statement, and say in the Data notes, as the Assumptions do, that they may include non-operating items |
| has core rows but no "As presented" note (the Assumptions list the core exclusions as "none found") | Core and as-presented figures are the same: call them operating figures, and say in the Data notes that the config's classified accounts were not found, so nothing is excluded |
| shows TTM as `n/a (<12 months)`, or its Limitations say there are no TTM figures | Say once that TTM figures are not available and why. Omit TTM statements |
| shows a TTM figure as `n/a (missing input)` | Say that the twelve months to that month include a missing value, so that TTM figure does not exist |
| shows YoY as `n/a (no data)` | Say that the same month last year is not in the workbook |
| shows a margin as `n/a (zero revenue)` or `n/a (negative revenue)` | Give that reason, never a margin |
| has no "Balance sheet" section | Omit the cash and balance-sheet findings and say the workbook has no balance sheet |
| says "No movements met the materiality thresholds" | Say so, and do not single out any movement as significant |

For questions about the whole period rather than one month (total revenue, the highest- or lowest-revenue month, the largest month-over-month moves), use the "Revenue by month, full period" section of the brief, or `income_statement.revenue_history` in `analysis.json`. If the total is `n/a`, a month is missing or blank and no total exists. If the brief says "extremes incomplete", the highest and lowest month cover only the months with a value: say so.

For how a TTM figure moved over time, use the "TTM trend" section. Its rows are twelve-month totals and ratios, one per month-end; the "Monthly trend" section holds the single-month figures.

**Read, never calculate.** You may describe the path a printed series takes (where it peaked, turned or kept moving). You may not produce a figure the outputs do not print: no sums, averages, differences, ratios, shares or rankings of your own. The pipeline's definitions are in [references/output-schema.md](references/output-schema.md).

## Step 4: Write the analysis

Write roughly 350–600 words in Markdown, in three clearly separated parts plus Data notes. Leave out any finding whose data the brief does not have (Step 3), and say so in the Data notes.

**Part A: Verified findings.** Facts only, each with its figure from the outputs.

1. **Headline**: two or three sentences on the month: revenue, gross margin, the operating result (core where it exists) and, where there is a balance sheet, cash. Give each one's direction and size.
2. **Revenue**: the MoM change and, where available, the YoY change. Name the streams whose changes account for it, with their figures. Give the TTM revenue and stream mix where available.
3. **Margins**: the gross margin and the operating margin (core where it exists): MoM in percentage points, and YoY and TTM where available. Name the cost lines whose changes account for the gross-margin move.
4. **Operating expenses**: the categories that moved materially. Mention an account only when its figure accounts for a category's move or it is flagged "Unusual".
5. **Trailing twelve months**: TTM revenue, gross profit, operating result and margins, and how this month compares with the TTM figures where the brief supports it.
6. **Cash and balance sheet**: the change in cash, working capital and liabilities, from the balance-sheet section only.

**Part B: Questions for the owner.** One question per material movement whose cause the data cannot show. A question names the movement, its figure and its month, and asks what caused it, whether it was expected or whether it will recur. It must not suggest an event, counterparty, price, volume or headcount that the outputs do not contain.

**Part C: Unconfirmed hypotheses.** Each is one sentence starting "Possible explanation (unconfirmed):", attached to one Part A finding. Its only permitted basis is what the outputs show (an account name, the timing of a movement, or the same pattern in earlier months under "Unusual months across the full history"), and the sentence states that basis. If the data gives no basis, write "None: the data does not suggest one."

**Data notes.** The validation status and any warnings; the key assumptions (especially which accounts the core figures exclude, if any); the limitations; and, for the reviewer, every account or config issue from Step 2. Keep this short.

### Rules

- **Facts and explanations are separate.** Part A states what moved. A cause appears only as a Part C hypothesis or a Part B question. The one exception is arithmetic: a line item whose quoted figure accounts for a movement ("<category> fell <amount>, driven by <account>, down <its change>"). Never invent causes, forecasts, events, customers, contracts, people or account classifications.
- **Cover the highlights.** Every headline-metric and margin highlight must be addressed. Line-item highlights may be grouped. If you leave one out, it should be because a larger highlight already accounts for it.
- **Core vs as presented.** When the brief has core figures, lead with them and say once what they exclude. Net income and every as-presented figure include those accounts. When one of them appears under "Unusual months across the full history", mention it whenever net income or an as-presented figure is compared across that month.
- **"As presented" follows the workbook.** When the brief has an "As presented" note, every operating figure you give as presented (operating expenses, operating income or operating margin) must say that it follows the workbook's own layout and includes the accounts that note lists. Do not call such a figure simply "operating" without that qualifier.
- **Classifications are the config's, not yours.** Describe accounts using the classification and category the brief reports, and say it is a configured judgment awaiting accounting-team confirmation where it matters. Never reclassify an account, move it between core and as presented, or rename a category. If one looks wrong, raise it in the Data notes for the reviewer.
- **Monthly vs TTM.** Label every figure as a single month or as TTM (twelve months to a month-end), and never let one stand for the other. "Improved each month" about a TTM series describes the rolling total, not the months themselves.
- **Operating margin, both views.** Where both exist, give the monthly operating-margin trend (from "Monthly trend") and the TTM trend (from "TTM trend") as separate statements. If they move in different directions, say so.
- **Describe a TTM series by its path, not just its ends.** Check the "TTM trend" section before writing "rose" or "fell" between two dates. If the series peaked or bottomed in between, say where and what followed: "TTM <metric> rose overall from <first month> to <analysis month>, peaking in <month> and falling in <month>". Write it that way only when the saved table shows it.
- **Percentages.** Changes in margins are in percentage points, not %. A `*` means the % was calculated against a negative base. For losses, prefer the absolute change ("the loss narrowed by <amount>"). Where the brief shows `n/a (reason)`, give the reason and never a %.
- **Balance sheet.** These are month-end balances. Never add them up across months or describe them as TTM.
- **Rounding and currency.** Use the figures exactly as the brief rounds them, in the currency the brief states.
- **Highlights are not exhaustive.** They come from the materiality rules listed under Assumptions. Do not describe a sub-threshold movement as significant.

### Before you return the analysis, check:

- [ ] Every number appears in `brief.md` or `analysis.json`, at the same rounding.
- [ ] Part A states no cause other than an arithmetic attribution; every hypothesis is in Part C, labelled, one sentence, with its data basis.
- [ ] No question or hypothesis names an event, counterparty, price, volume or headcount the outputs do not contain.
- [ ] Every headline-metric and margin highlight is covered.
- [ ] Warnings and errors are visible at the top, and every account or config issue from Step 2 is in the Data notes.
- [ ] Missing months, `n/a` comparisons and unavailable TTM or balance-sheet figures are stated, not filled.
- [ ] Where core figures exist, the core-vs-presented distinction is stated, and every as-presented operating figure says it follows the workbook and includes the configured accounts.
- [ ] Every figure is labelled monthly or TTM; no TTM series is described by its ends alone when it peaked or bottomed in between.
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

Exit `0` means the source line matches the saved analysis and every number was found in the saved outputs. Exit `4` means the source line is missing, duplicated or names a different period, workbook or analysis: copy it again from the current `brief.md` (if the analysis changed, rewrite the commentary from the new brief). Exit `4` also means the folder is not one current analysis: the latest run failed, or `brief.md`, `analysis.json` and `last_run.json` describe different runs. Then re-run the pipeline; never edit `last_run.json` or the source line to make the check pass. Exit `3` lists content findings, one per line, with the sentence. Fix each one and run the check again:

- `MISATTRIBUTED`: the number is not a figure of what the sentence names, for the month it names or the analysis month. Quote the right figure from the brief, and name the month whenever the figure is not the analysis month's.
- `CONTRADICTS`: the figure is right, but its sign, direction word ("rose", "fell", "narrowed"...) or "profit"/"loss" says the opposite of the analysis. Correct the wording.
- `FORECAST`: a number in a forward-looking sentence. Remove it; the analysis makes no forecasts.
- `UNSUPPORTED_CAUSE`: a cause stated as fact. Either quote the line item and its figure that account for the movement, or move it to Part C as "Possible explanation (unconfirmed): ..." or to Part B as a question.
- `UNTRACED`: a number in a sentence that names no metric, account or category and is not printed in `brief.md`. Replace it with a figure from the brief, or say it was not calculated.

Do not return the analysis until the check passes. The check reads a fixed vocabulary, so a wrong claim without a number, or one whose sentence names nothing it recognises, can still pass; it also does not tell % from pp, so write margin changes in pp yourself. Keep the rules above. Then show the analysis in the reply.

## Using the skill for another company

The pipeline locates sections, subtotals and months by structure and by configurable labels. It contains no account names, so a new company needs a config file, not a code change and not a change to these instructions:

1. Run the pipeline with `--no-config`. Check the Validation section for `unmapped_accounts`, `group_without_total`, `unmatched_total`, `ignored_column` and failed subtotal checks.
2. Create `config/companies/<company>.json` with `company_name` set to the company as named in the workbook's title (ask the user if unsure; never invent it), plus only the keys you need to override: sheet or section aliases, account classes, expense-category rollups and thresholds. See [references/company-config.md](references/company-config.md).
3. Re-run until validation passes and every expense account sits in a sensible category. The accounting team must sign off the classifications and categories.

## Maintenance
- Code: `src/financial_analysis/`, split into `loader` → `calculations` → `trends` / `validation` / `highlights` → `pipeline` → `output`.
- Tests: `python -m pytest` with the repository's `.venv` active. `tests/test_skill_reusability.py` fails if these instructions name a configured company, one of its accounts or categories, a specific month or a specific amount; keep examples as `<placeholders>`.
- When you change a calculation or a highlight rule, update [references/output-schema.md](references/output-schema.md) and the tests together.
