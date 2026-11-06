"""Pages beyond the main table."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from radar import db
from radar.registry import approve as reg
from radar.web.auth import require_user


def templates_partial(request, name, **ctx):
    from radar.web.app import templates
    return templates.TemplateResponse(request, name, ctx)


def register(app: FastAPI, render) -> None:
    stubs = {
        "/manual": ("Manual fact entry", "Step 5: enter a dated, cited fact from a manual-only source."),
        "/records": ("Public-records requests", "Step 5: track Chapter 119 requests filed by a person."),
        "/merges": ("Merge review", "Ambiguous entity matches appear here."),
    }
    for path, (title, msg) in stubs.items():
        def page(request: Request, _t=title, _m=msg):
            return render(request, "stub.html", title=_t, message=_m)
        app.add_api_route(path, page, methods=["GET"], response_class=HTMLResponse)

    @app.get("/a/{allocator_id}", response_class=HTMLResponse)
    def allocator_page(request: Request, allocator_id: str):
        from fastapi import HTTPException

        from radar import queries
        with db.connect() as con:
            a = queries.allocator_brief(con, allocator_id)
        if a is None:
            raise HTTPException(404)
        return render(request, "allocator.html", a=a)

    # ---------------- Touchpoint 3: signal review ----------------
    from datetime import date as _date

    from radar import queries, review
    from radar.models import SignalType
    from radar.readiness import compute

    def _enrich(con, sigs):
        names = dict(con.execute("SELECT allocator_id, display_name FROM allocators").fetchall())
        for s in sigs:
            s["display_name"] = names.get(s["allocator_id"], s["allocator_id"])
            trial = compute([{**s, "review_status": "approved"}], None, _date.today())
            s["tier_effect"] = trial.tier if trial.tier in ("DEPLOYING", "PREPARING") else None
        return sigs

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, status: str = "new"):
        status = status if status in ("new", "approved", "rejected") else "new"
        with db.connect() as con:
            sigs = _enrich(con, queries.signals_for(con, statuses=(status,)))
            counts = dict(con.execute("SELECT review_status, count(*) FROM signals GROUP BY 1").fetchall())
        for k in ("new", "approved", "rejected"):
            counts.setdefault(k, 0)
        if status == "new":  # what could move a tier first, newest first
            sigs.sort(key=lambda s: (s["tier_effect"] is None, s["tier_effect"] != "DEPLOYING", -s["event_date"].toordinal()))
        return render(request, "review.html", signals=sigs, status=status, counts=counts,
                      signal_types=list(SignalType.__members__))

    @app.post("/review/{signal_id}", response_class=HTMLResponse)
    def review_decide(request: Request, signal_id: str, decision: str = Form(...), note: str = Form(default=""),
                      signal_type: str | None = Form(default=None), qualifier: str | None = Form(default=None),
                      event_date: str | None = Form(default=None), summary: str | None = Form(default=None),
                      user: str = Depends(require_user)):
        error = None
        try:
            review.decide(signal_id, decision, user, note=note, signal_type=signal_type, qualifier=qualifier,
                          event_date=event_date, summary=summary)
        except (review.ReviewError, ValueError) as e:
            error = str(e)
        if "hx-request" not in request.headers:
            return RedirectResponse("/review", status_code=303)
        with db.connect() as con:
            s = [x for x in queries.signals_for(con, statuses=("new", "approved", "rejected")) if x["signal_id"] == signal_id]
            _enrich(con, s)
        return templates_partial(request, "review_card.html", s=s[0], error=error,
                                 signal_types=list(SignalType.__members__))

    # ---------------- Touchpoint 1: registry ----------------
    @app.get("/registry", response_class=HTMLResponse)
    def registry_page(request: Request, msg: str | None = None, err: str | None = None):
        data, version = reg.load()
        with db.connect() as con:
            appr = reg.approval(con)
        n_auto = sum(1 for a in (data or {}).get("allocators", []) if a["access"] == "AUTO")
        return render(request, "registry.html", reg=data, version=version, appr=appr,
                      current=bool(appr and appr["registry_version"] == version), n_auto=n_auto, msg=msg, err=err)

    @app.post("/registry/approve")
    def registry_approve(version: str = Form(...), page: list[str] = Form(default=[]), note: str = Form(default=""),
                         user: str = Depends(require_user)):
        selected = {tuple(p.split("|", 1)) for p in page if "|" in p}
        try:
            n = reg.approve(user, version, selected, note=note or None)
        except ValueError as e:
            return RedirectResponse(f"/registry?err={e}", status_code=303)
        return RedirectResponse(f"/registry?msg=Approved. {n} allocators loaded, {len(selected)} listing pages queued for crawl.",
                                status_code=303)
