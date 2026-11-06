"""Pages beyond the main table. Filled in step by step (see DECISIONS.md build log)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


def register(app: FastAPI, render) -> None:
    stubs = {
        "/registry": ("Registry", "Step 1: the discovery agent's Florida registry appears here for approval."),
        "/review": ("Signal review", "Step 4: signals awaiting approval appear here."),
        "/manual": ("Manual fact entry", "Step 5: enter a dated, cited fact from a manual-only source."),
        "/records": ("Public-records requests", "Step 5: track Chapter 119 requests filed by a person."),
        "/merges": ("Merge review", "Ambiguous entity matches appear here."),
    }
    for path, (title, msg) in stubs.items():
        def page(request: Request, _t=title, _m=msg):
            return render(request, "stub.html", title=_t, message=_m)
        app.add_api_route(path, page, methods=["GET"], response_class=HTMLResponse)
