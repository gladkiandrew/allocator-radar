// Plain-node tests for deploy/gate-worker.js (no wrangler, no network): `node --test tests/worker/`.
// Run from pytest by tests/test_gate.py when node is installed. The password hash below is built here for the test
// password "member-pass"; it is not a real credential.
import assert from "node:assert/strict";
import { createHash, pbkdf2Sync } from "node:crypto";
import { test } from "node:test";

import worker, { safeEqual, signSession, verifyPassword, verifySession } from "../../deploy/gate-worker.js";

const SALT = "00112233445566778899aabbccddeeff";
const DK = pbkdf2Sync("member-pass", Buffer.from(SALT, "hex"), 1000, 32, "sha256").toString("hex");
const HASH = ["pbkdf2_sha256", "1000", SALT, DK].join("$");
const ENV = {
  GATE_SESSION_SECRET: "test-secret-0123456789abcdef",
  GATE_MEMBER_PASSWORD_HASH: HASH,
  ASSETS: { fetch: async (req) => new Response(`asset ${new URL(req.url).pathname}`, { status: 200 }) },
};
const req = (path, init) => new Request(`https://members.example${path}`, init);

test("password check", async () => {
  assert.equal(await verifyPassword("member-pass", HASH), true);
  assert.equal(await verifyPassword("wrong", HASH), false);
  assert.equal(await verifyPassword("member-pass", createHash("sha256").update("member-pass").digest("hex")), false);
  assert.equal(safeEqual("abc", "abd"), false);
});

test("session cookie: valid, tampered, expired", async () => {
  const c = await signSession("member", ENV.GATE_SESSION_SECRET);
  assert.equal(await verifySession(c, ENV.GATE_SESSION_SECRET), "member");
  assert.equal(await verifySession(c.replace("member", "admin"), ENV.GATE_SESSION_SECRET), null);
  assert.equal(await verifySession(c, "another-secret-0123456789"), null);
  const old = await signSession("member", ENV.GATE_SESSION_SECRET, 10, 1000);
  assert.equal(await verifySession(old, ENV.GATE_SESSION_SECRET), null);
});

test("anonymous: only the login page and /static", async () => {
  const r = await worker.fetch(req("/a/canary.html"), ENV);
  assert.equal(r.status, 303);
  assert.match(r.headers.get("Location"), /\/login\?next=/);
  const login = await worker.fetch(req("/login"), ENV);
  assert.equal(login.status, 200);
  assert.match(await login.text(), /type="password"/);
  assert.equal((await worker.fetch(req("/static/style.css"), ENV)).status, 200);
  assert.equal((await worker.fetch(req("/index.html", { method: "POST" }), ENV)).status, 401);
});

test("login sets a signed HttpOnly cookie that opens the assets", async () => {
  const bad = await worker.fetch(req("/login", { method: "POST", body: new URLSearchParams({ password: "nope" }) }), ENV);
  assert.equal(bad.status, 401);
  assert.equal(bad.headers.get("Set-Cookie"), null);
  const ok = await worker.fetch(req("/login", { method: "POST", body: new URLSearchParams({ password: "member-pass", next: "/how" }) }), ENV);
  assert.equal(ok.status, 303);
  assert.equal(ok.headers.get("Location"), "/how");
  const setCookie = ok.headers.get("Set-Cookie");
  assert.match(setCookie, /HttpOnly/);
  assert.match(setCookie, /SameSite=Lax/);
  const cookie = setCookie.split(";")[0];
  const page = await worker.fetch(req("/how", { headers: { Cookie: cookie } }), ENV);
  assert.equal(page.status, 200);
  assert.equal(await page.text(), "asset /how");
  const evil = await worker.fetch(req("/login", { method: "POST", body: new URLSearchParams({ password: "member-pass", next: "//evil.example" }) }), ENV);
  assert.equal(evil.headers.get("Location"), "/");
});

test("python and worker agree on the hash format", async () => {
  // Produced by `radar hash-password` style code: see tests/test_gate.py::test_worker_accepts_python_hash.
  const fromPython = process.env.RADAR_TEST_PY_HASH;
  if (fromPython) assert.equal(await verifyPassword("member-pass", fromPython), true);
});

test("fails closed without secrets", async () => {
  const r = await worker.fetch(req("/index.html"), { ASSETS: ENV.ASSETS });
  assert.equal(r.status, 503);
});
