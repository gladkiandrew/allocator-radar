"""JS rendering for client-rendered plan sites (T-AIRO, FL2 DMS). Same Wall rules as client.py.

- The page URL must pass the same host verdict + robots checks as any GET.
- Every request the browser makes is intercepted: non-GET/HEAD requests are aborted
  (so no form, login, or analytics POST ever leaves), images/fonts/media are skipped,
  and every request is written to egress_log (purpose "render").
- Subresource GETs to third-party hosts (the site's own CDN/app hosts) are allowed for
  rendering only; nothing they return is stored. Only the rendered page's links are used.
- A challenge page or 403 raises BotWall. No stealth, no fingerprint changes, no retries.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from radar.net.client import BotWall, PoliteClient, RefusedRequest, _log, _looks_like_wall

log = logging.getLogger(__name__)
SKIP_TYPES = {"image", "font", "media"}
# D-B33: social-network widgets are never needed to read a listing page. They are refused, and the egress log records
# only that a widget request was refused, never its URL (widget URLs can carry profile paths or access tokens).
SOCIAL = re.compile(r"(?:^|[/.%2F])(?:facebook\.com|facebook\.net|fbcdn\.net|twitter\.com|x\.com|twimg\.com|"
                    r"linkedin\.com|licdn\.com|instagram\.com|tiktok\.com|youtube\.com/embed)", re.I)


def render(client: PoliteClient, url: str, *, wait_ms: int = 4000) -> tuple[str, str]:
    """Returns (final_url, rendered_html). Uses the client's policy checks and per-host delay."""
    client._check_method("GET")
    v = client._check_host(url)
    allowed, robots_delay = client.robots_allows(url)
    if not allowed:
        _log("GET", url, None, None, None, "disallowed", "render", "refused: robots")
        raise RefusedRequest(f"robots.txt disallows {url}")
    host = urlsplit(url).hostname or ""
    client._wait(host, max(client.min_delay, robots_delay, float(v.get("crawl_delay") or 0)))

    from playwright.sync_api import sync_playwright

    def on_route(route):
        req = route.request
        if SOCIAL.search(req.url):  # before anything else, so no social URL is ever logged
            _log(req.method, "https://social-widget.invalid/", None, None, None, "refused", "render",
                 "refused: social widget")
            return route.abort()
        if req.method not in ("GET", "HEAD"):
            _log(req.method, req.url, None, None, None, None, "render", "refused: method")
            return route.abort()
        if req.resource_type in SKIP_TYPES or urlsplit(req.url).scheme not in ("http", "https"):
            return route.abort()
        if req.url != url:  # subresources are logged without their query string (it can carry third-party tokens)
            u = urlsplit(req.url)
            _log(req.method, f"{u.scheme}://{u.netloc}{u.path}", None, None, None, "subresource", "render", None)
        else:
            _log(req.method, req.url, None, None, None, "allowed", "render", None)
        return route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=client.ua, service_workers="block", accept_downloads=False)
            page = ctx.new_page()
            page.route("**/*", on_route)
            resp = page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            status = resp.status if resp else None
            final = page.url
        finally:
            browser.close()
    if status == 403 or _looks_like_wall(html.encode()):
        raise BotWall(f"{host} presented a bot wall to the renderer")
    return final, html
