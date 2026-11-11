"""Pages beyond the main table."""

from __future__ import annotations

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from radar import db
from radar.registry import approve as reg
from radar.web.auth import require_user


def templates_partial(request, name, **ctx):
    from radar.web.app import templates
    from radar.web.gate import audience_context

    ctx.update(audience_context(request))
    return templates.TemplateResponse(request, name, ctx)


def register(app: FastAPI, render) -> None:
    stubs = {
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
        from radar.settings import get_settings

        if get_settings().public_mode:  # the public site shows approved signals only
            a["signals"] = [s for s in a["signals"] if s["review_status"] in ("approved", "auto_approved")]
        return render(request, "allocator.html", a=a)

    @app.get("/a/{allocator_id}/search", response_class=HTMLResponse)
    def allocator_search(request: Request, allocator_id: str, q: str = ""):
        from radar import search
        hits = search.search(q, allocator_id) if q else []
        return templates_partial(request, "search_results.html", hits=hits, q=q)

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
            s["hf"] = bool(s.get("hf_relevant"))
            s["tier_effect"] = trial.tier if trial.tier in ("DEPLOYING", "PREPARING") else None
        return sigs

    def _pending_sorted(con) -> list[dict]:
        """Pending signals in scope, in the order the queue shows them: what could move a tier first, newest first."""
        from radar import scope

        ids = scope.ids(con)
        sigs = _enrich(con, [s for s in queries.signals_for(con, statuses=("new",))
                             if ids is None or s["allocator_id"] in ids])
        sigs.sort(key=lambda s: (s["tier_effect"] is None, s["tier_effect"] != "DEPLOYING", -s["event_date"].toordinal()))
        return sigs

    # ---- focus mode: one pending signal per screen, keys a / r / s ----
    import re as _re
    from urllib.parse import quote as _quote

    _SIG_ID = _re.compile(r"[A-Za-z0-9_-]{1,80}")

    def _skips(raw: str | None) -> list[str]:
        out: list[str] = []
        for x in (raw or "").split(","):
            x = x.strip()
            if _SIG_ID.fullmatch(x) and x not in out:
                out.append(x)
        return out[-500:]

    def _next_url(skip: list[str], err: str | None = None, done: str | None = None) -> str:
        q = []
        if skip:
            q.append("skip=" + ",".join(skip))
        if err:
            q.append("err=" + _quote(err))
        if done:
            q.append("done=" + _quote(done))
        return "/review/next" + ("?" + "&".join(q) if q else "")

    @app.get("/review/next", response_class=HTMLResponse)
    def review_next(request: Request, skip: str | None = None, err: str | None = None, done: str | None = None):
        skipped = _skips(skip)
        with db.connect() as con:
            pending = _pending_sorted(con)
        left = [s for s in pending if s["signal_id"] not in skipped]
        n_skipped = len(pending) - len(left)
        return render(request, "review_focus.html", s=left[0] if left else None, remaining=len(left),
                      total=len(pending), n_skipped=n_skipped, skip=",".join(skipped), err=err, done=done)

    @app.post("/review/next/{signal_id}")
    def review_next_decide(signal_id: str, decision: str = Form(...), skip: str = Form(default=""),
                           user: str = Depends(require_user)):
        skipped = _skips(skip)
        if decision == "skip":
            if _SIG_ID.fullmatch(signal_id) and signal_id not in skipped:
                skipped.append(signal_id)
            return RedirectResponse(_next_url(skipped), status_code=303)
        if decision not in ("approved", "rejected"):
            return RedirectResponse(_next_url(skipped, err="decision must be approve, reject or skip"), status_code=303)
        try:
            review.decide(signal_id, decision, user)
        except (review.ReviewError, ValueError) as e:
            return RedirectResponse(_next_url(skipped, err=str(e)), status_code=303)
        return RedirectResponse(_next_url(skipped, done=decision), status_code=303)

    # ---- E&F spot-check: data/ef_spotcheck_25.md, decisions to a JSONL file, never the database ----
    from radar.web import ef_spotcheck as efs

    def _ef_url(skip: list[str], err: str | None = None, done: str | None = None) -> str:
        return "/review/ef" + _next_url(skip, err, done)[len("/review/next"):]

    @app.get("/review/ef", response_class=HTMLResponse)
    def review_ef(request: Request, skip: str | None = None, err: str | None = None, done: str | None = None):
        import duckdb

        from radar.sources.endowments.universe import KIND_LABEL
        from radar.web.app import public_href
        from radar.web.ef_routes import pretty

        skipped = _skips(skip)
        try:
            signals = efs.load_signals()
        except duckdb.Error as e:  # e.g. the DATA session holds the write lock
            return render(request, "review_ef.html", s=None, st=None, exists=True, skip="", err=None, done=None,
                          load_err=f"could not open the E&F store read-only: {e}", n_skipped=0)
        ids, exists = efs.sample(signals)
        st = efs.state(ids, efs.read_decisions())
        left = [i for i in st["undecided"] if i not in skipped]
        s = None
        if left:
            s = dict(signals[left[0]])
            s["org_pretty"] = pretty(s.get("org_name")) or s["ein"]
            s["kind_label"] = KIND_LABEL.get(s.get("org_kind"), s.get("org_kind") or "")
            url = public_href(s.get("source_url"))
            if url and s.get("page") and "#" not in url:  # some stored URLs already carry their #page=
                url += f"#page={s['page']}"
            s["href"] = url
            s["page_in_cite"] = bool(s.get("page")) and f"page {s['page']}" in (s.get("cite") or "").lower()
        return render(request, "review_ef.html", s=s, st=st, exists=exists, skip=",".join(skipped), err=err,
                      done=done, load_err=None, n_skipped=len(st["undecided"]) - len(left))

    @app.post("/review/ef/{signal_id}")
    def review_ef_decide(signal_id: str, decision: str = Form(...), skip: str = Form(default=""),
                         note: str = Form(default=""), user: str = Depends(require_user)):
        skipped = _skips(skip)
        ids, _ = efs.sample(efs.load_signals())
        if signal_id not in ids:
            return RedirectResponse(_ef_url(skipped, err="that signal is not in data/ef_spotcheck_25.md"), status_code=303)
        if decision == "skip":
            if signal_id not in skipped:
                skipped.append(signal_id)
            return RedirectResponse(_ef_url(skipped), status_code=303)
        try:
            efs.append_decision(signal_id, decision, user, note)
        except efs.SpotcheckError as e:
            return RedirectResponse(_ef_url(skipped, err=str(e)), status_code=303)
        return RedirectResponse(_ef_url(skipped, done=decision), status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    def review_page(request: Request, status: str = "new"):
        status = status if status in ("new", "approved", "auto_approved", "rejected") else "new"
        from radar import scope

        with db.connect() as con:
            ids = scope.ids(con)
            sigs = _enrich(con, [s for s in queries.signals_for(con, statuses=(status,))
                                 if ids is None or s["allocator_id"] in ids])
            counts: dict[str, int] = {}
            for st, aid in con.execute("SELECT review_status, allocator_id FROM signals").fetchall():
                if ids is None or aid in ids:
                    counts[st] = counts.get(st, 0) + 1
        for k in ("new", "approved", "auto_approved", "rejected"):
            counts.setdefault(k, 0)
        if status == "new":  # what could move a tier first, newest first
            sigs.sort(key=lambda s: (s["tier_effect"] is None, s["tier_effect"] != "DEPLOYING", -s["event_date"].toordinal()))
        return render(request, "review.html", signals=sigs, status=status, counts=counts,
                      signal_types=list(SignalType.__members__), policy_actor=review.policy_actor("signals"))

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
            s = [x for x in queries.signals_for(con, statuses=("new", "approved", "auto_approved", "rejected"))
                 if x["signal_id"] == signal_id]
            _enrich(con, s)
        return templates_partial(request, "review_card.html", s=s[0], error=error,
                                 signal_types=list(SignalType.__members__))

    # ---------------- Touchpoints 4 + 5: manual facts, records requests, inbox ----------------
    from datetime import date as _d

    from pydantic import ValidationError

    from radar import manual
    from radar.models import ManualFact

    def _allocs(con):
        from radar import scope

        return [a for a in db.rows(con, "SELECT allocator_id, display_name, jurisdiction_city FROM allocators ORDER BY display_name")
                if scope.contains(a)]

    @app.get("/manual", response_class=HTMLResponse)
    def manual_page(request: Request, msg: str | None = None):
        with db.connect() as con:
            allocs = _allocs(con)
        return render(request, "manual.html", allocators=allocs, sources=manual.manual_sources(),
                      signal_types=list(SignalType.__members__), form={}, msg=msg, err=None)

    @app.post("/manual", response_class=HTMLResponse)
    def manual_post(request: Request, allocator_id: str = Form(...), source_id: str = Form(...),
                    source_url: str = Form(...), event_date: str = Form(...), signal_type: str = Form(...),
                    qualifier: str = Form(default=""), fact: str = Form(...), user: str = Depends(require_user)):
        form = dict(allocator_id=allocator_id, source_id=source_id, source_url=source_url, event_date=event_date,
                    signal_type=signal_type, qualifier=qualifier, fact=fact)
        try:
            f = ManualFact(allocator_id=allocator_id, source_id=source_id, source_url=source_url,
                           event_date=event_date, signal_type=signal_type, qualifier=qualifier or None, fact=fact)
            manual.add_fact(f, user)
        except (ValidationError, manual.ManualError) as e:
            err = "; ".join(x["msg"] for x in e.errors()) if isinstance(e, ValidationError) else str(e)
            with db.connect() as con:
                allocs = _allocs(con)
            return render(request, "manual.html", allocators=allocs, sources=manual.manual_sources(),
                          signal_types=list(SignalType.__members__), form=form, msg=None, err=err)
        return RedirectResponse("/manual?msg=Added to the review queue as pending.", status_code=303)

    @app.get("/records", response_class=HTMLResponse)
    def records_page(request: Request, msg: str | None = None, err: str | None = None):
        with db.connect() as con:
            reqs = db.rows(con, """SELECT r.*, a.display_name,
                     (SELECT count(DISTINCT sha256) FROM document_urls du WHERE du.url LIKE 'manual://' || r.request_id || '/%') AS n_docs
                   FROM records_requests r LEFT JOIN allocators a USING (allocator_id) ORDER BY date_filed DESC""")
            allocs = _allocs(con)
        return render(request, "records.html", reqs=reqs, allocators=allocs, statuses=manual.RECORD_STATUSES,
                      msg=msg, err=err)

    @app.post("/records")
    def records_add(allocator_id: str = Form(...), request_text: str = Form(...), date_filed: str = Form(...),
                    note: str = Form(default=""), user: str = Depends(require_user)):
        try:
            rid = manual.add_request(allocator_id, request_text, _d.fromisoformat(date_filed), user, note or None)
        except (manual.ManualError, ValueError) as e:
            return RedirectResponse(f"/records?err={e}", status_code=303)
        return RedirectResponse(f"/records?msg=Tracking request {rid}. Drop responses in inbox/manual/records/{rid}/",
                                status_code=303)

    @app.post("/records/{request_id}/status")
    def records_status(request_id: str, status: str = Form(...), user: str = Depends(require_user)):
        try:
            manual.update_request(request_id, status, user)
        except manual.ManualError as e:
            return RedirectResponse(f"/records?err={e}", status_code=303)
        return RedirectResponse("/records", status_code=303)

    @app.post("/records/{request_id}/ingest")
    def records_ingest(request_id: str, user: str = Depends(require_user)):
        try:
            st = manual.ingest_request(request_id, user)
        except manual.ManualError as e:
            return RedirectResponse(f"/records?err={e}", status_code=303)
        return RedirectResponse(f"/records?msg=Ingested {st['files']} files ({st['new']} new).", status_code=303)

    @app.post("/inbox/ingest")
    def inbox_ingest(user: str = Depends(require_user)):
        st = manual.ingest_inbox(user)
        n = sum(v["files"] for v in st.values())
        return RedirectResponse(f"/records?msg=Manual inbox: {n} files across {len(st)} plans.", status_code=303)

    # ---------------- manual fetch tasks (data/manual_tasks.yaml) ----------------
    import yaml

    from radar.web import tasks as mt

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request, msg: str | None = None, err: str | None = None):
        load_err, rows = None, []
        try:
            rows = mt.sorted_tasks(mt.load())
        except mt.TaskError as e:
            load_err = str(e)
        except (yaml.YAMLError, UnicodeDecodeError) as e:  # malformed file: say so, never crash the page
            load_err = f"could not parse manual_tasks.yaml: {e}"
        for t in rows:
            t["_status"] = str(t.get("status") or "open")
            t["_uploadable"] = t["_status"] == "open" and mt.safe_name(t.get("allocator_id")) and mt.safe_name(t.get("file_name"))
        from radar.settings import get_settings

        return render(request, "tasks.html", tasks=rows, msg=msg, err=err, load_err=load_err,
                      exists=mt.tasks_path().exists(), inbox=get_settings().manual_inbox,
                      n_open=sum(1 for t in rows if t["_status"] == "open"))

    @app.post("/tasks/{task_id}/upload")
    async def tasks_upload(task_id: str, file: UploadFile = File(...), user: str = Depends(require_user)):
        from urllib.parse import quote

        data = await file.read(mt.MAX_UPLOAD_BYTES + 1)
        try:
            dest = mt.receive(task_id, data)
        except mt.TaskError as e:
            return RedirectResponse(f"/tasks?err={quote(str(e))}", status_code=303)
        rel = f"{dest.parent.name}/{dest.name}"
        return RedirectResponse(f"/tasks?msg={quote(f'Saved {rel} in the manual inbox; task {task_id} marked received.')}",
                                status_code=303)

    @app.get("/export.csv")
    def export_csv(user: str = Depends(require_user)):
        from fastapi.responses import Response as _R

        from radar import export
        text, n = export.build_csv(user)
        return _R(text, media_type="text/csv",
                  headers={"Content-Disposition": f'attachment; filename="allocator-radar-approved-{_d.today()}.csv"'})

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
