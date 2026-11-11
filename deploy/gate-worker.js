// Allocator Radar, gated members site: a Cloudflare Worker in front of the static member build.
//
// Deployed by `make deploy-gated` with wrangler.gated.jsonc. The assets (dist-member/, built by
// `radar export-static --audience member`) are served ONLY after a password check; without a valid session
// cookie the Worker serves the login page and nothing else (plus /static/*, the stylesheet and fonts it uses).
//
// Secrets (never in the repo; set once with `npx wrangler secret put <NAME> -c wrangler.gated.jsonc`):
//   GATE_SESSION_SECRET        random string, 32+ chars: the HMAC key for the session cookie
//   GATE_MEMBER_PASSWORD_HASH  output of `radar hash-password`: pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>
//
// Cookie: "member.<expiry unix>.<hex HMAC-SHA256(secret, 'member.<expiry>')>", HttpOnly, Secure, SameSite=Lax:
// the same format the local app uses (src/radar/web/gate.py). No state is kept, so there is no rate limiting here
// (an in-memory counter would only cover one isolate); PBKDF2 at 100,000 iterations makes each guess costly, and a
// Cloudflare WAF rate-limiting rule on POST /login is the right place to add more.

const COOKIE = "radar_session";
const TTL_S = 12 * 3600;
const enc = new TextEncoder();

function hex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function fromHex(s) {
  if (!/^(?:[0-9a-f]{2})*$/i.test(s)) throw new Error("bad hex");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}

// Constant-time string compare (equal-length check first; lengths are not secret here: both are fixed-size hex).
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function verifyPassword(password, stored) {
  if (!stored || !stored.startsWith("pbkdf2_sha256$")) return false;
  const parts = stored.trim().split("$");
  if (parts.length !== 4) return false;
  const iterations = parseInt(parts[1], 10);
  if (!(iterations > 0 && iterations <= 100000)) return false; // Workers' PBKDF2 limit
  let salt;
  try { salt = fromHex(parts[2]); } catch { return false; }
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
  return safeEqual(hex(bits), parts[3].toLowerCase());
}

async function hmacHex(secret, payload) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, enc.encode(payload)));
}

export async function signSession(role, secret, ttlS = TTL_S, now = Date.now() / 1000) {
  const payload = `${role}.${Math.floor(now + ttlS)}`;
  return `${payload}.${await hmacHex(secret, payload)}`;
}

export async function verifySession(value, secret, now = Date.now() / 1000) {
  if (!value || !secret) return null;
  const parts = value.split(".");
  if (parts.length !== 3) return null;
  const [role, exp, sig] = parts;
  if (!safeEqual(await hmacHex(secret, `${role}.${exp}`), sig)) return null;
  if (!/^\d+$/.test(exp) || parseInt(exp, 10) < now) return null;
  return role === "member" || role === "admin" ? role : null;
}

function readCookie(request, name) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

function safeNext(next) {
  return typeof next === "string" && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\") &&
    !next.startsWith("/login") ? next : "/";
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function loginPage(error = "", next = "/", status = 200) {
  const body = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Allocator Radar · Sign in</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body><main id="main" style="max-width:380px;margin:12vh auto;padding:0 16px">
<h1>Allocator Radar</h1><p class="muted">This site is for members. Enter the password to continue.</p>
${error ? `<div class="card" role="alert">${esc(error)}</div>` : ""}
<form method="post" action="/login" class="card">
<input type="hidden" name="next" value="${esc(next)}">
<label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required autofocus style="width:100%">
<button type="submit" style="margin-top:12px">Sign in</button></form></main></body></html>`;
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex" },
  });
}

function misconfigured() {
  return new Response("This site is not configured yet.", { status: 503, headers: { "Cache-Control": "no-store" } });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const secret = env.GATE_SESSION_SECRET;
    const hash = env.GATE_MEMBER_PASSWORD_HASH;
    if (!secret || secret.length < 16 || !hash) return misconfigured(); // fail closed: never serve assets ungated

    if (path === "/login" && request.method === "POST") {
      const form = await request.formData();
      const next = safeNext(form.get("next"));
      if (!(await verifyPassword(String(form.get("password") || ""), hash))) {
        return loginPage("That password is not right.", next, 401);
      }
      const cookie = await signSession("member", secret);
      return new Response(null, {
        status: 303,
        headers: {
          Location: next,
          "Set-Cookie": `${COOKIE}=${cookie}; Max-Age=${TTL_S}; Path=/; HttpOnly; Secure; SameSite=Lax`,
          "Cache-Control": "no-store",
        },
      });
    }
    if (path === "/logout") {
      return new Response(null, {
        status: 303,
        headers: { Location: "/login", "Set-Cookie": `${COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax` },
      });
    }
    if (path.startsWith("/static/")) return env.ASSETS.fetch(request); // what the login page needs

    const role = await verifySession(readCookie(request, COOKIE), secret);
    if (!role) {
      if (path === "/login") return loginPage("", safeNext(url.searchParams.get("next")));
      if (request.method !== "GET" && request.method !== "HEAD") return new Response("Sign in first.", { status: 401 });
      return Response.redirect(`${url.origin}/login?next=${encodeURIComponent(path + url.search)}`, 303);
    }
    if (path === "/login") return Response.redirect(`${url.origin}/`, 303);
    const resp = await env.ASSETS.fetch(request);
    const out = new Response(resp.body, resp);
    out.headers.set("Cache-Control", "private, no-store"); // members' pages must not sit in a shared cache
    return out;
  },
};
