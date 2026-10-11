# QuickBooks Online integration (MCP)

Claude Code connects to a **QuickBooks Online sandbox** through Intuit's open-source MCP server, [`intuit/quickbooks-online-mcp-server`](https://github.com/intuit/quickbooks-online-mcp-server). The server is cloned into `vendor/`, built locally and run as a stdio subprocess of Claude Code. It is configured **read-only**. For why this option was chosen, see [docs/integration-decision.md](../../docs/integration-decision.md). For the credential and data rules, see [docs/security.md](../../docs/security.md).

| Item | Value |
|---|---|
| Server source | `vendor/quickbooks-online-mcp-server` (gitignored), pinned at commit `31a1dd5`, no local modifications |
| Entry point | `vendor/quickbooks-online-mcp-server/dist/index.js` (Node; verified with v24.12.0) |
| Transport | stdio. The server has no HTTP transport |
| Config | `.mcp.json` (committed; contains **no secrets**) |
| Credentials | `%USERPROFILE%\.secrets\qbo\sandbox.env`, outside the repo |
| Environment | `sandbox` |
| Write/update/delete tools | Disabled (`QUICKBOOKS_DISABLE_WRITE/UPDATE/DELETE=true`) |

## Setup

### 1. Intuit developer app

1. Create a free account at developer.intuit.com. A QuickBooks Online sandbox company is created automatically.
2. Create an app with the **Accounting** scope.
3. Open **Development → Keys & credentials**, copy the **sandbox** Client ID and Client Secret, and add the redirect URI `http://localhost:8000/callback`.

### 2. Build the server

```powershell
git clone https://github.com/intuit/quickbooks-online-mcp-server vendor/quickbooks-online-mcp-server
cd vendor/quickbooks-online-mcp-server
git checkout 31a1dd5
npm ci
npm run build
```

Pinning the commit keeps the tool surface and the read-only gating behaviour fixed. Review the upstream changelog before moving the pin.

### 3. Token store (credentials)

Create `%USERPROFILE%\.secrets\qbo\sandbox.env`. It must be outside the repository and readable only by your Windows user (the default ACL on your profile folder):

```dotenv
QUICKBOOKS_CLIENT_ID=<sandbox client id>
QUICKBOOKS_CLIENT_SECRET=<sandbox client secret>
# Written by the server after OAuth. Do not edit by hand:
# QUICKBOOKS_REFRESH_TOKEN=...
# QUICKBOOKS_REALM_ID=...
```

Defence in depth: you may also put `QUICKBOOKS_DISABLE_WRITE=true`, `QUICKBOOKS_DISABLE_UPDATE=true` and `QUICKBOOKS_DISABLE_DELETE=true` here. The server loads this file over its process environment (`dotenv`, `override: true`), so flags set here still apply if someone removes them from `.mcp.json`.

### 4. `.mcp.json`

```json
{
  "mcpServers": {
    "quickbooks": {
      "type": "stdio",
      "command": "node",
      "args": ["vendor/quickbooks-online-mcp-server/dist/index.js"],
      "env": {
        "QUICKBOOKS_TOKEN_STORE_PATH": "${USERPROFILE}\\.secrets\\qbo\\sandbox.env",
        "QUICKBOOKS_ENVIRONMENT": "sandbox",
        "QUICKBOOKS_DISABLE_WRITE": "true",
        "QUICKBOOKS_DISABLE_UPDATE": "true",
        "QUICKBOOKS_DISABLE_DELETE": "true"
      }
    }
  }
}
```

Claude Code expands `${USERPROFILE}`. The server requires an **absolute** token-store path and refuses to start otherwise.

## OAuth

- **First connection:** if the token store has no refresh token, the first QuickBooks tool call starts a callback listener on `localhost:8000` and opens the Intuit consent page in your browser. Sign in, choose the sandbox company, and approve. The server then writes `QUICKBOOKS_REFRESH_TOKEN` and `QUICKBOOKS_REALM_ID` to the token store.
- **Standalone alternative:** run `npm run auth` inside the vendor directory, with `QUICKBOOKS_TOKEN_STORE_PATH` set in the shell to the same absolute path. Use this when you don't want the browser to open mid-session.
- **Production companies:** the localhost redirect only works for sandbox. Intuit rejects localhost redirect URIs for production apps, so the server refuses the interactive flow in production and asks for re-authorisation through a public HTTPS redirect. This project does not connect to production.

