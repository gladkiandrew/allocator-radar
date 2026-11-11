"""Endowments & Foundations pages (Phase C): /ef and /ef/<ein>."""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

SMALL = {"of", "and", "the", "for", "at", "in", "a", "an", "to", "&"}
KEEP_UPPER = {"FIU", "FAU", "UCF", "USF", "UNF", "UWF", "FGCU", "FSU", "UF", "FAMU", "II", "III", "IV", "LP", "LLC",
              "LLP", "CTA", "PE", "US", "USA", "NA", "AG"}


def pretty(name: str | None) -> str:
    """'UNIVERSITY OF FLORIDA FOUNDATION INC' -> 'University of Florida Foundation Inc'. Names already in mixed case are kept."""
    if not name or not name.isupper():
        return name or ""
    words = name.split()
    out = []
    for i, w in enumerate(words):
        core = re.sub(r"[^A-Za-z&]", "", w)
        if core in KEEP_UPPER:
            out.append(w)
        elif i and core.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


def register(app: FastAPI, render, public: bool) -> None:
    from radar.web.app import templates

    templates.env.filters["pretty"] = pretty

    @app.get("/ef", response_class=HTMLResponse)
    def ef_index(request: Request):
        from radar.sources.endowments import views
        from radar.sources.endowments.universe import KIND_LABEL

        rows = views.org_table(public=public)
        counts = {k: sum(1 for r in rows if r["kind"] == k) for k in KIND_LABEL}
        tiers = {t: sum(1 for r in rows if r["readiness"].tier == t) for t in ("DEPLOYING", "PREPARING", "WATCHING", "STALE")}
        n_signals = sum(len(r["signals"]) for r in rows)
        return render(request, "ef.html", rows=rows, kinds=KIND_LABEL, counts=counts, tiers=tiers, n_signals=n_signals,
                      ef_view="assets")

    @app.get("/ef/hedge-fund-buyers", response_class=HTMLResponse)  # registered before /ef/{ein}
    def ef_hf(request: Request):
        from radar.sources.endowments import views

        rows = views.hf_buyers()
        n_hf = sum(1 for r in rows if r["hf"])
        n_alt_only = sum(1 for r in rows if not r["hf"] and r["alt"])
        return render(request, "ef_hf.html", rows=rows, n_hf=n_hf, n_alt_only=n_alt_only,
                      n_neither=len(rows) - n_hf - n_alt_only, ef_view="hf")

    @app.get("/ef/{ein}", response_class=HTMLResponse)
    def ef_org(request: Request, ein: str):
        from radar.sources.endowments import views

        if not re.fullmatch(r"\d{9}", ein):
            raise HTTPException(404)
        o = views.org_brief(ein, public=public)
        if o is None:
            raise HTTPException(404)
        return render(request, "ef_org.html", o=o)
