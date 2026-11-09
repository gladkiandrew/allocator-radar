"""FastAPI app: server-rendered Jinja + HTMX. No SPA, no build step."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from radar import db, lake, queries
from radar.readiness import TIER_ORDER
from radar.settings import readiness_config
from radar.web.auth import require_user

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
def cite_href(e: dict) -> str:
    """A citation always lands on the page of the PDF: our cached copy if we hold it."""
    if e.get("document_sha256"):
        return f"/doc/{e['document_sha256']}" + (f"#page={e['page']}" if e.get("page") else "")
    return e["source_url"]


templates.env.globals["cite_href"] = cite_href


def uniq_cites(evs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in evs or []:
        k = (e.get("document_sha256"), e.get("page"), e.get("source_url"))
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


templates.env.filters["uniq_cites"] = uniq_cites


def _scope_stats(con, ids: list[str]) -> dict:
    """Documents read and cited signals for the plans in scope."""
    if not ids:
        return {"documents": 0, "pages": 0, "signals": 0}
    ph = ",".join("?" * len(ids))
    docs = con.execute(f"SELECT count(DISTINCT sha256) FROM document_urls WHERE allocator_id IN ({ph})", ids).fetchone()[0]
    pages = con.execute(f"""SELECT count(*) FROM pages WHERE sha256 IN
                            (SELECT DISTINCT sha256 FROM document_urls WHERE allocator_id IN ({ph}))""", ids).fetchone()[0]
    sigs = con.execute(f"""SELECT count(*) FROM signals WHERE allocator_id IN ({ph})
                           AND review_status IN ('approved', 'auto_approved')""", ids).fetchone()[0]
    return {"documents": docs, "pages": pages, "signals": sigs}


def create_app() -> FastAPI:
    db.init_db()
    app = FastAPI(title="Allocator Radar", docs_url=None, redoc_url=None, dependencies=[Depends(require_user)])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        from radar import scope

        with db.connect() as con:
            ctx.setdefault("nav", {"pending": queries.pending_count(con)})
        ctx.setdefault("scope", scope.config())
        return templates.TemplateResponse(request, name, ctx)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, tier: str | None = None, strategy: str = "all"):
        from radar.buckets import HF_BUCKETS

        with db.connect() as con:
            everything = queries.allocator_table(con)
        rows = [r for r in everything if not r["bar_reasons"]]  # main-table minimum bar (config/main_table.yaml)
        buckets = sorted({s.get("bucket") for r in rows for s in r["readiness"].reasons if s.get("bucket")})

        def matches(s):
            if strategy == "all":
                return True
            if strategy == "hf":
                return bool(s.get("hf_relevant")) or s.get("bucket") in HF_BUCKETS
            return s.get("bucket") == strategy

        if strategy != "all":  # keep plans whose qualifying signals match; show the matching signal first
            kept = []
            for r in rows:
                hits = [s for s in r["readiness"].reasons if matches(s)]
                if hits:
                    r["readiness"].reasons = hits
                    kept.append(r)
            rows = kept
        counts = {t: sum(1 for r in rows if r["readiness"].tier == t) for t in TIER_ORDER}
        counts["total"] = len(rows)
        shown = [r for r in rows if not tier or r["readiness"].tier == tier]
        spotlight = [r for r in rows if r["readiness"].tier in ("DEPLOYING", "PREPARING")] if not tier else []
        with db.connect() as con:
            kpi = _scope_stats(con, [r["allocator_id"] for r in everything])
        kpi.update(plans=len(everything), main=len([r for r in everything if not r["bar_reasons"]]))
        return render(request, "index.html", rows=shown, tiers=TIER_ORDER, tier=tier, counts=counts,
                      strategy=strategy, buckets=buckets, spotlight=spotlight, kpi=kpi,
                      n_gaps=sum(1 for r in everything if r["bar_reasons"]),
                      stale_days=readiness_config()["coverage"]["stale_after_days"])

    @app.get("/gaps", response_class=HTMLResponse)
    def gaps(request: Request):
        from radar.registry import approve as reg

        from radar import coverage

        with db.connect() as con:
            rows = [r for r in queries.allocator_table(con) if r["bar_reasons"]]  # sorted: DEPLOYING/PREPARING first
            for r in rows:
                r["gap_cat"], r["gap_desc"], r["gap_action"] = coverage.gap_category(con, r)
        groups = [(c, d, act, [r for r in rows if r["gap_cat"] == c]) for c, d, act in coverage.GAP_CATEGORIES]
        data, _ = reg.load()
        return render(request, "gaps.html", rows=rows, groups=groups,
                      sponsors=(data or {}).get("manual_only_sponsors", []))

    @app.get("/how", response_class=HTMLResponse)
    def how(request: Request):
        return render(request, "how.html", rules=readiness_config())

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        with db.connect() as con:
            everything = queries.allocator_table(con)
            ids = [r["allocator_id"] for r in everything]
            local = _scope_stats(con, ids)
            total = {
                "plans": con.execute("SELECT count(*) FROM allocators").fetchone()[0],
                "documents": con.execute("SELECT count(*) FROM documents").fetchone()[0],
                "pages": con.execute("SELECT count(*) FROM pages").fetchone()[0],
                "evidence": con.execute("SELECT count(*) FROM evidence").fetchone()[0],
                "facts": con.execute("SELECT count(*) FROM plan_facts").fetchone()[0]
                + con.execute("SELECT count(*) FROM mandates").fetchone()[0],
            }
        local.update(plans=len(everything), main=len([r for r in everything if not r["bar_reasons"]]),
                     deploying=sum(1 for r in everything if r["readiness"].tier == "DEPLOYING" and not r["bar_reasons"]),
                     preparing=sum(1 for r in everything if r["readiness"].tier == "PREPARING" and not r["bar_reasons"]))
        return render(request, "about.html", local=local, total=total)

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
