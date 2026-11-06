"""FastAPI app: server-rendered Jinja + HTMX. No SPA, no build step."""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from radar import db, lake, queries
from radar.readiness import TIER_ORDER
from radar.settings import get_settings, readiness_config

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
_basic = HTTPBasic(auto_error=False)


def require_user(creds: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    s = get_settings()
    if s.auth_mode == "none":
        return s.reviewer
    if creds is None or not s.auth_password_hash:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(creds.username, s.auth_user)
    ok_pass = secrets.compare_digest(hashlib.sha256(creds.password.encode()).hexdigest(), s.auth_password_hash)
    if not (ok_user and ok_pass):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


def cite_href(e: dict) -> str:
    """A citation always lands on the page of the PDF: our cached copy if we hold it."""
    if e.get("document_sha256"):
        return f"/doc/{e['document_sha256']}" + (f"#page={e['page']}" if e.get("page") else "")
    return e["source_url"]


templates.env.globals["cite_href"] = cite_href


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="Allocator Radar", docs_url=None, redoc_url=None, dependencies=[Depends(require_user)])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        with db.connect() as con:
            ctx.setdefault("nav", {"pending": queries.pending_count(con)})
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, tier: str | None = None):
        with db.connect() as con:
            rows = queries.allocator_table(con)
        counts = {t: sum(1 for r in rows if r["readiness"].tier == t) for t in TIER_ORDER}
        counts["total"] = len(rows)
        shown = [r for r in rows if not tier or r["readiness"].tier == tier]
        return render(request, "index.html", rows=shown, tiers=TIER_ORDER, tier=tier, counts=counts,
                      stale_days=readiness_config()["coverage"]["stale_after_days"])

    @app.get("/how", response_class=HTMLResponse)
    def how(request: Request):
        return render(request, "how.html", rules=readiness_config())

    @app.get("/doc/{sha}")
    def doc(sha: str):
        if not (len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)) or not lake.exists(sha):
            raise HTTPException(404)
        with db.connect() as con:
            meta = con.execute("SELECT content_type FROM documents WHERE sha256 = ?", [sha]).fetchone()
        ctype = (meta[0] if meta else None) or "application/pdf"
        return Response(lake.get(sha), media_type=ctype.split(";")[0],
                        headers={"Content-Disposition": f'inline; filename="{sha[:12]}.pdf"',
                                 "Cache-Control": "private, max-age=86400"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    from radar.web import routes  # noqa: E402  (registers the remaining pages)

    routes.register(app, render)
    return app
