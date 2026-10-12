# analysis.json — field reference (schema 1.0)

The result is deterministic for a given workbook, config and period: no timestamps and stable ordering. That lets evaluations compare it field by field. Amounts are rounded half-up to 2 dp, and percentages and percentage points to 2 dp. Month keys are `YYYY-MM`.

## Top level

| Field | Meaning |
|---|---|
| `schema_version`, `engine_version` | Bump `SCHEMA_VERSION` in `src/financial_analysis/__init__.py` when fields change meaning |
| `source` | The workbook file name, its `sha256` and the sheets used. The hash shows which data state produced the result |
| `config` | The config file name, whether classification and rollups are configured, the thresholds and the fiscal-year start |
| `status` | `pass` / `pass_with_warnings` / `fail`, the worst issue severity. `fail` means no commentary is written |
| `periods` | `available`, `analysis_period`, `prior_period`, `same_period_last_year` (null if absent), `missing_periods` and `materiality_threshold` |
| `income_statement` | Full monthly data. See below |
| `analysis_period` | Rows ready for commentary on the analysis month. See below |
| `balance_sheet` | Point-in-time balances, or null when there is no balance sheet |
| `highlights`, `highlights_dropped` | Rule-based P&L movements for the analysis month, ranked, plus the number cut off by `max_highlights` |
| `history_outliers` | The largest unusual account values across every month (one-offs and recurring peaks) |
| `validation` | `status`, `counts` by severity, `checks` and `issues` |
| `assumptions`, `limitations` | Plain-English statements generated from the data state |

## income_statement

- `sections`: maps each section key (`revenue`, `cogs`, `opex`, `other_income`, `other_expense`) to the label of the group it was found under, or null.
- `monthly.<metric>`: `{label, unit: amount|percent, basis: "calculated", values: {month: number|null}, notes: {month: reason}}`.
- `ttm.<metric>`: `{values, notes}`. Amounts are 12-month sums. Ratios are the TTM numerator divided by TTM revenue.
- `revenue_history`: revenue across the whole workbook, independent of the analysis month.
  - `{label, basis, first_period, last_period, months_in_data, total, total_note, monthly[], largest_mom_increase, largest_mom_decrease}`.
  - `total` is the sum of every month from `first_period` to `last_period`. It is null, with `total_note`, when a month is missing (`comparison_period_not_in_data`) or blank (`missing_value`): no total is given for a partial period.
  - `monthly[]` is `{period, value, prior_period, prior_value, abs_change, pct_change, note}` for every month in the data. It follows the same rules as the `mom` comparison objects below: the first month and the month after a gap have no comparison, and there is no % from a zero base or across a sign change.
  - `largest_mom_increase` / `largest_mom_decrease` are copies of the `monthly[]` rows with the largest positive and most negative `abs_change`. Ties go to the earliest month. Each is null when there is no such move.
  - `highest_month` / `lowest_month` are `{period, value}` for the month with the highest and lowest revenue, taken from the same monthly values. Ties go to the earliest month. Blank months are skipped. Both are null when no month has a value.
  - `extremes_note` is null when every month from `first_period` to `last_period` has a value. Otherwise the extremes cover only the months with a value, and the note says why: `comparison_period_not_in_data` (a month is missing from the data) or `missing_value` (a month is blank).
- `reported_totals.<key>`: the workbook's own subtotal or derived row (`basis: "reported"`) with its `source_row`. These are used only for reconciliation.
- `opex_categories[]`: `{name, accounts[], values}`. Each operating-expense account belongs to exactly one category.
- `accounts[]`: every leaf account, with `{key, label, section, path, category, classification, source_row, values}`.

### Metrics

Every metric is calculated from leaf accounts only.

