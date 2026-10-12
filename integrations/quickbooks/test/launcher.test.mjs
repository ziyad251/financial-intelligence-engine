// End-to-end tests of launch.mjs against the real vendored server build, over stdio.
// The token store is a temporary FAKE one: no request ever reaches Intuit, and the real
// credentials are never read. Skipped when the server has not been built.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const launcher = path.join(here, "..", "launch.mjs");
const built = fs.existsSync(path.join(here, "..", "..", "..", "vendor", "quickbooks-online-mcp-server", "dist", "index.js"));
const FAKE_SECRET = "FakeClientSecretForLauncherTests0123456789";

function fakeStore(extra = []) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "qbo-launch-"));
  const file = path.join(dir, "sandbox.env");
  fs.writeFileSync(file, ["QUICKBOOKS_CLIENT_ID=ABfakeClientIdForLauncherTests0123456789",
    `QUICKBOOKS_CLIENT_SECRET=${FAKE_SECRET}`, ...extra].join("\n") + "\n");
  return file;
}

/** Start the launcher, send JSON-RPC messages, collect replies until `until` holds or the timeout. */
function session(store, messages, { env = {}, until, timeout = 20000 } = {}) {
  return new Promise((resolve) => {
    const childEnv = { ...process.env, QUICKBOOKS_TOKEN_STORE_PATH: store, ...env };
    for (const k of Object.keys(childEnv)) if (k.startsWith("QUICKBOOKS_DISABLE_") && !(k in env)) delete childEnv[k];
    const child = spawn(process.execPath, [launcher], { env: childEnv, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "", stderr = "";
    const replies = [];
    const finish = (code) => resolve({ code, stdout, stderr, replies });
    const timer = setTimeout(() => child.kill(), timeout);
    child.stdout.on("data", (d) => {
      stdout += d;
      const lines = stdout.split("\n");
      replies.length = 0;
      for (const l of lines.filter(Boolean)) { try { replies.push(JSON.parse(l)); } catch { replies.push({ invalid: l }); } }
      if (until && until(replies)) child.kill();
    });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("exit", (code) => { clearTimeout(timer); finish(code); });
    for (const m of messages) child.stdin.write(JSON.stringify(m) + "\n");
  });
}

const INIT = [
  { jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "launcher-test", version: "0" } } },
  { jsonrpc: "2.0", method: "notifications/initialized" },
];

test("only read tools are exposed even when the token store turns the write flags off", { skip: !built }, async () => {
  const store = fakeStore(["QUICKBOOKS_DISABLE_WRITE=false", "QUICKBOOKS_DISABLE_UPDATE=false", "QUICKBOOKS_DISABLE_DELETE=false"]);
  const r = await session(store, [...INIT, { jsonrpc: "2.0", id: 2, method: "tools/list" }],
    { until: (rs) => rs.some((x) => x.id === 2) });
  const list = r.replies.find((x) => x.id === 2);
  assert.ok(list, r.stderr);
  const names = list.result.tools.map((t) => t.name);
  assert.equal(names.length, 71);
  assert.ok(names.every((n) => /^(get|search|read)[_-]/.test(n)), names.join());
  assert.ok(!r.replies.some((x) => x.invalid), "stdout must carry only JSON-RPC messages");
  assert.match(r.stderr, /read-only guard blocked 71 non-read tool/);
  assert.match(r.stderr, /token store overrides it/);
  assert.ok(!r.stderr.includes(FAKE_SECRET));
});

test("without a stored authorization, a tool call fails without opening an OAuth listener", { skip: !built }, async () => {
  const store = fakeStore();
  const before = await portOpen(8000);
  const r = await session(store, [...INIT,
    { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "get_company_info", arguments: { params: {} } } }],
  { until: (rs) => rs.some((x) => x.id === 3) });
  const reply = r.replies.find((x) => x.id === 3);
  assert.ok(reply, r.stderr);
  // The upstream handler reports failures as text content (it does not set isError).
  assert.match(reply.result.content[0].text, /^Error: .*Interactive QuickBooks OAuth is disabled/);
  assert.match(reply.result.content[0].text, /launch\.mjs --auth/);
  assert.equal(await portOpen(8000), before);  // nothing new is listening on the callback port
  assert.ok(!r.stderr.includes(FAKE_SECRET) && !r.stdout.includes(FAKE_SECRET));
});

test("a production environment is refused before the server starts", { skip: !built }, async () => {
  const r = await session(fakeStore(["QUICKBOOKS_ENVIRONMENT=production"]), INIT, { timeout: 10000 });
  assert.equal(r.code, 1);
  assert.equal(r.stdout, "");
  assert.match(r.stderr, /refusing to start: QUICKBOOKS_ENVIRONMENT is 'production'; this project connects to the sandbox only/);
});

test("a server whose registration cannot be guarded is refused (fail closed)", async () => {
  const fake = fs.mkdtempSync(path.join(os.tmpdir(), "qbo-fakeserver-"));
  fs.mkdirSync(path.join(fake, "dist", "server"), { recursive: true });
  fs.mkdirSync(path.join(fake, "node_modules", "dotenv"), { recursive: true });
  fs.writeFileSync(path.join(fake, "package.json"), "{}");
  fs.writeFileSync(path.join(fake, "node_modules", "dotenv", "index.js"), "exports.parse = () => ({});");
  fs.writeFileSync(path.join(fake, "node_modules", "dotenv", "package.json"), '{"main":"index.js"}');
  fs.writeFileSync(path.join(fake, "dist", "server", "qbo-mcp-server.js"),
    "export class QuickbooksMCPServer { static GetServer() { return {}; } }");
  fs.writeFileSync(path.join(fake, "dist", "index.js"), "console.error('SHOULD NOT LOAD');");
  const r = await session(fakeStore(), INIT, { env: { QBO_MCP_SERVER_DIR: fake }, timeout: 10000 });
  assert.equal(r.code, 1);
  assert.match(r.stderr, /refusing to start: cannot enforce read-only tools/);
  assert.ok(!r.stderr.includes("SHOULD NOT LOAD"));
});

function portOpen(port) {
  return new Promise((resolve) => {
    const s = net.connect({ host: "127.0.0.1", port, timeout: 1000 });
    s.once("connect", () => { s.destroy(); resolve(true); });
    s.once("error", () => resolve(false));
    s.once("timeout", () => { s.destroy(); resolve(false); });
  });
}
