# QuickBooks Online integration (MCP)

Claude Code connects to a **QuickBooks Online sandbox** through Intuit's open-source MCP server, [`intuit/quickbooks-online-mcp-server`](https://github.com/intuit/quickbooks-online-mcp-server). The server is cloned into `vendor/`, built locally at a pinned commit, and started over stdio by this project's **hardened launcher**, `integrations/quickbooks/launch.mjs`. The launcher adds the protections listed below without modifying Intuit's code. For why this option was chosen, see [docs/integration-decision.md](../../docs/integration-decision.md). For the credential and data rules, see [docs/security.md](../../docs/security.md).

| Item | Value |
|---|---|
| Server source | `vendor/quickbooks-online-mcp-server` (gitignored), pinned at commit `31a1dd5`. Source code unmodified; dependency versions updated by [`patches/dependency-security-updates.patch`](patches/dependency-security-updates.patch) |
| Launcher | `integrations/quickbooks/launch.mjs` + `hardening.mjs` (committed, no dependencies of their own) |
| Node.js | 20 or later (`engines` in `package.json`); tested with **24.12.0** (`.nvmrc`) on Windows 11 |
| Transport | stdio. The server has no HTTP transport |
| Config | `.mcp.json` (committed; contains **no secrets** and no machine-specific paths) |
| Credentials | `~/.secrets/qbo/sandbox.env` (Windows: `%USERPROFILE%\.secrets\qbo\sandbox.env`), outside the repo |
| Environment | `sandbox` only. The launcher refuses any other |
| MCP tools exposed | 71 read tools (`get_`, `search_`, `read_`). Create, update and delete tools are never registered |
| OAuth permission | **Not read-only.** The grant uses Intuit's Accounting scope, which permits writes. See [Permissions](#permissions-what-is-and-is-not-read-only) |

## What the launcher enforces

