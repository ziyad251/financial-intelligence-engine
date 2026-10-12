// Unit tests for hardening.mjs. Every "secret" below is a fake value made up for the test.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import util from "node:util";

import {
  INTERACTIVE_OAUTH_DISABLED, checkNodeVersion, guardToolRegistration, installLoopbackOAuthListener, preflight, redact,
} from "../hardening.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE = {
  code: "AB11760000000FakeAuthorizationCode0123456789abcdef",
  state: "9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a3928",
  realm: "9341455500001234",
  refresh: "AB11790000000FakeRefreshToken0123456789abcdefABCDEF",
  access: "eyJlbmMiOiJBMTI4Q0JDLUhTMjU2IiwiYWxnIjoiZGlyIn0.FakeAccessTokenPayload1234567890.FakeSig123",
  secret: "FakeClientSecretValue0123456789abcdef",
  clientId: "ABfakeClientId0123456789abcdefghijklmnopqrstuvwxyz",
  basic: Buffer.from("ABfakeClientId:FakeClientSecretValue").toString("base64"),
};
const leaked = (text) => Object.entries(FAKE).filter(([, v]) => text.includes(v)).map(([k]) => k);

test("redacts the OAuth callback URL the server logs", () => {
  const line = `[auth-server] GET /callback?code=${FAKE.code}&state=${FAKE.state}&realmId=${FAKE.realm}`;
  const out = redact(line);
  assert.deepEqual(leaked(out), []);
  assert.equal(out, "[auth-server] GET /callback?code=[REDACTED]&state=[REDACTED]&realmId=[REDACTED]");
});

test("redacts the authorize URL and a mismatched-state message", () => {
  const out = redact(`https://appcenter.intuit.com/connect/oauth2?client_id=${FAKE.clientId}&scope=com.intuit.quickbooks.accounting&state=${FAKE.state}\n` +
    `[auth-server] Rejected callback with mismatched state (got: ${FAKE.state})`);
  assert.deepEqual(leaked(out).filter((k) => k !== "state"), []);
  assert.ok(!out.includes(`state=${FAKE.state}`));
});

test("redacts a token-exchange error object printed in full", () => {
  // The shape intuit-oauth attaches to a failed exchange (authResponse, axios config with headers and body).
  const error = Object.assign(new Error("Request failed with status code 400"), {
    authResponse: { token: { access_token: FAKE.access, refresh_token: FAKE.refresh, realmId: FAKE.realm },
      response: { config: { headers: { Authorization: `Basic ${FAKE.basic}` },
        data: `grant_type=authorization_code&code=${FAKE.code}&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fcallback` } } },
    originalMessage: "Request failed with status code 400",
  });
  const printed = util.format("Error during token creation:", util.inspect(error, { depth: Infinity }));
  const out = redact(printed);
  assert.deepEqual(leaked(out), [], out);
  assert.match(out, /Request failed with status code 400/);  // the diagnosis survives
});

test("redacts JSON, .env lines and bare tokens", () => {
  const out = redact([
    JSON.stringify({ access_token: FAKE.access, refresh_token: FAKE.refresh, client_secret: FAKE.secret }),
    `QUICKBOOKS_CLIENT_SECRET=${FAKE.secret}`, `QUICKBOOKS_REFRESH_TOKEN=${FAKE.refresh}`,
    `QUICKBOOKS_REALM_ID=${FAKE.realm}`, `Authorization: Bearer ${FAKE.access}`, `token was ${FAKE.refresh}`,
  ].join("\n"));
  assert.deepEqual(leaked(out), [], out);
});

test("keeps ordinary diagnostics", () => {
  for (const line of ["Request failed with status code 401", "Error: listen EADDRINUSE: address already in use :::8000",
    "{ code: 'ECONNREFUSED', port: 8000 }", "[qbo-client] WARNING: refresh token expires in ~9 day(s)."]) {
    assert.equal(redact(line), line);
  }
});

