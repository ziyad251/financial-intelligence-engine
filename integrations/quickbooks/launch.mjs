#!/usr/bin/env node
// Launch Intuit's QuickBooks MCP server (vendor/, pinned, unmodified build) with this project's hardening.
//
//   node integrations/quickbooks/launch.mjs          MCP server over stdio (used by .mcp.json)
//   node integrations/quickbooks/launch.mjs --auth   one-time interactive sandbox OAuth, from a terminal
//
// See hardening.mjs for what is enforced and integrations/quickbooks/README.md for setup.
// QBO_MCP_SERVER_DIR overrides the server location (tests only).

import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  checkNodeVersion, guardToolRegistration, installConsoleRedaction, installLoopbackOAuthListener, preflight,
} from "./hardening.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");
const serverDir = process.env.QBO_MCP_SERVER_DIR || path.join(repoRoot, "vendor", "quickbooks-online-mcp-server");
const interactiveAuth = process.argv.includes("--auth");
const load = (rel) => import(pathToFileURL(path.join(serverDir, "dist", rel)).href);

installConsoleRedaction();
try {
  checkNodeVersion();
  const { parse } = createRequire(path.join(serverDir, "package.json"))("dotenv");
  const { warnings } = preflight({ env: process.env, repoRoot, parse });
  warnings.forEach((w) => console.error(w));
  installLoopbackOAuthListener({ http, allowInteractive: interactiveAuth });

  if (interactiveAuth) {
    await load("auth-server.js");
  } else {
    // The server registers every tool on this one McpServer instance; guard it before index.js registers them.
    const { QuickbooksMCPServer } = await load("server/qbo-mcp-server.js");
    const blocked = guardToolRegistration(QuickbooksMCPServer.GetServer());
    await load("index.js");
    if (blocked.length) console.error(`[qbo-launcher] read-only guard blocked ${blocked.length} non-read tool(s)`);
  }
} catch (error) {
  console.error(`[qbo-launcher] refusing to start: ${error.message}`);
  process.exit(1);
}
