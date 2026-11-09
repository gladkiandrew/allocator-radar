"""Render the public read-only site to plain HTML (D-B15): `radar export-static --out dist/`.

The pages come from the real app in public mode, so the static snapshot shows exactly what the public
server would: approved and auto-approved signals only (each labeled), no review queue, no tools, no export,
and citations that point to the original public document, never to the lake.

The app is called in-process as an ASGI callable (no HTTP client, no socket). Starting from the public
pages, every internal link is followed; each page is written to a file, and every internal link is
rewritten to a relative path, so the snapshot works from any static host or straight off the disk.
A link to a page the snapshot does not hold (an admin page, a raw document) fails the export.
"""

from __future__ import annotations

import asyncio
import html
import os
import posixpath
import re
import shutil
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

SEEDS = ["/", "/how", "/about", "/gaps"]
PAGES = {"/how": "how.html", "/about": "about.html", "/gaps": "gaps.html"}
ATTR = re.compile(r'\b(href|src|action|data-href)="([^"]*)"')
CSS_URL = re.compile(r'url\("(/[^"]*)"\)')
ALLOCATOR_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class ExportError(RuntimeError):
    pass


def out_path(path: str, query: str = "") -> str:
    """Site path (+ query) -> file in the snapshot. Raises ExportError for anything not public."""
    if path == "/":
        q = {k: v[-1] for k, v in parse_qs(query).items()}
        strategy, tier = q.get("strategy", "all"), q.get("tier", "")
        if not re.fullmatch(r"[A-Za-z_]+", strategy) or (tier and not re.fullmatch(r"[A-Z]+", tier)):
            raise ExportError(f"unexpected filter: /?{query}")
        if strategy == "all" and not tier:
            return "index.html"
        return f"view-{strategy.lower()}-{(tier or 'all').lower()}.html"
    if path in PAGES:
        return PAGES[path]
    m = re.fullmatch(r"/a/([^/]+)", path)
    if m and ALLOCATOR_ID.fullmatch(m.group(1)):
        return f"a/{m.group(1)}.html"
    if path.startswith("/static/") and ".." not in path:
        return path.lstrip("/")
    raise ExportError(f"link to a page the public snapshot does not hold: {path}{'?' + query if query else ''}")


def _canonical(path: str, query: str) -> str:
    """One key per page: filter query strings are normalized so the same view is rendered once."""
    if path != "/" or not query:
        return path
    q = {k: v[-1] for k, v in parse_qs(query).items()}
    if q.get("strategy", "all") == "all" and not q.get("tier"):
        return "/"
    return "/?" + urlencode(sorted((k, v) for k, v in q.items() if k in ("tier", "strategy")))


def _get(app, target: str) -> tuple[int, str]:
    """GET one path from the ASGI app in-process."""
    parts = urlsplit(target)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
             "server": ("allocator.amagna.co", 443), "client": ("127.0.0.1", 0), "root_path": "",
             "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(),
             "headers": [(b"host", b"allocator.amagna.co")]}
    status, body = 0, bytearray()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            body.extend(msg.get("body", b""))

    asyncio.run(app(scope, receive, send))
    return status, body.decode("utf-8")


def _rel(page: str, target: str) -> str:
    rel = posixpath.relpath(target, posixpath.dirname(page) or ".")
    return rel


def rewrite(page_html: str, page: str, *, absolute: bool = False) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite internal links in one page. Returns the new HTML and the (path, query) of each internal link."""
    found: list[tuple[str, str]] = []

    def sub(m: re.Match) -> str:
        attr, raw = m.group(1), m.group(2)
        url = html.unescape(raw)
        if not url.startswith("/") or url.startswith("//"):
            return m.group(0)  # external, fragment-only or data: URL
        parts = urlsplit(url)
        target = out_path(parts.path, parts.query)
        if not parts.path.startswith("/static/"):
            found.append((parts.path, parts.query))
        new = ("/" + target) if absolute else _rel(page, target)
        if parts.fragment:
            new += "#" + parts.fragment
        return f'{attr}="{html.escape(new, quote=True)}"'

    return ATTR.sub(sub, page_html), found


def _updated_date() -> str:
    """The newest fetch in the store: what the data is current to."""
    from radar import db

    with db.connect() as con:
        ts = con.execute("SELECT max(last_fetched) FROM document_urls").fetchone()[0]
    from datetime import date

    return (ts.date() if ts else date.today()).isoformat()


def export(out: Path) -> dict:
    """Write the snapshot to `out` (replaced). Returns counts."""
    from radar import db, queries
    from radar import settings as S

    os.environ["RADAR_PUBLIC_MODE"] = "true"
    os.environ["RADAR_AUTH_MODE"] = "none"
    S.get_settings.cache_clear()
    from radar.web import app as webapp

    app = webapp.create_app()
    webapp.templates.env.globals.update(static_site=True, updated=_updated_date())
    try:
        out = Path(out)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)

        with db.connect() as con:
            seeds = SEEDS + [f"/a/{r['allocator_id']}" for r in queries.allocator_table(con)]
        queue, seen, written = deque(seeds), set(seeds), {}
        while queue:
            key = queue.popleft()
            parts = urlsplit(key)
            page = out_path(parts.path, parts.query)
            status, body = _get(app, key)
            if status != 200:
                raise ExportError(f"GET {key} returned {status}")
            body, links = rewrite(body, page)
            dest = out / page
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
            written[page] = key
            for path, query in links:
                k = _canonical(path, query)
                if k not in seen:
                    seen.add(k)
                    queue.append(k)

        _write_404(out, webapp)
        static_src = Path(webapp.HERE) / "static"
        n_assets = 0
        for f in static_src.rglob("*"):
            if f.is_file() and f.name != "htmx.min.js":  # the snapshot has no live endpoints for htmx to call
                dest = out / "static" / f.relative_to(static_src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix == ".css":  # url("/static/...") -> relative to the stylesheet
                    rel = "static/" + str(f.relative_to(static_src))
                    dest.write_text(CSS_URL.sub(lambda m: f'url("{_rel(rel, out_path(m.group(1)))}")', f.read_text()))
                else:
                    shutil.copy2(f, dest)
                n_assets += 1
        n_html = sum(1 for _ in out.rglob("*.html"))
        return {"pages": n_html, "briefs": sum(1 for p in written if p.startswith("a/")),
                "views": sum(1 for p in written if p.startswith("view-")), "assets": n_assets,
                "updated": webapp.templates.env.globals["updated"], "out": str(out)}
    finally:
        webapp.templates.env.globals.update(static_site=False)
        os.environ.pop("RADAR_PUBLIC_MODE", None)
        os.environ.pop("RADAR_AUTH_MODE", None)
        S.get_settings.cache_clear()


def _write_404(out: Path, webapp) -> None:
    """Served by the host for any unknown path, at any depth, so its links are root-absolute."""
    from types import SimpleNamespace

    from radar import scope

    req = SimpleNamespace(url=SimpleNamespace(path="/404"))
    body = webapp.templates.get_template("stub.html").render(
        request=req, title="Page not found", nav={"pending": 0}, scope=scope.config(),
        message="That page is not part of this snapshot. Start from the allocator table.")
    body, _ = rewrite(body, "404.html", absolute=True)
    (out / "404.html").write_text(body)