| Metric | Definition |
|---|---|
| `revenue`, `cogs`, `opex_total` | Sum of the section's accounts |
| `gross_profit` | revenue − cogs. Absent when there is no COGS section |
| `operating_income` | revenue − cogs − opex_total (as the statement presents it) |
| `non_operating_in_opex` | The opex accounts classified `non_operating` or `income_tax` in the config |
| `core_opex`, `core_operating_income` | opex_total − non_operating_in_opex, and operating_income + non_operating_in_opex |
| `other_income`, `other_expense` | Present only if the workbook has those sections |
| `net_income` | operating_income + other_income − other_expense |
| `non_cash_expenses` | The accounts classified `non_cash`. This is a memo figure and is not excluded from core opex |
| `gross_margin`, `operating_margin`, `core_operating_margin`, `net_margin`, `opex_to_revenue` | numerator ÷ revenue × 100 |

When no classification is configured, the core metrics are omitted.

## analysis_period

- `metrics[]`: `{metric, label, unit, value, value_note, mom, yoy, ttm, ttm_note, ytd, ytd_note}`.
- `revenue_streams[]`, `cogs_accounts[]`, `opex_categories[]`, `opex_accounts[]`, `other_accounts[]`: `{key, label, value, mom, yoy, ttm, ttm_note, ttm_pct_of_revenue, median_other_months, …}`.
- Comparison objects (`mom`, `yoy`):
  - Amounts: `{current, prior, abs_change, pct_change, note, comparison_period}`. `pct_change` is in % and is relative to |prior|.
  - Percent metrics: `{current, prior, pp_change, note, comparison_period}`.

### Note and reason codes

| Code | Meaning |
|---|---|
| `comparison_period_not_in_data` | The earlier month is not in the workbook |
| `missing_value` | An input month is blank or unusable, so the result is null |
| `zero_base` | The earlier value is 0, so there is no % change |
| `sign_change` | The value crossed zero, so there is no % change |
| `negative_base` | The % was calculated against a negative base (the brief marks it `*`) |
| `insufficient_history` | Fewer than 12 months end at this month, so there is no TTM |
| `zero_denominator`, `negative_denominator` | Revenue is ≤ 0, so there is no margin |

## Highlights

Each highlight is `{rank, type, scope, key, label, period, comparison, current, baseline, abs_change | pp_change, pct_change, direction, profit_effect, materiality_threshold | threshold_pp}`.

- `type`: `mom_change`, `yoy_change` or `unusual_vs_history`.
- `scope`: `metric`, `margin`, `revenue_stream`, `cogs_account`, `opex_category`, `opex_account` or `balance_sheet`.
- `materiality(month)` = max(`materiality_abs`, `materiality_pct_of_revenue` × |revenue|).

| Kind | Rule |
|---|---|
| Headline metric (MoM, YoY) | \|abs change\| ≥ materiality |
| Margin (MoM, YoY) | \|pp change\| ≥ `margin_move_pp` |
| Line item (MoM; also YoY for streams and categories) | \|abs\| ≥ materiality and (\|%\| ≥ `min_mom_pct` or % undefined) |
| Unusual vs history | \|value − median of all other months\| ≥ materiality and ≥ `one_off_min_ratio_to_median` × \|median\| |
| Balance sheet (`balance_sheet.highlights`) | \|change\| ≥ `bs_materiality_abs` (MoM and vs 12 months earlier) |

Highlights are ordered as headline metrics, then margins, then line items. Within each group they are sorted by size, and ties are broken by type and key.

## Validation

Each entry in `checks[]` is `{check, status: pass|fail|skipped|flagged, description, …}`. The checks are:

- `subtotal:<sheet>:<group>`: every `Total X` row equals the sum of the leaf accounts under X. A failure is an **error** for section-level groups and a warning for nested groups. This is the double-counting guard.
- `derived:gross_profit`, `derived:operating_income`, `derived:net_income`: the reported rows equal the calculated figures (error).
- `opex_categories_complete`: the categories add up to opex_total (error).
- `balance_sheet_balances`: assets = liabilities + equity (error).
- `earnings_rollforward`: the monthly change in the balance sheet's current-year earnings equals net income. At the fiscal-year start month it equals net income itself (warning).
- `negative_values`: revenue or expense accounts with negative postings that are not configured as expected (info).
- `missing_values`, `period_continuity`: blank account cells and gaps between months.

