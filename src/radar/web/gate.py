"""Access gate: three audiences (public, member, admin), OFF by default.

Contract for the templates: every render gets `audience` ('public' | 'member' | 'admin') plus the booleans
`can_see_contacts` (member or admin) and `can_admin` (admin). See audience_context().

  gate off, public_mode false (local app)  -> 'admin'
  gate off, public_mode true (public site) -> settings.public_audience ('public' unless the member static build)
  gate on                                  -> the role in the signed session cookie

Passwords: `radar hash-password` prints pbkdf2_sha256$<iterations>$<salt hex>$<hash hex> (PBKDF2-HMAC-SHA256,
100,000 iterations: the most the Cloudflare Workers WebCrypto PBKDF2 allows, so the gated Worker verifies the
same string). A bare 64-hex sha256 is still accepted for the legacy RADAR_AUTH_PASSWORD_HASH.

Session cookie: "<role>.<expiry unix>.<hex HMAC-SHA256(secret, '<role>.<expiry>')>", HttpOnly, SameSite=Lax.
deploy/gate-worker.js uses the same format.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote

# Module level, not inside install(): with postponed annotations FastAPI resolves Request/Form from module globals.
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

ROLES = ("public", "member", "admin")
ADMIN_PATHS = ("/review", "/tasks", "/export", "/manual", "/records", "/registry", "/inbox")
OPEN_PATHS = ("/static/", "/login", "/logout", "/healthz")
COOKIE = "radar_session"
PBKDF2_ITERATIONS = 100_000


# ---------------------------------------------------------------- audience contract

def audience_for(request: Any) -> str:
    from radar.settings import get_settings

    s = get_settings()
    if s.gate_on:
        state = getattr(request, "state", None)
        return getattr(state, "audience", None) or "public"
    if s.public_mode:
        return s.public_audience
    return "admin"


def audience_context(request: Any) -> dict:
    aud = audience_for(request)
    from radar.settings import get_settings

    return {"audience": aud, "can_see_contacts": aud in ("member", "admin"), "can_admin": aud == "admin",
            "gate_on": get_settings().gate_on}


# ---------------------------------------------------------------- passwords

def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check against a pbkdf2 string (or a legacy sha256 hex)."""
    if not stored:
        return False
    stored = stored.strip()
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, it, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(it))
        except ValueError:
            return False
        return hmac.compare_digest(dk.hex(), hash_hex.lower())
    if len(stored) == 64:
        return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored.lower())
    return False


# ---------------------------------------------------------------- session cookie

def _sig(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(role: str, secret: str, ttl_s: int, *, now: float | None = None) -> str:
    exp = int((now or time.time()) + ttl_s)
    payload = f"{role}.{exp}"
    return f"{payload}.{_sig(secret, payload)}"


def verify_session(value: str | None, secret: str, *, now: float | None = None) -> str | None:
    """The role in a valid, unexpired cookie, else None."""
    if not value or not secret:
        return None
    try:
        role, exp, sig = value.split(".")
        exp_i = int(exp)
    except ValueError:
        return None
    if not hmac.compare_digest(_sig(secret, f"{role}.{exp}"), sig):
        return None
    if role not in ("member", "admin") or exp_i < (now or time.time()):
        return None
    return role


# ---------------------------------------------------------------- login backoff (in memory, per client IP)

class Backoff:
    """After FREE failed logins, each further failure locks the client out for 2^(n-FREE) s, capped at MAX_S."""

    FREE, MAX_S = 5, 300

    def __init__(self) -> None:
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def wait_s(self, key: str, now: float | None = None) -> float:
        now = now or time.time()
        with self._lock:
            n, until = self._fails.get(key, (0, 0.0))
        return max(0.0, until - now)

    def fail(self, key: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            n, _ = self._fails.get(key, (0, 0.0))
            n += 1
            until = now + min(2 ** (n - self.FREE), self.MAX_S) if n >= self.FREE else 0.0
            self._fails[key] = (n, until)
            if len(self._fails) > 10_000:  # bound memory
                self._fails.clear()

    def ok(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


# ---------------------------------------------------------------- install on the app

class GateConfigError(RuntimeError):
    pass


def check_config(s) -> None:
    if not s.session_secret or len(s.session_secret) < 16:
        raise GateConfigError("RADAR_GATE=on needs RADAR_SESSION_SECRET (16+ characters; e.g. "
                              "`python -c 'import secrets; print(secrets.token_hex(32))'`). Refusing to start.")
    if not (s.member_password_hash or s.admin_password_hash):
        raise GateConfigError("RADAR_GATE=on needs RADAR_MEMBER_PASSWORD_HASH and/or RADAR_ADMIN_PASSWORD_HASH "
                              "(from `radar hash-password`). Refusing to start.")


def _safe_next(nxt: str | None) -> str:
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or nxt.startswith("/login"):
        return "/"
    return nxt


def install(app, templates) -> None:
    """Gate middleware plus /login and /logout. Call only when settings.gate_on."""
    from radar.settings import get_settings

    s = get_settings()
    check_config(s)
    secret, ttl = s.session_secret, s.session_ttl_hours * 3600
    backoff = Backoff()

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path.startswith(OPEN_PATHS) or path == "/login":
            return await call_next(request)
        role = verify_session(request.cookies.get(COOKIE), secret)
        if role is None:
            if request.method in ("GET", "HEAD"):
                return RedirectResponse("/login?next=" + quote(path + (f"?{request.url.query}" if request.url.query else ""), safe="/?=&"),
                                        status_code=303)
            return PlainTextResponse("Sign in first.", status_code=401)
        if path.startswith(ADMIN_PATHS) and role != "admin":
            return PlainTextResponse("Admins only.", status_code=403)
        request.state.audience = role
        return await call_next(request)

    def login_page(request: Request, error: str | None = None, nxt: str = "/", status: int = 200):
        return templates.TemplateResponse(request, "login.html", {"error": error, "next": nxt}, status_code=status)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return login_page(request, nxt=_safe_next(next))

    @app.post("/login")
    def login(request: Request, password: str = Form(""), next: str = Form("/")):
        key = request.client.host if request.client else "?"
        nxt = _safe_next(next)
        wait = backoff.wait_s(key)
        if wait > 0:
            return login_page(request, f"Too many attempts. Try again in {int(wait) + 1} seconds.", nxt, 429)
        role = None
        # Check both hashes every time so the response time does not say which one matched.
        is_admin = verify_password(password, s.admin_password_hash)
        is_member = verify_password(password, s.member_password_hash)
        if is_admin:
            role = "admin"
        elif is_member:
            role = "member"
        if role is None:
            backoff.fail(key)
            return login_page(request, "That password is not right.", nxt, 401)
        backoff.ok(key)
        resp = RedirectResponse(nxt, status_code=303)
        resp.set_cookie(COOKIE, sign_session(role, secret, ttl), max_age=ttl, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
        return resp

    @app.api_route("/logout", methods=["GET", "POST"])
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp
