# Financial Intelligence Engine

This is a build exercise for RE Financial Services: connect QuickBooks Online to Claude, and turn monthly financial-statement analysis into a Claude Skill backed by deterministic code and tests.

| Part | Deliverable | Status |
|---|---|---|
| 1. QuickBooks ↔ Claude | Read-only MCP connection to a sandbox company | Working. [Integration README](integrations/quickbooks/README.md), [decision record](docs/integration-decision.md) |
| 2. Trend-analysis skill | [`skills/monthly-trend-analysis`](skills/monthly-trend-analysis/SKILL.md) and its Python pipeline | Working on the supplied workbook; 62 unit and integration tests |
| 3. Eval suite (5+ golden cases) | `evals/` | **Not started.** `evals/golden_cases/` and `evals/fixtures/` are empty |
| 4. One-page architecture note | `docs/architecture.md` | **Not written** |

## Layout

```
.mcp.json                         QuickBooks MCP server config (no secrets)
.claude/settings.json             Deny rules for credential files
.claude/skills/                   Local links to skills/ for discovery (gitignored; see below)
config/companies/                 Per-company settings: account classes, expense categories
data/                             Source workbook (read-only input; gitignored, so place it here locally)
docs/                             Integration decision, security (assignment brief PDF kept locally, not committed)
integrations/quickbooks/          QuickBooks setup, OAuth, tokens, troubleshooting
scripts/link-skills.{ps1,sh}      Make skills/ discoverable by Claude Code
skills/monthly-trend-analysis/    The Claude Skill: SKILL.md, runner script, references
src/financial_analysis/           Deterministic pipeline: load → calculate → validate → highlight → output
tests/                            pytest suite (small hand-built workbooks + the ABC workbook)
vendor/                           Intuit's QuickBooks MCP server, built locally (gitignored)
out/                              Analysis outputs (gitignored; contains client figures)
```

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .[dev]
.venv\Scripts\python -m pytest                     # 62 tests
powershell -ExecutionPolicy Bypass -File scripts\link-skills.ps1   # once per clone (macOS/Linux: sh scripts/link-skills.sh)
```

### Run the analysis directly

```powershell
.venv\Scripts\python.exe skills/monthly-trend-analysis/scripts/run_analysis.py "data/ABC Inc - Financial Statements (1) (1).xlsx" --config config/companies/abc-inc.json
```

This writes `out/<workbook>/<YYYY-MM>/analysis.json` (full structured result) and `brief.md` (reviewer brief). Add `--period YYYY-MM` to analyse another month. Exit codes: `0` means validation passed, possibly with warnings; `2` means validation failed; `1` means the workbook could not be read.

### Run it as a Skill in Claude Code

After running `link-skills`, start Claude Code in the repo and ask for a monthly trend analysis, or invoke `/monthly-trend-analysis`. The Skill runs the pipeline and stops if validation fails. Otherwise it writes owner-facing commentary to `out/.../commentary.md` using only figures from the pipeline output.

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
- **New companies.** Another company needs a JSON file in `config/companies/`, not a code change. See [company-config.md](skills/monthly-trend-analysis/references/company-config.md).
- **Output format.** See [output-schema.md](skills/monthly-trend-analysis/references/output-schema.md).

## How the QuickBooks integration works

Claude Code starts Intuit's open-source MCP server (`vendor/quickbooks-online-mcp-server`, pinned commit) over stdio, as configured in `.mcp.json`:

- Credentials live in `%USERPROFILE%\.secrets\qbo\sandbox.env`, outside the repo.
- Create, update and delete tools are disabled. The 71 read tools include P&L, balance sheet, trial balance and general ledger.
- It connects to the sandbox only.

Setup, OAuth, token rotation, reconnection and team hosting are covered in [integrations/quickbooks/README.md](integrations/quickbooks/README.md) and [docs/integration-decision.md](docs/integration-decision.md). Credential and data rules are in [docs/security.md](docs/security.md).

The pipeline currently reads the Excel workbook only. Running the same skill against data from QuickBooks (the assignment's stretch goal) is not implemented.

## Skill discovery note

Claude Code discovers project skills under `.claude/skills/<name>/`. The single source copy lives in `skills/`. `scripts/link-skills.*` creates a link to it: a directory junction on Windows, a symlink on macOS/Linux. The link is gitignored because git would otherwise commit the files a second time through the junction.