Each entry in `issues[]` is `{code, severity, message, sheet?, account?, period?, details?}`. The codes raised by the loader are `missing_value`, `non_numeric_value`, `formula_error`, `formula_unevaluated`, `formula_evaluated`, `missing_period`, `duplicate_period`, `ignored_column`, `unlabelled_row`, `orphan_row`, `group_without_total`, `unmatched_total` and `formula_row_excluded`. `empty_opex_category` (warning) means a configured expense category matched no account and is reported as 0.

## Output files

Each output folder holds the analysis of one workbook content. Before writing, a run compares the workbook's SHA-256 with the one the folder's existing outputs record (`analysis.json`, else `analysis.failed.json`, else `last_run.json`). If they differ, or the existing outputs record none, every earlier run file and `commentary*.md` is moved, unchanged, into `superseded-<earlier sha256[:12]>-<UTC time>/` inside the folder, and `last_run.json` names it under `superseded_outputs`. So a different workbook saved under the same file name never shares a folder with the earlier analysis. The rows below describe a folder whose outputs are from the same workbook content.

| Run outcome | Files written to `out/<workbook>/<YYYY-MM>/` |
|---|---|
| Exit 0 (`pass`, `pass_with_warnings`) | `analysis.json`, `brief.md`, `last_run.json`. Any `*.failed.*` files from an earlier failed run are removed. If the analysis differs from the `analysis.json` it replaces, an existing `commentary.md` is renamed to `commentary.stale.md` (`-2`, `-3`, … if taken) and named in `last_run.json` as `commentary_set_aside` |
| Exit 2 (`fail`) | `analysis.failed.json`, `brief.failed.md`, `last_run.json`. Earlier `analysis.json`, `brief.md` and `commentary.md` are left untouched and listed in `last_run.json` as `stale_earlier_outputs` |
| Exit 1 (unreadable workbook or invalid config) | Nothing |

`last_run.json` is `{status, exit_code, analysis_period, workbook, sha256, analysis_id, run_at, analysis_json, brief, stale_earlier_outputs, commentary_set_aside, commentary_set_aside_source, workbook_path, config, company_check, superseded_outputs}`.

- `analysis_id` is the SHA-256 of the analysis JSON with sorted keys (`output.analysis_id`). The analysis has no timestamps, so an unchanged analysis keeps the same id. Anyone can recompute it from the saved `analysis.json`.
- `run_at` is the UTC time of the run. It lives only here, so `analysis.json` stays comparable across runs.
- `commentary_set_aside_source` is `{analysis_period, workbook_sha256, analysis_id}` from the source line of the commentary this run set aside, or null when it set none aside or the commentary had no source line (its origin is then unknown).
- `workbook_path` is the absolute path of the workbook analysed; `config` is the `--config` path, or null.
- `company_check` is `{config_company, workbook_company, confirmed_company, result}`. `result` is `matched_workbook_title` (the workbook's title names the config's `company_name`), `confirmed_by_user` (it did not, and the user confirmed the company with `--confirm-company`), or `not_checked_no_company_in_config` (no config, or one without `company_name`). A mismatch without confirmation stops the run with exit 1.
- `superseded_outputs` is `{folder, sha256, files}` when this run moved another workbook's outputs aside, else null.
- Whether a commentary is stale is still decided by comparing the whole saved `analysis.json` with the new result, not by the id.

`brief.md` prints the commentary source line, `Source: analysis <YYYY-MM> · workbook sha256 <hex> · analysis_id <hex>`, near the top. A commentary ends with it, and `python -m financial_analysis.trace <folder>` (exit 4) checks it against the saved analysis. It also exits 4 when the folder is not one current analysis: `analysis.json` records a failed validation, `brief.md` does not print the source line of `analysis.json`, or `last_run.json` (when present) records a failed latest run or a different period, workbook SHA-256 or `analysis_id`. `last_run.json` can only reject a commentary; acceptance rests on `analysis.json`, from which the `analysis_id` is recomputed.
