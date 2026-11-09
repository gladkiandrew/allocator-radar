"""Site crawler (templates T-WP, T-CP, T-CITY, T-GOV, CUSTOM: static HTML listing pages).

For each APPROVED allocator with access AUTO:
  listing pages (from the approved registry) -> document links -> fetch -> lake -> parse.

- Refuses to run unless the current registry version is approved (Touchpoint 1).
- 5-year history (AGENDA §10 Q7). Undated links are kept, newest-dated first.
- Shared city pages (several plans on one page) assign each document to the plan(s)
  whose distinguishing word appears in the link text, URL, or section heading.
- A bot wall marks the allocator MANUAL in the store and stops that host.
- One thread per host; the PoliteClient enforces per-host delays and robots.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import unquote, urlsplit

from radar import db, ingest
from radar.crawl import legistar
from radar.crawl.links import classify, is_document, parse_date, parse_links
from radar.net.client import BotWall, HostThrottled, PoliteClient, RefusedRequest
from radar.parse import pdf
from radar.registry import approve as reg

log = logging.getLogger(__name__)

HISTORY_YEARS = 5
RENDER_TEMPLATES = {"T-AIRO"}  # client-rendered sites: listing pages go through the Playwright renderer
TYPE_PRIORITY = ["RFP", "AGENDA", "MINUTES", "IPS", "PERFORMANCE", "DISCLOSURE_112664", "ACTUARIAL", "FINANCIAL", "OTHER"]
PLAN_WORDS = {
    "police": r"\bpolice\b", "fire": r"\bfire(?:fighters?)?\b", "general": r"\b(?:general|ge)\b",
    "management": r"\bmanagement\b",
}
# City-wide listings (every board's agendas on one page): a document counts only if its section heading or link
# names a pension/retirement board.
CITYWIDE_PAGE = re.compile(r"/AgendaCenter/?(\?.*)?$|/Archive\.aspx/?$|/archive\.aspx/?$", re.I)
PENSION_SECTION = re.compile(r"pension|retire|trust fund|175|185", re.I)
# On city-hosted pages, a document link must look like it belongs to a pension plan.
CITY_DOC_HINT = re.compile(r"(pension|retire|trust|112\.664|actuar|valuation|investment|performance|"
                           r"agenda|minutes|ips|policy|financial|audit|board|quarter|rfp|disclosure)", re.I)


@dataclass
class DocLink:
    url: str
    title: str
    doc_type: str
    doc_date: date | None
    listed_on: str
    allocator_ids: list[str]


def _plan_word(allocator_id: str) -> str | None:
    tail = allocator_id.rsplit("-", 1)[-1]
    return tail if tail in PLAN_WORDS else None


_PLAN_KIND = {"police": r"\bpolice\b", "fire": r"\bfire(?:fighters?)?\b",
              "general": r"\b(?:general|employees|ge)\b", "management": r"\bmanagement\b"}


def _kinds(text: str) -> set[str]:
    t = unquote(text).replace("-", " ").replace("_", " ")
    return {k for k, pat in _PLAN_KIND.items() if re.search(pat, t, re.I)}


def foreign_plan_page(page_url: str, owners: list[dict]) -> bool:
    """A city listing page that names only OTHER plan kinds than its owners (e.g. the Police board's page
    filed under the Firefighters' plan) is not crawled for them."""
    if owners[0].get("template") not in {"T-CP", "T-CITY", "T-GOV"}:
        return False
    page_kinds = _kinds(urlsplit(page_url).path)
    if not page_kinds:
        return False
    owner_kinds = set()
    for o in owners:
        owner_kinds |= _kinds(o["allocator_id"].split("-", 2)[-1]) | _kinds(o.get("display_name", ""))
    return bool(owner_kinds) and not (page_kinds & owner_kinds)


def collect_links(client: PoliteClient, allocs: list[dict], pages: dict[str, list[str]]) -> list[DocLink]:
    """Read every listing page once; return document links assigned to allocators."""
    siblings = [a for a in allocs if _plan_word(a["allocator_id"])]
    by_page: dict[str, list[dict]] = defaultdict(list)
    for a in allocs:
        for u in pages.get(a["allocator_id"], []):
            by_page[u].append(a)
    found: dict[str, DocLink] = {}
    cutoff = date.today() - timedelta(days=365 * HISTORY_YEARS)
    for page_url, owners in by_page.items():
        if legistar.is_legistar(page_url):  # T-LEG: dated agenda/minutes files from the Legistar API
            for ev in legistar.events(client, page_url, HISTORY_YEARS):
                if ev["url"] not in found:
                    found[ev["url"]] = DocLink(ev["url"], ev["title"], ev["doc_type"], ev["doc_date"], page_url,
                                               [o["allocator_id"] for o in owners])
            continue
        if foreign_plan_page(page_url, owners):
            log.info("skipping %s: names a different plan than %s", page_url, [o["allocator_id"] for o in owners])
            continue
        if owners[0].get("template") in RENDER_TEMPLATES:
            from radar.net.render import render

            final, html = render(client, page_url)
            _, links = parse_links(html, final)
            r = type("R", (), {"url": final})()
        else:
            r = client.get(page_url)
            if r.status != 200:
                log.warning("listing page %s returned %s", page_url, r.status)
                continue
            _, links = parse_links(r.text, r.url)
        city = owners[0]["template"] in {"T-CP", "T-CITY", "T-GOV"}
        if len(owners) > 1:  # a plan-specific board page on a shared city site belongs to that plan only
            page_owned = [o for o in owners if (w := _plan_word(o["allocator_id"]))
                          and re.search(PLAN_WORDS[w], unquote(page_url).replace("-", " "), re.I)]
            if page_owned:
                owners = page_owned
        for l in links:
            if not is_document(l.url):
                continue
            label = f"{l.text} | {unquote(urlsplit(l.url).path.rsplit('/', 1)[-1])} | {l.context}"
            if city and not CITY_DOC_HINT.search(label):
                continue
            if CITYWIDE_PAGE.search(urlsplit(page_url).path + ("?" + urlsplit(page_url).query if urlsplit(page_url).query else "")) \
                    and not PENSION_SECTION.search(f"{l.context} {l.text}"):
                continue
            d = parse_date(l.text) or parse_date(unquote(l.url)) or parse_date(l.context)
            if d and d < cutoff:
                continue
            if d and d > date.today() + timedelta(days=60):
                d = None  # implausible: ignore rather than invent
            # 1) a plan named in the link text / filename wins (any sibling plan on this site),
            # 2) else the plan that owns the listing page, 3) else every owner of the page.
            link_label = f"{l.text} {unquote(urlsplit(l.url).path.rsplit('/', 1)[-1]).replace('-', ' ').replace('_', ' ')}"
            named = [o["allocator_id"] for o in siblings
                     if re.search(PLAN_WORDS[_plan_word(o["allocator_id"])], link_label, re.I)] if len(siblings) > 1 else []
            if named:
                ids = named
            elif len(owners) > 1:
                matched = [o["allocator_id"] for o in owners
                           if (w := _plan_word(o["allocator_id"])) and re.search(PLAN_WORDS[w], label, re.I)]
                ids = matched or [o["allocator_id"] for o in owners]
            else:
                ids = [owners[0]["allocator_id"]]
            title = (l.text or unquote(urlsplit(l.url).path.rsplit("/", 1)[-1]))[:200]
            prev = found.get(l.url)
            if prev:
                prev.allocator_ids = sorted(set(prev.allocator_ids) | set(ids))
                continue
            found[l.url] = DocLink(l.url, title, classify(label), d, r.url, ids)
    return list(found.values())


LATEST_KINDS = ["IPS", "PERFORMANCE", "ACTUARIAL", "FINANCIAL", "DISCLOSURE_112664"]
WINDOW_DAYS = 548  # 18 months of agendas + minutes


def select_targets(links: list[DocLink], cap: int = 150) -> list[DocLink]:
    """Depth targets (owner's Phase A bar): 18 months of agendas/minutes/RFPs, and the latest IPS,
    quarterly report, actuarial valuation, audited financials, and 112.664 disclosure."""
    since = date.today() - timedelta(days=WINDOW_DAYS)
    pick: list[DocLink] = []
    by_kind: dict[str, list[DocLink]] = defaultdict(list)
    for d in links:
        by_kind[d.doc_type].append(d)
    for kind in ("RFP", "AGENDA", "MINUTES"):
        dated = sorted([d for d in by_kind[kind] if d.doc_date and d.doc_date >= since], key=lambda d: d.doc_date, reverse=True)
        undated = [d for d in by_kind[kind] if not d.doc_date][:10]
        pick += dated + undated
    for kind in LATEST_KINDS:
        dated = sorted([d for d in by_kind[kind] if d.doc_date], key=lambda d: d.doc_date, reverse=True)[:2]
        pick += dated or by_kind[kind][:3]
    pick += sorted(by_kind["OTHER"], key=lambda d: (d.doc_date is None, -(d.doc_date.toordinal() if d.doc_date else 0)))[:8]
    seen, out = set(), []
    for d in pick:
        if d.url not in seen:
            seen.add(d.url)
            out.append(d)
    return out[:cap]


def record_links(links: list[DocLink]) -> None:
    with db.connect() as con:
        for d in links:
            for aid in d.allocator_ids:
                con.execute("""INSERT INTO document_links VALUES (?, ?, ?, ?, ?, ?, now())
                               ON CONFLICT (allocator_id, url) DO UPDATE SET last_seen = now(), title = excluded.title,
                                 doc_type = excluded.doc_type, doc_date = coalesce(excluded.doc_date, document_links.doc_date),
                                 listed_on = excluded.listed_on""",
                            [aid, d.url, d.title, d.doc_type, d.doc_date, d.listed_on])


def _rank(d: DocLink) -> tuple:
    return (0 if d.doc_date else 1, -(d.doc_date.toordinal() if d.doc_date else 0), TYPE_PRIORITY.index(d.doc_type))


def crawl_host(host: str, allocs: list[dict], pages: dict[str, list[str]], max_docs: int,
               skip_known: bool = True) -> dict:
    client = PoliteClient(purpose="crawl")
    stats = {"host": host, "listed": 0, "fetched": 0, "new": 0, "skipped_known": 0, "errors": 0, "status": "ok"}
    try:
        links = collect_links(client, allocs, pages)
        record_links(links)
        stats["listed"] = len(links)
        # Per allocator: depth targets. A shared link counts once.
        targets: dict[str, DocLink] = {}
        for a in allocs:
            mine = [d for d in links if a["allocator_id"] in d.allocator_ids]
            for d in select_targets(mine, cap=max_docs):
                targets[d.url] = d
        stats["targets"] = len(targets)
        with db.connect() as con:
            known = {u for (u,) in con.execute("SELECT DISTINCT url FROM document_urls").fetchall()}
        for d in sorted(targets.values(), key=_rank):
            if skip_known and d.url in known:
                stats["skipped_known"] += 1
                continue
            try:
                r = client.get(d.url)
            except RefusedRequest as e:
                log.info("refused %s: %s", d.url, e)
                stats["errors"] += 1
                continue
            if r.status != 200 or not r.content:
                stats["errors"] += 1
                continue
            for aid in d.allocator_ids:
                sha, new = ingest.store_document(r.content, url=d.url, allocator_id=aid, content_type=r.content_type,
                                                 title=d.title, doc_type=d.doc_type, doc_date=d.doc_date,
                                                 listed_on=d.listed_on)
                stats["new"] += int(new)
            pdf.parse_document(sha)
            stats["fetched"] += 1
    except BotWall as e:
        stats["status"] = f"bot wall: {e}"
        with db.connect() as con:
            for a in allocs:
                con.execute("UPDATE allocators SET access = 'MANUAL', access_reason = ? WHERE allocator_id = ?",
                            [f"bot wall during crawl: {e}"[:200], a["allocator_id"]])
    except HostThrottled as e:
        stats["status"] = f"throttled: {e}"
    except Exception as e:
        log.exception("crawl failed for %s", host)
        stats["status"] = f"error: {type(e).__name__}: {str(e)[:160]}"
    finally:
        client.close()
    with db.connect() as con:
        db.audit(con, "crawler", "crawl_host", host, status="ok" if stats["status"] == "ok" else "error", detail=stats)
    return stats


def run(only: list[str] | None = None, templates: set[str] | None = None, max_docs: int = 150) -> list[dict]:
    with db.connect() as con:
        if not reg.is_current_approved(con):
            raise PermissionError("registry/florida.yaml is not approved in its current version (Touchpoint 1)")
        allocs = db.rows(con, "SELECT * FROM allocators WHERE access = 'AUTO'")
        page_rows = db.rows(con, "SELECT allocator_id, url FROM allocator_pages")
    pages: dict[str, list[str]] = defaultdict(list)
    for p in page_rows:
        pages[p["allocator_id"]].append(p["url"])
    allocs = [a for a in allocs if pages.get(a["allocator_id"])
              and (not only or a["allocator_id"] in only)
              and (not templates or a["template"] in templates)]
    by_host: dict[str, list[dict]] = defaultdict(list)
    for a in allocs:
        by_host[urlsplit(pages[a["allocator_id"]][0]).hostname or ""].append(a)
    log.info("crawling %d allocators on %d hosts", len(allocs), len(by_host))
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(crawl_host, h, al, pages, max_docs) for h, al in by_host.items()]
        return [f.result() for f in futs]