test("console output goes to stderr only, redacted (stdout is the MCP protocol channel)", () => {
  const script = `import { installConsoleRedaction } from ${JSON.stringify(new URL("../hardening.mjs", import.meta.url).href)};
    installConsoleRedaction();
    console.log("[auth-server] GET /callback?code=${FAKE.code}&state=${FAKE.state}&realmId=${FAKE.realm}");
    console.error("Error during token creation:", { token: { refresh_token: "${FAKE.refresh}" } });
    console.info("QUICKBOOKS_CLIENT_SECRET=${FAKE.secret}");`;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", script], { encoding: "utf8" });
  assert.equal(run.status, 0, run.stderr);
  assert.equal(run.stdout, "");
  assert.deepEqual(leaked(run.stderr), [], run.stderr);
  for (const marker of ["[auth-server] GET /callback?code=[REDACTED]", "Error during token creation:",
    "QUICKBOOKS_CLIENT_SECRET=[REDACTED]"]) assert.ok(run.stderr.includes(marker), run.stderr);
});

test("only read tools can be registered, whatever the names", () => {
  const registered = [];
  const server = { tool(name) { registered.push(name); return { name }; }, registerTool(name) { registered.push(name); } };
  const blocked = guardToolRegistration(server);
  const names = ["get_company_info", "get-bill", "search_bills", "read_invoice", "create_bill", "create-bill",
    "update_vendor", "update-vendor", "delete_item", "delete-item", "send_invoice", "void_payment", "upload_file", "",
    "getCompanyInfo"];
  names.forEach((n) => server.tool(n, "description", {}, () => {}));
  server.registerTool("delete_everything", {}, () => {});
  assert.deepEqual(registered, ["get_company_info", "get-bill", "search_bills", "read_invoice"]);
  assert.equal(blocked.length, names.length - 4 + 1);
});

test("the tool guard fails closed when registration cannot be intercepted", () => {
  assert.throws(() => guardToolRegistration({}), /cannot enforce read-only tools/);
  assert.throws(() => guardToolRegistration(null), /cannot enforce read-only tools/);
});

test("without --auth no OAuth listener is opened", async () => {
  const restore = installLoopbackOAuthListener({ http, allowInteractive: false });
  const server = http.createServer();
  try {
    const error = await new Promise((resolve) => { server.on("error", resolve); server.listen(0, "::", () => resolve(null)); });
    assert.ok(error && error.message === INTERACTIVE_OAUTH_DISABLED);
    assert.equal(server.listening, false);
  } finally {
    if (server.listening) server.close();  // a regression must fail, not hang the run on an open listener
    restore();
  }
});

const connect = (host, port) => new Promise((resolve) => {
  const socket = net.connect({ host, port, timeout: 1500 });
  socket.once("connect", () => { socket.destroy(); resolve(true); });
  socket.once("error", () => resolve(false));
  socket.once("timeout", () => { socket.destroy(); resolve(false); });
});
const get = (host, port) => new Promise((resolve, reject) => {
  http.get({ host, port, path: "/callback?state=x" }, (res) => { res.resume(); resolve(res.statusCode); }).on("error", reject);
});
const lanAddress = () => Object.values(os.networkInterfaces()).flat()
  .find((a) => a && a.family === "IPv4" && !a.internal)?.address;

