# Company config

The pipeline works without a config for workbooks laid out like a QuickBooks monthly export. A company config is a JSON file holding **only the keys it overrides**, and it is deep-merged onto `DEFAULTS` in `src/financial_analysis/config.py`. Top-level keys starting with `_` (such as `_comment`) are comments and are ignored; anywhere else they are rejected as unknown keys.

The config holds accounting judgment: which accounts are non-operating, and how expenses roll up into categories an owner can read. The accounting team owns those decisions and must review the file. Engineers should not settle them in code.

## How the workbook is read

The pipeline needs no account names, only the layout:

- **Months**: the first row in the top 30 with at least two month headers, such as `Jun 2026`, `30 Jun 2026`, `2026-06`, `Jun-26` or a date cell. The column order does not matter. Columns that are not months but hold data (such as `Total`) are ignored with a warning.
- **Groups**: a labelled row with no values opens a group. A `Total <group label>` row closes it and is that group's reported subtotal.
- **Accounts**: rows with values that are not totals and not calculated lines. These are the only rows ever summed.
- **Derived lines**: rows named in `derived_rows` (Gross Profit, Operating Income, Net Income), and any other row whose cells are formulas referencing other cells. These are used for reconciliation only.

## Keys

| Key | Default | Use |
|---|---|---|
| `company_name` | null | The company this config is for, exactly as the workbook's title names it (punctuation, spacing and case are ignored). The pipeline refuses a workbook whose title names another company, or no company, unless the user confirms with `--confirm-company`. The skill requires it. Never guess it |
| `currency` | `USD` | Display only |
| `fiscal_year_start_month` | `1` | Sets the YTD window and the earnings roll-forward reset |
| `blank_cells_as_zero` | `false` | Set this to true only if the accounting team confirms that blanks mean zero in this export |
| `reconciliation_tolerance` | `0.01` | The largest difference tolerated in subtotal checks |
| `income_statement.sheet_names` / `balance_sheet.sheet_names` | common names | Sheet name aliases, matched exactly and case-insensitively |
| `income_statement.sections.<revenue\|cogs\|opex\|other_income\|other_expense>` | common labels | Group labels for each section. The section may be nested, as in QuickBooks Desktop's "Ordinary Income/Expense" |
| `income_statement.derived_rows` | Gross Profit, Operating/Net Income variants | Labels of reported calculated lines to reconcile against |
| `income_statement.account_classes.non_operating` / `income_tax` | `[]` | Operating-expense accounts excluded from the **core** figures |
| `income_statement.account_classes.non_cash` | `[]` | A memo total only. These accounts are not excluded from core figures |
| `income_statement.expected_negative_accounts` | `[]` | Accounts whose negative values should not be flagged (for example, interest income booked as a contra-expense) |
| `income_statement.opex_rollups` | `[]` | An ordered list of `{"name", "match": [regex]}`. Each regex is matched against the account path, such as `Operating Expenses > S & M Payroll > Sales - Payroll`, and the first match wins. Unmatched accounts go to "Other operating expenses". Each one is an `uncategorised_opex` **warning** naming the account, its row and its values, and is listed in the brief's "Accounts needing classification" section. It is counted in core operating expenses until it is classified, and is never classified automatically. When the list is empty, each direct child of the opex section is its own category |
| `balance_sheet.groups.<assets\|liabilities\|equity\|current_assets\|current_liabilities\|cash>` | common labels | Group labels used for the balance-sheet metrics |
| `balance_sheet.current_year_earnings_accounts` | Current Year Earnings, Net Income | The account used for the earnings roll-forward check |
| `thresholds.*` | see config.py | Materiality rules for highlights. See output-schema.md |

Every configured classification label must match an account exactly, apart from case and whitespace. If one does not, the pipeline warns with `config_account_not_found`, which catches typos and renamed accounts. An `opex_rollups` entry whose patterns match no account is reported as 0 with an `empty_opex_category` warning.

The config is validated before any analysis runs. Unknown keys (at any level, so a misspelled `income_statment` is not silently ignored), values of the wrong type, out-of-range numbers and invalid regular expressions stop the run with exit code 1 and a message naming the offending key, for example `Invalid company config ...: unknown key 'income_statment'. Did you mean 'income_statement'?`.

## Onboarding checklist

1. Run the pipeline with `--no-config` and read the Validation section.
2. If no section or sheet is found, add aliases.
3. Classify the non-operating and income-tax accounts that the statement presents inside operating expenses.
4. Add `opex_rollups` until the "Other operating expenses" category is empty or deliberately small.
5. Set `company_name` to the company named in the workbook's title (confirm it with the user).
6. Re-run until the status is `pass` and every subtotal check passes. Get the classifications and categories signed off.

Example: `config/companies/abc-inc.json`.
