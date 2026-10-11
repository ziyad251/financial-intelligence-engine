# Security and data handling

This page covers how credentials and financial data are handled in this project. It also lists, plainly, what is **not** yet protected.

## Credentials

| Control | Where | Verified |
|---|---|---|
| QuickBooks client ID, secret, refresh token and realm ID are kept outside the repo in `%USERPROFILE%\.secrets\qbo\sandbox.env` | `.mcp.json` points to the file through `${USERPROFILE}`; no secret values are in the repo | `.mcp.json` contains only the path, the environment name and the read-only flags (checked with values masked) |
| Claude Code cannot read or edit the secrets directory or `.env` files | `.claude/settings.json` deny rules: `Read(~/.secrets/**)`, `Edit(~/.secrets/**)`, `Read(**/.env)`, `Read(**/*.env)` | Present. The 2026-10-11 audit kept to these rules and did not open the token store |
| Backstop ignore patterns for credential files: `.env*`, `*.pem`, `*.key`, `tokens.json`, `secrets/` | `.gitignore` | Present |
| The vendored server, with its own `node_modules` and any `.env` inside it, is never committed | `vendor/` in `.gitignore` | Present |
| Rotated refresh tokens are written atomically with mode `0600` | Upstream server (`saveTokensToEnv`) | Code reviewed. On Windows the protection comes from the NTFS ACL on the user profile folder; `0600` has little effect there |
| A sandbox company only, never a live client | `QUICKBOOKS_ENVIRONMENT=sandbox` | Present in `.mcp.json` |

**Rules:** never paste credentials into chat, issues or commits; never copy a token store to another person or machine; to revoke a grant, disconnect the app in the Intuit portal and rotate the client secret.

## QuickBooks access is read-only

Create, update and delete tools are not registered at all, rather than registered and refused. This is controlled by `QUICKBOOKS_DISABLE_WRITE/UPDATE/DELETE=true`. The 2026-10-11 verification, which listed tools only and called none, is in [integrations/quickbooks/README.md](../integrations/quickbooks/README.md#verifying-read-only-mode).

Nothing in this project uploads workbook data to QuickBooks. The assignment's stretch goal of loading the workbook into the sandbox has deliberately not been done; it would require turning on write tools against the sandbox only.

## Financial data

| Data | Handling |
|---|---|
| Source workbook (`data/`) | Gitignored, and never committed. Read-only input: the pipeline opens it with `openpyxl` and never writes to it. It records the workbook's SHA-256 hash in every result, so outputs can be traced to the exact file |
| Analysis outputs (`out/`) | Gitignored, because they contain client figures |
| Python pipeline | Makes no network calls; it only reads files and writes to `out/` |
| Claude | Reads `brief.md` and, where needed, `analysis.json`. These are sent to Anthropic's API as part of the conversation, like any context Claude reads. For real clients this must fall under the firm's agreement with Anthropic and the client engagement terms |
| `get_invoice_pdf` tool | Writes PDFs to the local disk. Point `QBO_PDF_OUTPUT_DIR` outside the repo if it is used |

## Known gaps and risks

1. **Workbooks are excluded only by `.gitignore`.** `data/` is ignored, but nothing stops a forced add (`git add -f`) or a workbook saved elsewhere in the repo. A pre-commit check for `*.xlsx` files would close this gap. The integration test that uses the ABC workbook is skipped automatically when the file is absent, as in a fresh clone.
2. **The token store is a plaintext file on a laptop.** That is acceptable for a sandbox but not for client production; see the hosted design in [integration-decision.md](integration-decision.md#team-hosting).
3. **The token store also controls the server's environment.** The server loads it with `override: true`, so whatever is in that file wins over `.mcp.json`. Anyone who can edit the token store can therefore change the server's behaviour, including the read-only flags. Protect it as a credential.
4. **Supply chain.** The upstream server and its npm dependencies are trusted at the pinned commit. `npm audit` and a review of upstream changes belong in the update procedure; they were not run in this audit.
5. **Server logging.** Diagnostics go to stderr. The server's logs have not been audited to confirm that no token material is ever printed.
