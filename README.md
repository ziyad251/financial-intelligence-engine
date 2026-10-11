# Financial Intelligence Engine

This is a build exercise for RE Financial Services: connect QuickBooks Online to Claude, and turn monthly financial-statement analysis into a Claude Skill backed by deterministic code and tests.

| Part | Deliverable | Status |
|---|---|---|
| 1. QuickBooks ↔ Claude | Read-only MCP connection to a sandbox company | Working. [Integration README](integrations/quickbooks/README.md), [decision record](docs/integration-decision.md) |
| 2. Trend-analysis skill | [`.claude/skills/monthly-trend-analysis`](.claude/skills/monthly-trend-analysis/SKILL.md) and its Python pipeline | Working on the supplied workbook; 88 tests (78 run without the workbook, see Quick start) |
| 3. Eval suite (5+ golden cases) | `evals/` | **Not started.** `evals/golden_cases/` and `evals/fixtures/` are empty |
| 4. One-page architecture note | `docs/architecture.md` | **Not written** |

## Layout

```
.mcp.json                         QuickBooks MCP server config (no secrets)
.claude/settings.json             Deny rules for credential files
.claude/skills/monthly-trend-analysis/  The Claude Skill: SKILL.md, runner script, references
config/companies/                 Per-company settings: account classes, expense categories
data/                             Source workbook (read-only input; gitignored, so place it here locally)
docs/                             Integration decision, security (assignment brief PDF kept locally, not committed)
integrations/quickbooks/          QuickBooks setup, OAuth, tokens, troubleshooting
src/financial_analysis/           Deterministic pipeline: load → calculate → validate → highlight → output
tests/                            pytest suite (small hand-built workbooks + the ABC workbook)
vendor/                           Intuit's QuickBooks MCP server, built locally (gitignored)
out/                              Analysis outputs (gitignored; contains client figures)
```

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest
```

With the supplied workbook in `data/`, the suite runs 88 tests and all pass. Without it, as in a fresh clone, 78 pass and 10 are **skipped**: the 10 tests in `tests/test_abc_workbook.py` check the pipeline against the totals Excel cached in that workbook. The workbook is client data and is never committed, so those tests skip themselves when it is absent. Every other test builds small synthetic workbooks in a temporary directory.

### Run the analysis directly

```powershell
.venv\Scripts\python.exe .claude/skills/monthly-trend-analysis/scripts/run_analysis.py "data/ABC Inc - Financial Statements (1) (1).xlsx" --config config/companies/abc-inc.json
```

This is the supported command. The runner works from any directory and needs only `openpyxl` in the environment, not an installed package: it finds the repository root and puts `src/` on the import path itself.

`python -m financial_analysis <workbook> [options]` runs the same CLI, but only when the package is importable. Either install it with `pip install -e .` (the Quick start does this), or point `PYTHONPATH` at `src` (PowerShell: `$env:PYTHONPATH = "src"`). Without one of those it fails with `No module named financial_analysis`. Unlike the runner, it writes `out/` relative to the current directory.

Outputs go to `out/<workbook>/<YYYY-MM>/`. Add `--period YYYY-MM` to analyse another month. Exit codes:

| Exit code | Meaning | Files written |
|---|---|---|
| `0` | Validation passed, possibly with warnings | `analysis.json` (full structured result), `brief.md` (reviewer brief), `last_run.json`. If the figures changed since an existing `commentary.md` was written, that file is renamed to `commentary.stale.md` |
| `2` | Validation failed | `analysis.failed.json`, `brief.failed.md`, `last_run.json`. Earlier `analysis.json`, `brief.md` and `commentary.md` are left untouched and listed in `last_run.json` as stale |
| `1` | The workbook could not be read, or the company config is invalid (the message names the key) | Nothing |

### Run it as a Skill in Claude Code

Start Claude Code in the repository or any folder inside it (no setup step: the skill is discovered automatically) and ask for a monthly trend analysis, or invoke `/monthly-trend-analysis`. The Skill runs the pipeline and stops if validation fails. Otherwise it writes owner-facing commentary to `out/.../commentary.md` using only figures from the pipeline output.

## How the financial analysis works

All of the arithmetic is done in Python; Claude only interprets the results.

- **Loading.** The loader finds the month columns, sections, `Total …` rows and calculated lines from the workbook's structure. It contains no account names. Only individual accounts are ever summed, and the workbook's own totals are used just to reconcile against. That is how double-counting is prevented.
- **Checks.** These include:
  - every subtotal against its accounts;
  - reported gross profit, operating income and net income against the calculated figures;
  - the balance sheet balancing;
  - the month-to-month change in current-year earnings against net income;
  - missing months, blank cells (never treated as zero), text in number cells and duplicate months.
- **Metrics.** Revenue (by stream), cost of goods sold, gross profit and margin, operating expenses (as presented, by category, and "core" excluding configured non-operating accounts), operating and net income. Each comes with MoM, YoY, TTM and YTD views. Balance-sheet figures are month-end balances and are never summed into TTM.
- **New companies.** Another company needs a JSON file in `config/companies/`, not a code change. See [company-config.md](.claude/skills/monthly-trend-analysis/references/company-config.md).
- **Output format.** See [output-schema.md](.claude/skills/monthly-trend-analysis/references/output-schema.md).

## How the QuickBooks integration works

Claude Code starts Intuit's open-source MCP server (`vendor/quickbooks-online-mcp-server`, pinned commit) over stdio, as configured in `.mcp.json`:

- Credentials live in `%USERPROFILE%\.secrets\qbo\sandbox.env`, outside the repo.
- Create, update and delete tools are disabled. The 71 read tools include P&L, balance sheet, trial balance and general ledger.
- It connects to the sandbox only.

Setup, OAuth, token rotation, reconnection and team hosting are covered in [integrations/quickbooks/README.md](integrations/quickbooks/README.md) and [docs/integration-decision.md](docs/integration-decision.md). Credential and data rules are in [docs/security.md](docs/security.md).

The pipeline currently reads the Excel workbook only. Running the same skill against data from QuickBooks (the assignment's stretch goal) is not implemented.

## Skill location

The skill lives in `.claude/skills/monthly-trend-analysis/`, Claude Code's documented project-skill location. It is committed there as the single copy. Claude Code loads it in a session started in the repository or any folder inside it, with no links, scripts or install step. This was checked with Claude Code 2.1.296: the skill is listed in sessions started at the root, in `tests/` and in `src/financial_analysis/`, and not in a session started outside the repository.

Two alternatives were rejected:

- **A top-level `skills/` directory.** Claude Code only discovers that as part of a plugin. Making the repository root a plugin would also load the root `.mcp.json` as a second, plugin-scoped QuickBooks server, and would need a marketplace and an install step.
- **A junction or symlink into `.claude/skills/`.** This needs setup on every clone, and git follows junctions on Windows.

The skill's runner locates the repository root itself and is independent of the current directory. The Python engine in `src/` has no dependency on Claude Code and runs on its own with `python -m financial_analysis`.