test("with --auth the callback listener binds loopback only, IPv4 and IPv6, with one handler", async () => {
  const restore = installLoopbackOAuthListener({ http, allowInteractive: true });
  const server = http.createServer((req, res) => { res.writeHead(204); res.end(); });
  try {
    await new Promise((resolve, reject) => { server.on("error", reject); server.listen(0, "::", resolve); });
    const { address, port } = server.address();
    assert.equal(address, "127.0.0.1");
    assert.equal(await get("127.0.0.1", port), 204);
    if (server.loopbackTwin?.listening) {
      assert.equal(server.loopbackTwin.address().address, "::1");
      assert.equal(await get("::1", port), 204);
    }
    const lan = lanAddress();
    if (lan) assert.equal(await connect(lan, port), false, `reachable on ${lan}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    restore();
  }
  assert.equal(server.loopbackTwin?.listening ?? false, false);  // closing the server closes its twin
});

test("with --auth a non-loopback host is refused", async () => {
  const restore = installLoopbackOAuthListener({ http, allowInteractive: true });
  try {
    const server = http.createServer();
    const error = await new Promise((resolve) => { server.on("error", resolve); server.listen(0, "192.0.2.1", () => resolve(null)); });
    assert.match(error.message, /refusing to listen on non-loopback address 192\.0\.2\.1/);
  } finally { restore(); }
});

function tempStore(lines) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qbo-test-"));
  const file = path.join(dir, "sandbox.env");
  fs.writeFileSync(file, lines.join("\n") + "\n");
  return file;
}
const parse = (buf) => Object.fromEntries(buf.toString().split("\n").filter((l) => l.includes("="))
  .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const repoRoot = path.resolve(here, "..", "..", "..");
const FLAGS = { QUICKBOOKS_DISABLE_WRITE: "true", QUICKBOOKS_DISABLE_UPDATE: "true", QUICKBOOKS_DISABLE_DELETE: "true" };

test("preflight: sandbox store outside the repo passes, and the path is exported for the server", () => {
  const store = tempStore([`QUICKBOOKS_CLIENT_SECRET=${FAKE.secret}`]);
  const env = { QUICKBOOKS_TOKEN_STORE_PATH: store, ...FLAGS };
  assert.deepEqual(preflight({ env, repoRoot, parse }), { store, warnings: [] });
  assert.equal(env.QUICKBOOKS_TOKEN_STORE_PATH, store);
});

test("preflight: the default store is ~/.secrets/qbo/sandbox.env on every OS", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "qbo-home-"));
  fs.mkdirSync(path.join(home, ".secrets", "qbo"), { recursive: true });
  fs.writeFileSync(path.join(home, ".secrets", "qbo", "sandbox.env"), "QUICKBOOKS_CLIENT_ID=x\n");
  const env = { ...FLAGS };
  assert.equal(preflight({ env, repoRoot, parse, home }).store, path.join(home, ".secrets", "qbo", "sandbox.env"));
});

test("preflight refuses unsafe or unusable settings", () => {
  const cases = [
    [{ QUICKBOOKS_TOKEN_STORE_PATH: "${USERPROFILE}\\.secrets\\qbo\\sandbox.env" }, /unexpanded variable/],
    [{ QUICKBOOKS_TOKEN_STORE_PATH: "relative/sandbox.env" }, /must be an absolute path/],
    [{ QUICKBOOKS_TOKEN_STORE_PATH: path.join(repoRoot, "sandbox.env") }, /outside the repository/],
    [{ QUICKBOOKS_TOKEN_STORE_PATH: path.join(os.tmpdir(), "qbo-missing", "nope.env") }, /token store not found/],
    [{ QUICKBOOKS_TOKEN_STORE_PATH: tempStore(["QUICKBOOKS_ENVIRONMENT=production"]) }, /sandbox only/],
  ];
  for (const [env, message] of cases) assert.throws(() => preflight({ env: { ...env }, repoRoot, parse }), message);
});

test("preflight warns, without values, when the token store turns write flags off", () => {
  const store = tempStore(["QUICKBOOKS_DISABLE_WRITE=false", `QUICKBOOKS_CLIENT_SECRET=${FAKE.secret}`]);
  const { warnings } = preflight({ env: { QUICKBOOKS_TOKEN_STORE_PATH: store, ...FLAGS }, repoRoot, parse });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /QUICKBOOKS_DISABLE_WRITE .*token store overrides it.*read-only guard/);
  assert.deepEqual(leaked(warnings[0]), []);
});

test("Node version check", () => {
  assert.doesNotThrow(() => checkNodeVersion("20.0.0"));
  assert.doesNotThrow(() => checkNodeVersion("24.12.0"));
  assert.throws(() => checkNodeVersion("18.19.0"), /Node.js 20 or later/);
});
