# Architecture

**Built** means implemented and covered by tests or a recorded live check. **Proposed** means designed only: not built, not tested.

## Structure (built)

```
data/<workbook>.xlsx + config/companies/<co>.json
        │
        ▼
src/financial_analysis   selection → loader → calculations → trends / validation / highlights → output
        │                (deterministic Python, no network; the only place numbers are made)
        ▼
out/<workbook>/<YYYY-MM>/   analysis.json, brief.md, last_run.json   (or *.failed.* on a failed check)
        │
        ▼
Skill .claude/skills/monthly-trend-analysis ── writes ─► commentary.md ── checked by financial_analysis.trace

Claude Code ── stdio ── integrations/quickbooks/launch.mjs ── Intuit MCP server (vendor/, pinned) ── QBO sandbox
```

| Component | Responsibility |
|---|---|
| `src/financial_analysis/` | Finds months, sections and totals by structure (no account names in code); sums leaf accounts only; MoM, YoY, TTM, YTD; reconciles every subtotal and derived line; writes outputs with freshness rules |
| `config/companies/*.json` | Per-company labels, account classes, expense categories and thresholds: a new company needs a file, not code. Validated on load |
| Skill (`SKILL.md`, `scripts/`, `references/`) | Runs the pipeline; stops on a failed validation; Claude writes commentary from `brief.md` only, then the trace check rejects untraceable or contradicted figures |
| `evals/golden_cases/`, `tests/` | Hand-calculated golden cases through the real CLI; unit tests (`python -m pytest`) |
| `integrations/quickbooks/` | Launcher around Intuit's unmodified server: read-only tools (fails closed), sandbox only, token store outside the repo, redacted logs, loopback-only OAuth |
| Memory | None relied on. State lives in files: config (judgments), `out/` (results), `last_run.json` (provenance). Nothing client-specific goes in Claude memory or `CLAUDE.md` |

## Credentials and data (built, single developer)

- QuickBooks token store: `~/.secrets/qbo/sandbox.env`, outside the repo, denied to Claude by `.claude/settings.json`. The OAuth grant is **write-capable**; only the tool surface is read-only.
- Workbooks (`data/`) and outputs (`out/`) are gitignored. The pipeline makes no network calls; `brief.md` and parts of `analysis.json` reach Anthropic as conversation context.
- A run refuses a workbook outside `data/`, guessing between several, or a config whose `company_name` does not match the workbook title.

**Not enforced today:** separation between clients. All workbooks and outputs share one tree, with no client key or access control.

## Ten clients (proposed)

| Concern | Today | Proposed |
|---|---|---|
| Data and outputs | One `data/` and `out/` tree | `clients/<client>/{data,out}` or a per-client storage prefix, each with its own access policy |
| Configs | `config/companies/` in git | One per client; changes reviewed by accounting |
| QuickBooks | One stdio server, one realm, one developer | One server instance per client realm in the firm's cloud; token in a secrets manager; MCP gateway with SSO and per-client authorisation; read-only by default |
| Claude context | One local project | Per-client Claude projects or workspaces, under the firm's Anthropic agreement |
| Logs | Redacted launcher stderr | Gateway log of user, client, tool and time; no payloads |

**Recommendation:** per-client workspaces sharing one versioned engine. Isolation comes from separate paths, permissions and processes rather than tenant filters in code. Revisit a multi-tenant service only at much larger scale. Details: [integration-decision.md](integration-decision.md#team-hosting).

**What breaks first:** OAuth. One stdio server holds one realm and Intuit rotates refresh tokens, so a grant cannot be shared between laptops. Next: choosing the wrong workbook or config (mitigated only by the company-name check), then reviewer throughput. Classifications are accounting judgments needing sign-off per client; the commentary check is a guard, not proof, so the reviewer remains the final control.
