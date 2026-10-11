# Integration decision: QuickBooks Online ↔ Claude

**Decision:** use Intuit's open-source MCP server (`intuit/quickbooks-online-mcp-server`), pinned to a known commit, built locally, run over stdio from Claude Code, connected to a sandbox company and restricted to read-only tools.

**Status:** implemented and working against the sandbox. Company-information and Profit & Loss queries succeeded, and the read-only tool surface was verified. The details are in [integrations/quickbooks/README.md](../integrations/quickbooks/README.md).

## Options considered

| Option | Verdict | Reasoning |
|---|---|---|
| **A. Hosted or "native" connector** (a connector directory entry or a third-party hosted QuickBooks MCP) | Rejected for this build | Credentials and client ledger data would pass through, or be held by, a third party we have not assessed. We would have less control over which tools are exposed, and no guarantee of a read-only mode or of sandbox support. Pinning versions and keeping evaluations reproducible would also be harder. |
| **B. Intuit's open-source MCP server** | **Chosen** | Maintained by the API owner. It has 71 read tools, including the reports this work needs: P&L, balance sheet, trial balance, general ledger, cash flow and aging. It has a supported environment switch that removes create/update/delete tools at registration (verified). Its token handling is careful: rotation is persisted atomically, it re-reads the store before refreshing so multiple processes don't conflict, and it accepts a configurable token-store path. The code can be read and pinned, and credentials go only to Intuit. |
| **C. Custom MCP on the QuickBooks API** | Deferred | It would allow a tight, report-shaped tool set, but it would mean re-implementing OAuth, token rotation and concurrency handling, which option B already gets right (that is where the subtle bugs live). It remains the fallback if we need tools B cannot provide. The likely route is a thin custom layer over B's client, not a rewrite. |

### Accepted trade-offs of option B

- **Large tool surface.** About 71 read tools is more than a trend analysis needs, which uses more of the model's context and gives more ways to read data. Mitigations: read-only mode, and sandbox data only.
- **stdio only.** The server cannot be hosted for a team as it stands; see below.
- **Version drift.** Upstream changes could alter tools or gating. Mitigation: the commit is pinned (`31a1dd5`), and any update to the pin should repeat the `tools/list` check in the integration README.

## Team hosting

A laptop stdio setup is right for one developer and a sandbox. It is wrong for a team serving real clients:

- every person repeats OAuth for every client;
- Intuit rotates refresh tokens, so one grant cannot be shared safely across several machines;
- client credentials end up on laptops; and
- there is no central audit of who read which client's books.

**Proposed design (not yet built):**

1. **One server instance per client company (QuickBooks realm),** run as a container in the firm's own cloud account, in a region chosen for the client's data-residency needs (US or India). The instance is the isolation boundary: it can only ever hold one client's token.
2. **Credentials live in a secrets manager.** Each instance's token store is a per-client secret mounted on an encrypted, writable volume at `QUICKBOOKS_TOKEN_STORE_PATH`; the server already supports this for read-only containers. Rotated refresh tokens are written back to that one location only.
3. **Remote MCP endpoint.** Because the server is stdio-only, each instance runs behind an MCP gateway that exposes Streamable HTTP, or we contribute an HTTP transport upstream. Claude clients (Claude Code, or organisation connectors in Claude) connect to it as a remote MCP server.
4. **Authentication and authorisation at the gateway.** Staff sign in with the firm's SSO, and access is granted per client, so staff only see the clients they work on. The firm's Intuit OAuth app stays server-side; no staff member ever handles a client refresh token.
5. **Read-only by default.** The `QUICKBOOKS_DISABLE_*` flags are on in every instance. If we ever need write access (for example, posting journal entries), it runs as a separate, explicitly approved instance with human confirmation.
6. **Onboarding and operations.** Production OAuth needs a public HTTPS redirect, so a small admin onboarding flow performs the consent once per client and stores the token. Gateway logs record user, client, tool and time, never payloads. Instance egress is limited to Intuit's API and OAuth endpoints.

**Why per-client instances rather than one multi-tenant server:** the upstream server holds one realm per process. Isolating clients at the process and secret level is simpler to reason about and audit than tenant routing inside a single process, and the cost of small idle containers is low.