Each item is covered by the tests in `test/` (`npm test`, see [Verifying](#verifying)).

| Protection | How | If it cannot be enforced |
|---|---|---|
| Read-only tool surface | Before the server registers its tools, the launcher wraps registration on the server's single `McpServer` instance. It accepts only names matching `^(get\|search\|read)[_-]`. This holds even if the `QUICKBOOKS_DISABLE_*` flags are missing, or are switched off by the token store (which the server loads over its environment). | The launcher refuses to start (exit 1) if registration cannot be wrapped |
| Sandbox only | Before the server loads, the token store's and the environment's `QUICKBOOKS_ENVIRONMENT` are checked (by key, never printed). | Anything other than `sandbox` stops the launch |
| Credentials outside the repo | The token store must be an absolute path outside the repository. Unexpanded variables such as `${USERPROFILE}` are rejected. | The launch stops |
| No secrets in logs | Every console line goes to **stderr**, redacted. Stdout is kept for the MCP protocol: the upstream server's `console.log` would otherwise corrupt it. Redacted: OAuth `code`, `state`, `realmId` and `client_id` in URLs and form bodies; access, refresh and ID tokens; client secrets; `Authorization` headers; realm IDs; Intuit-style tokens and JWTs. HTTP status codes and error codes are kept. | Not applicable (installed before anything else runs) |
| No surprise OAuth | Inside the MCP server, the interactive OAuth flow is refused before any listener is bound or browser opened. A missing, expired or revoked authorization makes the tool call fail with instructions to run `--auth`. | Not applicable |
| Loopback-only callback | In `--auth` mode the callback listener binds `127.0.0.1` and `::1` (one handler), instead of upstream's all-interfaces `::`. Other hosts are refused. The server's own `state` (CSRF) check is unchanged. | If IPv6 loopback is unavailable, IPv4 loopback is used alone |

Fixed at the source by updating dependencies: see [Dependencies](#dependencies-and-known-vulnerabilities).

## Setup (Windows, as tested)

### 1. Intuit developer app

1. Create a free account at developer.intuit.com. A QuickBooks Online sandbox company is created automatically.
2. Create an app with the **Accounting** scope.
3. Open **Development → Keys & credentials**, copy the **sandbox** Client ID and Client Secret, and add the redirect URI `http://localhost:8000/callback`.

### 2. Build the server

Use Node.js 24.12.0 (`.nvmrc`; any 20+ should work). From the repository root, in PowerShell:

```powershell
git clone https://github.com/intuit/quickbooks-online-mcp-server vendor/quickbooks-online-mcp-server
cd vendor/quickbooks-online-mcp-server
git checkout 31a1dd5
git apply ../../integrations/quickbooks/patches/dependency-security-updates.patch
npm ci
npm run build
cd ../..
```

The patch changes only `package.json` and `package-lock.json` (see [Dependencies](#dependencies-and-known-vulnerabilities)), so `npm ci` installs the patched versions. Pinning the commit keeps the tool surface fixed. Before moving the pin, review the upstream changelog, re-create the patch and run the tests.

### 3. Token store (credentials)

Create `%USERPROFILE%\.secrets\qbo\sandbox.env`. It must be outside the repository and readable only by your user, which is the default ACL on your profile folder:

```dotenv
QUICKBOOKS_CLIENT_ID=<sandbox client id>
QUICKBOOKS_CLIENT_SECRET=<sandbox client secret>
# Written by the server after OAuth. Do not edit by hand:
# QUICKBOOKS_REFRESH_TOKEN=...
# QUICKBOOKS_REALM_ID=...
```

To use another location, set `QUICKBOOKS_TOKEN_STORE_PATH` to an absolute path in the environment Claude Code starts with. The launcher checks the path is outside the repository.

Do not put `QUICKBOOKS_DISABLE_*=false` or `QUICKBOOKS_ENVIRONMENT` in this file. The server loads it over its environment (`dotenv`, `override: true`). Write tools stay blocked by the launcher regardless, and a non-sandbox environment stops the launch.

### 4. Authorize once (interactive OAuth)

From the repository root, in a terminal (not inside Claude Code):

```powershell
node integrations/quickbooks/launch.mjs --auth
```

This runs the server's own `auth-server` under the launcher's protections. It starts the callback listener on loopback port 8000, opens the Intuit consent page, and writes `QUICKBOOKS_REFRESH_TOKEN` and `QUICKBOOKS_REALM_ID` to the token store. Choose the **sandbox** company. Do **not** use the server's `npm run auth`: it bypasses the launcher, binds all interfaces and logs the callback URL unredacted.

**Not yet exercised end to end:** a full `--auth` consent flow through the launcher has not been run, because it would replace the working authorization. The loopback binding, the refusal outside `--auth` and the redaction are unit-tested. The `state` check and code exchange are the server's own, unchanged.

### 5. `.mcp.json` (already committed)

```json
{
  "mcpServers": {
    "quickbooks": {
      "type": "stdio",
      "command": "node",
      "args": ["integrations/quickbooks/launch.mjs"],
      "env": {
        "QUICKBOOKS_ENVIRONMENT": "sandbox",
        "QUICKBOOKS_DISABLE_WRITE": "true",
        "QUICKBOOKS_DISABLE_UPDATE": "true",
        "QUICKBOOKS_DISABLE_DELETE": "true"
      }
    }
  }
}
```

Start Claude Code in the repository root, approve the project MCP server, and check `claude mcp list` shows `quickbooks … ✔ Connected`. The `DISABLE_*` flags are kept as a second layer; the launcher's guard does not depend on them.

### macOS and Linux

Not tested. Intended to work as follows:
- The launcher's default token store is `~/.secrets/qbo/sandbox.env` on every OS, and `.mcp.json` has no OS-specific path.
- Use the same commands with `/` paths. Protect the store with `chmod 700 ~/.secrets/qbo && chmod 600 ~/.secrets/qbo/sandbox.env`; the server writes rotated tokens with mode `0600`.
- `node` must be on the `PATH` Claude Code starts with. With nvm, start Claude Code from a shell where `nvm use` has run, or put an absolute node path in `.mcp.json` locally.

## Permissions: what is and is not read-only

- **MCP tools: read-only, enforced.** Only `get_`, `search_` and `read_` tools are registered, by both the server's `QUICKBOOKS_DISABLE_*` flags and the launcher's guard. Claude cannot call a write tool through this server.
- **OAuth grant: not read-only.** Intuit's Accounting scope covers reading and writing. The refresh token in the token store can therefore be used, by any process that can read the file, to write to the sandbox company directly through the QuickBooks API. This project has not found or verified a read-only QuickBooks OAuth scope. Protect the token store as a write-capable credential.
- `get_invoice_pdf` is a read tool but writes the PDF to local disk (`QBO_PDF_OUTPUT_DIR`). It does not write to QuickBooks.

## OAuth and tokens

| Token | Lifetime | Where it lives |
|---|---|---|
| Access token | About 1 hour | Server memory only. Refreshed automatically 5 minutes before expiry (no browser) |
| Refresh token | Rotates on refresh; expires under Intuit's validity rules if unused | Token store file |

- Intuit **rotates** the refresh token, and the previous one stops working. The server writes the new token immediately, via a temporary file and a rename, with file mode `0600`. On Windows the profile folder's ACL is what protects it.
- Before treating a rejected refresh as final, the server re-reads the token store. This handles two processes sharing one store (for example Claude Desktop and Claude Code).
- Never copy the token store between machines or people. Two copies of a refresh token invalidate each other on the first rotation.

| Situation | Action |
|---|---|
| A tool call fails with "Interactive QuickBooks OAuth is disabled …" | The stored authorization is missing, expired or revoked. Run `node integrations/quickbooks/launch.mjs --auth`, then reconnect with `/mcp` |
| Server shows as disconnected | Run `/mcp` and reconnect `quickbooks`, or restart Claude Code. Check `claude mcp list`; the reason is on the server's stderr (`[qbo-launcher] refusing to start: …`) |
| Sandbox company reset, or `invalid_grant` | Remove the `QUICKBOOKS_REFRESH_TOKEN` and `QUICKBOOKS_REALM_ID` lines from the token store, then run `--auth` |
| Client secret rotated in the Intuit portal | Update `QUICKBOOKS_CLIENT_SECRET` in the token store, then reconnect |

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `refusing to start: token store not found at …` | Create the token store (step 3), or set `QUICKBOOKS_TOKEN_STORE_PATH` |
| `refusing to start: … unexpanded variable` | A `${…}` or `%…%` path reached the launcher unexpanded. Unset it to use the default, or give an absolute path |
| `refusing to start: QUICKBOOKS_ENVIRONMENT is 'production'` | Remove `QUICKBOOKS_ENVIRONMENT` from the token store. This project is sandbox only |
| `refusing to start: cannot enforce read-only tools` | The server build changed how tools are registered (for example after moving the pin). Do not bypass the launcher: update `hardening.mjs` and its tests |
| `Client ID, Client Secret and Redirect URI must be set` | The token store lacks the client keys |
| OAuth page shows a redirect URI error | `http://localhost:8000/callback` is not registered on the Intuit app (sandbox keys section) |
| `--auth`: callback never arrives | Port 8000 in use, or a firewall blocking loopback |
| HTTP 401 from the token endpoint | `invalid_client`: wrong client ID or secret, or production keys used against sandbox |

## Verifying

```powershell
cd integrations/quickbooks
npm test                      # node --test: 20 tests, no network, fake credentials only
cd ../../vendor/quickbooks-online-mcp-server
npm run build; npm test       # upstream build and test suite
npm audit --omit=dev          # production dependency audit
```

`npm test` in `integrations/quickbooks` covers:
- the redaction of representative callback URLs, token-exchange errors, JSON and `.env` lines, and the stdout/stderr split;
- the tool guard, including failing closed;
- loopback binding and the refusal outside `--auth`;
- preflight refusals;
- end to end over stdio against the real server build with a fake token store: exactly 71 read tools even when the token store turns the `DISABLE_*` flags off, no OAuth listener when authorization is missing, production refused.

Results on 2026-10-12, in this repository:

| Check | Result |
|---|---|
| `npm test` (launcher) | 20 passed, 0 failed |
| Upstream `npm run build` / `npm test` after the dependency patch | build OK; 35 suites, 639 tests passed |
| Live sandbox, through the launcher with the existing authorization | `tools/list`: 71 tools, 0 write; `get_company_info` returned the sandbox company; stdout contained only JSON-RPC; no OAuth flow started |
| `claude mcp list` | `quickbooks: node integrations/quickbooks/launch.mjs - ✔ Connected` |

## Dependencies and known vulnerabilities

`npm audit --omit=dev` at the pinned commit reported 8 production findings. The patch updates five packages within their existing major versions:

| Package | From → to | Severity | Used at runtime here? |
|---|---|---|---|
| `axios` (via `intuit-oauth`, `node-quickbooks`) | 1.19.0 → 1.20.0 | high | Yes: all Intuit API and token calls |
| `@modelcontextprotocol/sdk` (direct) | 1.30.0 → 1.31.0 (lowest patched) | high | Yes. The advisory concerns its OAuth client, which this stdio server does not use |
| `proxy-addr` (via SDK → `express`) | 2.0.7 → 2.0.8 | critical | No: `express` serves only the SDK's HTTP transports |
| `ip-address` (via SDK → `express-rate-limit`) | 10.4.0 → 10.7.4 | moderate | No (HTTP transports only) |
| `fast-uri` (via SDK → `ajv`) | 3.1.7 → 3.1.8 | moderate | Yes (schema validation) |

After the patch, `npm audit --omit=dev` reports **3 moderate findings, no fix available**: `query-string` and `decode-uri-component` (a denial of service from crafted URLs), used by `intuit-oauth` to parse the OAuth callback URL. Exposure is limited to the loopback listener during `--auth`. Development-only findings (jest, eslint and their dependencies) remain and were not changed; they are not loaded at runtime. `npm audit fix` was deliberately not run.

## Team setup

The current setup is per developer: each person has their own Intuit sandbox app, token store and OAuth grant. Onboarding a teammate for local development:

1. Build the server (step 2), including the patch.
2. Create their own token store with their own sandbox keys (step 3). Never share yours.
3. Authorize once with `--auth` (step 4).
4. Start Claude Code in the repository and approve the project MCP server.

A shared, multi-client deployment is a **proposal, not implemented or tested**: see [docs/integration-decision.md](../../docs/integration-decision.md#team-hosting).