## Token persistence

| Token | Lifetime | Where it lives |
|---|---|---|
| Access token | About 1 hour | Memory only. Refreshed automatically 5 minutes before expiry |
| Refresh token | Rotates on refresh; expires under Intuit's validity rules if not used (see Intuit's OAuth documentation for current limits) | Token store file |

- Intuit **rotates** the refresh token, and the previous one stops working. The server writes the new token immediately, via a temporary file and a rename, with file mode `0600`.
- Before refreshing, the server re-reads the token store. This handles two processes sharing one store (for example Claude Desktop and Claude Code at the same time): a process will not use a token that a sibling has already rotated.
- Never copy the token store between machines or people. Two independent copies of a refresh token will invalidate each other on the first rotation.

## Reconnection

| Situation | Action |
|---|---|
| Server shows as disconnected in Claude Code | Run `/mcp` and reconnect the `quickbooks` server, or restart Claude Code. Run `claude mcp list` to check its status |
| Refresh token revoked or expired (`invalid_grant`), or the sandbox company was reset | Remove the `QUICKBOOKS_REFRESH_TOKEN` and `QUICKBOOKS_REALM_ID` lines from the token store, then run the OAuth flow again |
| Client secret rotated in the Intuit portal | Update `QUICKBOOKS_CLIENT_SECRET` in the token store, then reconnect |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `QUICKBOOKS_TOKEN_STORE_PATH must be an absolute path, got "${USERPROFILE}..."` | The server was started outside Claude Code, which would normally expand `${USERPROFILE}`. Pass an expanded absolute path |
| `Client ID, Client Secret and Redirect URI must be set` | The token store is missing, or lacks the client keys, at the configured path |
| OAuth page shows a redirect URI error | `http://localhost:8000/callback` is not registered on the Intuit app (in the sandbox keys section) |
| Callback never arrives | Port 8000 is in use by another process, or a firewall is blocking localhost |
| HTTP 401 from the token endpoint | `invalid_client`: wrong client ID or secret, or production keys used against sandbox |
| No `create_*`, `update_*` or `delete_*` tools | Expected: read-only configuration |

## Verifying read-only mode

The server decides each tool's category from its name prefix (`create`, `update` or `delete`, with `_` or `-`). It skips registering a tool when the matching `QUICKBOOKS_DISABLE_*` flag is `"true"`; all other tools are read tools (`src/helpers/register-tool.ts`). All 71 write-capable tools at the pinned commit use one of those prefixes.

You can check this without calling any tool by sending only `initialize` and `tools/list` over stdio. This audit's results (2026-10-11):

| Run | Tools listed | create/update/delete |
|---|---|---|
| Configured server (real token store) | 71 | 0 |
| Dummy token store, flags as in `.mcp.json` | 71 | 0 |
| Dummy token store, flags removed | 142 | 71 |

The QuickBooks tools exposed in a live Claude Code session were also all `get`/`search`/`read` tools (71).

**Caveat:** `get_invoice_pdf` is a read tool, but it writes the PDF to the local disk (`QBO_PDF_OUTPUT_DIR`). It does not write to QuickBooks.

## Team setup

The current setup is per developer: each person has their own Intuit sandbox app, token store and OAuth grant. For a shared, multi-client deployment (a hosted server, per-client credential isolation, SSO), see [docs/integration-decision.md](../../docs/integration-decision.md#team-hosting).

Onboarding a teammate for local development:

1. Build the server (step 2).
2. Create their own token store with their own sandbox keys (step 3). Never share yours.
3. Pull the repo; `.mcp.json` is already committed.
4. Start Claude Code in the repo, approve the project MCP server, and complete OAuth.
