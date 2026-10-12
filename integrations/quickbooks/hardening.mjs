// Runtime hardening for Intuit's QuickBooks MCP server (vendor/, pinned and unmodified).
//
// launch.mjs installs these before it loads the server, so the protections live in this
// repository rather than in a patched copy of third-party code:
//
//   redact / installConsoleRedaction  every console line goes to stderr (stdout is the MCP
//                                     protocol channel) with OAuth codes, tokens, secrets and
//                                     realm IDs replaced by [REDACTED]
//   guardToolRegistration             only get_/search_/read_ tools can be registered, whatever
//                                     the QUICKBOOKS_DISABLE_* flags say (fails closed)
//   installLoopbackOAuthListener      the OAuth callback listener binds 127.0.0.1 and ::1 only,
//                                     and is refused outright unless interactive auth was asked for
//   preflight                         sandbox only, token store outside the repository
//
// Nothing here weakens the server's own OAuth state check; it only narrows what can run.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import util from "node:util";

const REDACTED = "[REDACTED]";
const SECRET_KEYS = [
  "access_token", "refresh_token", "id_token", "x_refresh_token", "client_secret", "clientSecret", "accessToken",
  "refreshToken", "idToken", "realmId", "realm_id", "authorization", "Authorization", "password",
];
const QUERY_KEYS = ["code", "state", "realmId", "realm_id", "client_id", "access_token", "refresh_token", "id_token",
  "client_secret", "token"];
const RULES = [
  // URL query strings and form bodies: ?code=...&state=...&realmId=...
  [new RegExp(`([?&\\s]|^)(${QUERY_KEYS.join("|")})=([^&\\s'"]+)`, "g"), `$1$2=${REDACTED}`],
  // key: 'value' / "key":"value" / key=value, as printed by util.inspect, JSON or .env files
  [new RegExp(`(["']?)\\b(${SECRET_KEYS.join("|")})\\1(\\s*[:=]\\s*)(['"]?)[^'"\\s,}&]+\\4`, "g"), `$1$2$1$3$4${REDACTED}$4`],
  [/\b(QUICKBOOKS_(?:CLIENT_ID|CLIENT_SECRET|REFRESH_TOKEN|REALM_ID))=\S+/g, `$1=${REDACTED}`],
  [/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/g, `$1 ${REDACTED}`],
  [/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.?[A-Za-z0-9_-]*/g, REDACTED],  // JWTs (access and ID tokens)
  [/\bAB11[0-9A-Za-z]{20,}/g, REDACTED],                                   // Intuit refresh tokens and auth codes
];

/** Replace credentials, OAuth codes and realm IDs in a log line. Diagnostics such as HTTP status codes stay. */
export function redact(text) {
  let out = String(text);
  for (const [pattern, replacement] of RULES) out = out.replace(pattern, replacement);
  return out;
}

/** Send every console method to stderr, redacted. Returns a function that restores the originals. */
export function installConsoleRedaction(stream = process.stderr) {
  const methods = ["log", "info", "warn", "error", "debug", "trace"];
  const originals = Object.fromEntries(methods.map((m) => [m, console[m]]));
  for (const m of methods) {
    console[m] = (...args) => { stream.write(redact(util.format(...args)) + "\n"); };
  }
  return () => Object.assign(console, originals);
}

export const READ_TOOL = /^(get|search|read)[_-][a-z0-9_-]+$/i;

/** Let only read tools be registered on the MCP server. Throws if registration cannot be intercepted, so the
 *  launcher refuses to start rather than run with write tools possibly exposed. Returns the blocked names. */
export function guardToolRegistration(server) {
  if (!server || typeof server.tool !== "function") {
    throw new Error("cannot enforce read-only tools: the MCP server has no tool() registration method to guard");
  }
  const blocked = [];
  for (const method of ["tool", "registerTool"]) {
    const original = server[method];
    if (typeof original !== "function") continue;
    server[method] = function guarded(name, ...rest) {
      if (typeof name !== "string" || !READ_TOOL.test(name)) {
        blocked.push(String(name));
        return undefined;
      }
      return original.call(this, name, ...rest);
    };
  }
  return blocked;
}

export const INTERACTIVE_OAUTH_DISABLED =
  "Interactive QuickBooks OAuth is disabled inside the MCP server, so no browser window or callback listener was " +
  "opened. The stored sandbox authorization is missing, expired or revoked. Re-authorize once from a terminal: " +
  "node integrations/quickbooks/launch.mjs --auth (see integrations/quickbooks/README.md), then reconnect the server.";

const WILDCARD = new Set([undefined, null, "", "::", "0.0.0.0"]);
const LOOPBACK = new Set(["127.0.0.1", "::1", "localhost"]);

/** Constrain http listeners in this process (the only one is the OAuth callback):
 *  - allowInteractive false: refuse to listen at all (emits 'error'); nothing is bound and no browser opens.
 *  - allowInteractive true: a wildcard host becomes 127.0.0.1 plus a twin on ::1 (browsers resolve
 *    localhost to either), sharing the original request handler; a non-loopback host is refused.
 *  Returns a function that restores http.Server.prototype.listen. */
export function installLoopbackOAuthListener({ http, allowInteractive }) {
  const original = http.Server.prototype.listen;
  http.Server.prototype.listen = function loopbackListen(...args) {
    const server = this;
    const options = args[0] !== null && typeof args[0] === "object" ? args[0] : null;
    const port = options ? options.port : args[0];
    const host = options ? options.host : (typeof args[1] === "string" ? args[1] : undefined);
    const callback = args.find((a) => typeof a === "function");
    const fail = (message) => { process.nextTick(() => server.emit("error", new Error(message))); return server; };

    if (!allowInteractive) return fail(INTERACTIVE_OAUTH_DISABLED);
    if (!WILDCARD.has(host)) {
      return LOOPBACK.has(host) ? original.apply(server, args)
        : fail(`refusing to listen on non-loopback address ${host}`);
    }
    original.call(server, port, "127.0.0.1", () => {
      const twin = http.createServer((req, res) => server.emit("request", req, res));
      const ready = () => { if (callback) callback.call(server); };
      twin.once("listening", ready);
      twin.once("error", (err) => {  // no IPv6 loopback: 127.0.0.1 still serves
        console.error(`[qbo-launcher] IPv6 loopback listener unavailable (${err.code || err.message}); using 127.0.0.1 only`);
        ready();
      });
      original.call(twin, server.address().port, "::1");
      server.once("close", () => { if (twin.listening) twin.close(); });
      server.loopbackTwin = twin;
    });
    return server;
  };
  return () => { http.Server.prototype.listen = original; };
}

/** Node 20+ is required (node:test, stable ESM loader); the server is tested on the version in .nvmrc. */
export function checkNodeVersion(version = process.versions.node) {
  const major = Number(version.split(".")[0]);
  if (!(major >= 20)) throw new Error(`Node.js 20 or later is required, found ${version}`);
}

export function defaultTokenStore(home = os.homedir()) {
  return path.join(home, ".secrets", "qbo", "sandbox.env");
}

/** Resolve and check the token store and the effective server settings before the server loads.
 *  The server loads the token store over its environment (dotenv override), so the store's values win:
 *  they are checked here, by key only. No value is ever returned or logged. */
export function preflight({ env, repoRoot, parse, home = os.homedir() }) {
  const configured = env.QUICKBOOKS_TOKEN_STORE_PATH?.trim();
  const store = configured || defaultTokenStore(home);
  if (/\$\{|%[A-Za-z_]+%/.test(store)) {
    throw new Error("QUICKBOOKS_TOKEN_STORE_PATH contains an unexpanded variable; unset it to use the default " +
      `${defaultTokenStore("~")}, or give an absolute path`);
  }
  if (!path.isAbsolute(store)) throw new Error("QUICKBOOKS_TOKEN_STORE_PATH must be an absolute path");
  const rel = path.relative(path.resolve(repoRoot), path.resolve(store));
  if (rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel))) {
    throw new Error("the token store must be outside the repository so credentials can never be committed");
  }
  if (!fs.existsSync(store)) {
    throw new Error(`token store not found at ${store}; create it as described in integrations/quickbooks/README.md`);
  }
  const fromStore = parse(fs.readFileSync(store));
  const effective = { ...env, ...fromStore };
  const environment = effective.QUICKBOOKS_ENVIRONMENT || "sandbox";
  if (environment !== "sandbox") {
    throw new Error(`QUICKBOOKS_ENVIRONMENT is '${environment}'; this project connects to the sandbox only`);
  }
  const warnings = ["QUICKBOOKS_DISABLE_WRITE", "QUICKBOOKS_DISABLE_UPDATE", "QUICKBOOKS_DISABLE_DELETE"]
    .filter((flag) => effective[flag] !== "true")
    .map((flag) => `[qbo-launcher] ${flag} is not "true" after the token store is applied` +
      `${flag in fromStore ? " (the token store overrides it)" : ""}; write tools stay blocked by the read-only guard`);
  env.QUICKBOOKS_TOKEN_STORE_PATH = store;
  return { store, warnings };
}
