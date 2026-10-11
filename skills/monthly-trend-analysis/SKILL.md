---
name: monthly-trend-analysis
description: Produce an owner-facing monthly financial trend analysis (revenue, margins, operating expenses by category, month-over-month and trailing-twelve-month views, balance-sheet movements) from an Excel workbook of monthly financial statements. Use when asked for a monthly trend analysis, month-end commentary, MoM/TTM review or "what moved this month" for a company workbook. Runs a deterministic Python pipeline for every number; Claude writes the commentary from its validated output.
---

# Monthly financial trend analysis

Python does all of the arithmetic and validation. Your job is to run it, check that its output can be trusted, and turn the result into a short analysis that a reviewer can finish and send to the business owner.

**Never calculate financial figures yourself.** Every number in your commentary must come from `brief.md` or `analysis.json`. If a figure you want is not there, say it was not calculated. Do not work it out.

## Inputs

| Input | Default | Notes |
|---|---|---|
| Workbook | the `.xlsx` in `data/` | Monthly income statement sheet required; balance sheet optional |
| Company config | `config/companies/<company>.json` | Optional. Holds account classifications and expense categories. See [references/company-config.md](references/company-config.md) |
| Analysis month | latest month in the workbook | `--period YYYY-MM` |

If several workbooks or configs could apply, ask which ones to use. Do not guess.

## Step 1: Run the pipeline

Run this from the repository root. On Windows:

```
.venv/Scripts/python.exe skills/monthly-trend-analysis/scripts/run_analysis.py "data/<workbook>.xlsx" --config config/companies/<company>.json --quiet
```

On macOS or Linux, use `.venv/bin/python`. Add `--period YYYY-MM` to analyse a month other than the latest. If `.venv` does not exist, create it with `python -m venv .venv` and then run `.venv/Scripts/pip install -e .[dev]`.

The pipeline writes two files to `out/<workbook name>/<YYYY-MM>/`:

- `brief.md`: every figure you need, already rounded for presentation, plus validation results, assumptions and limitations.
- `analysis.json`: the full structured result at 2 decimal places. It has the monthly series for every metric, category and account, all comparisons, highlights and issues. Its fields are described in [references/output-schema.md](references/output-schema.md).

## Step 2: Check the exit code and validation status

| Exit code / status | What to do |
|---|---|
| `1` (could not analyse) | Report the error message. Usually a sheet or section was not recognised, which is fixed in the company config. Stop. |
| `2` / `fail` | **Do not write commentary.** Report each error from the Validation section: what failed, the months affected and the workbook row. Ask the reviewer to resolve them. |
| `0` / `pass_with_warnings` | Continue, but open with the warnings and caveat every figure they affect. |
| `0` / `pass` | Continue. |

## Step 3: Read the results

Read all of `brief.md`. Use `analysis.json` only when you need something the brief leaves out, such as one account's monthly history or the `note` explaining why a comparison is missing. Search it for the key you need rather than reading the whole file.

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
- [ ] The core-vs-presented distinction and the limitations are stated.

Save the analysis as `commentary.md` in the same `out/` folder, and show it in the reply.

## Using the skill for another company

The pipeline locates sections, subtotals and months by structure and by configurable labels. It contains no account names, so a new company needs a config file, not a code change:

1. Run the pipeline with no `--config`. Check the Validation section for `unmapped_accounts`, `group_without_total`, `unmatched_total`, `ignored_column` and failed subtotal checks.
2. Create `config/companies/<company>.json` with only the keys you need to override: sheet or section aliases, account classes, expense-category rollups and thresholds. See [references/company-config.md](references/company-config.md).
3. Re-run until validation passes and every expense account sits in a sensible category. The accounting team must sign off the classifications and categories.

## Maintenance

- Code: `src/financial_analysis/`, split into `loader` → `calculations` → `trends` / `validation` / `highlights` → `pipeline` → `output`.
- Tests: `.venv/Scripts/python.exe -m pytest`.
- When you change a calculation or a highlight rule, update [references/output-schema.md](references/output-schema.md) and the tests together.
