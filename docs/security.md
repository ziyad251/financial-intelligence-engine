# Security and data handling

This page covers how credentials and financial data are handled in this project. It also lists, plainly, what is **not** yet protected.

## Credentials

| Control | Where | Verified |
|---|---|---|
| QuickBooks client ID, secret, refresh token and realm ID are kept outside the repo in `~/.secrets/qbo/sandbox.env` (Windows: `%USERPROFILE%\.secrets\qbo\sandbox.env`) | The launcher (`integrations/quickbooks/launch.mjs`) defaults to that path. It refuses a token store inside the repository, a relative path or an unexpanded variable | `.mcp.json` holds no secrets and no path. Launcher refusals are tested in `integrations/quickbooks/test/` |
| Claude Code cannot read or edit the secrets directory or `.env` files | `.claude/settings.json` deny rules: `Read(~/.secrets/**)`, `Edit(~/.secrets/**)`, `Read(**/.env)`, `Read(**/*.env)` | Present. The 2026-10-11 audit kept to these rules and did not open the token store |
| Backstop ignore patterns for credential files: `.env*`, `*.pem`, `*.key`, `tokens.json`, `secrets/` | `.gitignore` | Present |
| The vendored server, with its own `node_modules` and any `.env` inside it, is never committed | `vendor/` in `.gitignore` | Present |
| Rotated refresh tokens are written atomically with mode `0600` | Upstream server (`saveTokensToEnv`) | Code reviewed. On Windows the protection comes from the NTFS ACL on the user profile folder; `0600` has little effect there |
| A sandbox company only, never a live client | `QUICKBOOKS_ENVIRONMENT=sandbox` in `.mcp.json`; the launcher refuses to start if the environment or token store sets anything else | Tested (production is refused before the server loads) |
| No credentials or OAuth codes in logs | The launcher sends every console line to stderr, redacting OAuth codes, `state`, realm IDs, tokens, client secrets and `Authorization` headers | Tested with representative fake secrets. Pattern-based: a secret in an unexpected format could still pass |

**Rules:** never paste credentials into chat, issues or commits; never copy a token store to another person or machine; to revoke a grant, disconnect the app in the Intuit portal and rotate the client secret.

## QuickBooks tools are read-only; the OAuth grant is not

Create, update and delete tools are not registered at all, rather than registered and refused. Two independent layers do this: the server's `QUICKBOOKS_DISABLE_WRITE/UPDATE/DELETE=true` flags, and the launcher's registration guard. The guard accepts only `get_`/`search_`/`read_` tools even when the flags are missing or overridden, and refuses to start if it cannot be installed. Tested end to end over stdio, and observed live: 71 read tools, 0 write.

The **OAuth authorization itself is not read-only**. Intuit's Accounting scope permits writes, so the refresh token in the token store is a write-capable credential for the sandbox company, usable directly against the QuickBooks API by anything that can read the file. No read-only QuickBooks scope has been identified or verified. See [integrations/quickbooks/README.md](../integrations/quickbooks/README.md#permissions-what-is-and-is-not-read-only).

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
3. **The token store is a write-capable credential.** The server loads it with `override: true`, so it wins over `.mcp.json`. The launcher now neutralises the two settings that matter: write tools stay blocked whatever the flags say, and a non-sandbox environment stops the launch. But the refresh token itself can write to the sandbox through the API (see above). Protect the file as a credential.
4. **Supply chain.** The upstream server is trusted at the pinned commit. `npm audit --omit=dev` found 8 production findings. A committed patch (`integrations/quickbooks/patches/dependency-security-updates.patch`) updates five packages within their major versions, and the upstream build and its 639 tests pass. **3 moderate findings remain, with no fix available** (`intuit-oauth` → `query-string` → `decode-uri-component`; reachable only through the loopback OAuth listener during `--auth`). Development-only findings remain and are not loaded at runtime.
5. **Server logging.** The upstream server printed the OAuth callback URL, including the authorization code, and full token-exchange error objects; it printed them to stdout, the protocol channel. The launcher now redacts every console line and sends it to stderr (tested). Limits: redaction is pattern-based, and the server's own `npm run auth` bypasses the launcher, so the README says to use `launch.mjs --auth` instead.
6. **Interactive OAuth.** Upstream starts a browser OAuth flow, with a listener on all network interfaces, whenever a tool call finds no valid refresh token. The launcher refuses that inside the MCP server (tested). In `--auth` mode it binds the listener to loopback only (unit-tested). A full consent flow through the launcher has not been run end to end, because that would replace the working authorization.
