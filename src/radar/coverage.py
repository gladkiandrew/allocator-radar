"""Coverage: what each plan publishes vs what we hold, and the main-table minimum bar.

Status per checklist item:
  ok             we hold it (for "last 18 months" items: every dated one we found listed)
  partial        we hold some of what is listed
  missing        the plan's listing pages link it, we have not fetched it yet
  not_published  not found on the plan's approved listing pages
Coverage % = held items / items the plan publishes (not_published items are excluded).
"""

from __future__ import annotations

from datetime import date, timedelta

from radar import db
from radar.settings import load_yaml

ITEMS = [
    ("agendas", "Agendas, last 18 months", "AGENDA", 548),
    ("minutes", "Minutes, last 18 months", "MINUTES", 548),
    ("ips", "Investment policy statement (latest)", "IPS", None),
    ("performance", "Quarterly investment report (latest)", "PERFORMANCE", None),
    ("actuarial", "Actuarial valuation (latest)", "ACTUARIAL", None),
    ("financial", "Audited financial statements (latest)", "FINANCIAL", None),
    ("disclosure", "F.S. 112.664 disclosure (latest)", "DISCLOSURE_112664", None),
]

EFFECTIVE_DOCS = """
SELECT du.allocator_id, du.sha256, any_value(du.url) AS url, any_value(du.title) AS title,
       CASE WHEN any_value(du.doc_type) IS NULL OR any_value(du.doc_type) = 'OTHER'
            THEN coalesce(any_value(d.doc_kind), 'OTHER') ELSE any_value(du.doc_type) END AS kind,
       coalesce(max(du.doc_date), any_value(d.content_date)) AS dated,
       max(du.last_fetched) AS fetched
FROM document_urls du JOIN documents d USING (sha256)
WHERE du.allocator_id = ?
GROUP BY du.allocator_id, du.sha256
"""


def held_documents(con, allocator_id: str) -> list[dict]:
    return db.rows(con, EFFECTIVE_DOCS, [allocator_id])


def checklist(con, allocator_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    held = held_documents(con, allocator_id)
    held_urls = {h["url"] for h in held}
    links = db.rows(con, "SELECT url, doc_type, doc_date FROM document_links WHERE allocator_id = ?", [allocator_id])
    items, score, applicable = [], 0.0, 0
    for key, label, kind, window in ITEMS:
        since = today - timedelta(days=window) if window else None
        mine = [h for h in held if h["kind"] == kind and (since is None or (h["dated"] and h["dated"] >= since))]
        listed = [l for l in links if l["doc_type"] == kind and (since is None or (l["doc_date"] and l["doc_date"] >= since))]
        unfetched = [l for l in listed if l["url"] not in held_urls]
        if window:
            n_have, n_listed = len(mine), len({l["url"] for l in listed} | {h["url"] for h in mine})
            if n_listed == 0:
                status = "not_published"
            elif n_have >= n_listed:
                status = "ok"
            elif n_have:
                status = "partial"
            else:
                status = "missing"
            detail = f"{n_have} of {n_listed}" if n_listed else ""
            frac = (n_have / n_listed) if n_listed else 0
        else:
            newest = max(mine, key=lambda h: h["dated"] or date.min) if mine else None
            if newest:
                status, frac = "ok", 1.0
                detail = f"dated {newest['dated']}" if newest["dated"] else "undated"
            elif unfetched or listed:
                status, frac, detail = "missing", 0.0, f"{len(listed)} listed"
            else:
                status, frac, detail = "not_published", 0.0, ""
        if status != "not_published":
            applicable += 1
            score += frac
        items.append({"key": key, "label": label, "status": status, "detail": detail,
                      "newest": max(mine, key=lambda h: h["dated"] or date.min) if mine else None})
    pct = round(100 * score / applicable) if applicable else 0
    return {"items": items, "pct": pct, "applicable": applicable}


def bar(con, allocator: dict, today: date | None = None) -> list[str]:
    """Reasons this allocator does NOT meet the main-table bar (empty list = it does)."""
    cfg = load_yaml("main_table.yaml")
    today = today or date.today()
    reasons = []
    held = held_documents(con, allocator["allocator_id"])
    if allocator.get("access") == "MANUAL" and not held:  # manual-only plans qualify through the manual inbox
        reasons.append(f"manual-only source with no files in the manual inbox yet: {allocator.get('access_reason') or ''}".rstrip(": "))
    if len(held) < cfg["min_documents"]:
        reasons.append(f"{len(held)} documents fetched (minimum {cfg['min_documents']})")
    recent = today - timedelta(days=cfg["recent_document_days"])
    if not any(h["dated"] and h["dated"] >= recent for h in held):
        newest = max((h["dated"] for h in held if h["dated"]), default=None)
        reasons.append(f"no document dated in the last 6 months (newest: {newest or 'none dated'})")
    if cfg.get("require_consultant"):
        has = con.execute("SELECT 1 FROM consultant_engagements WHERE allocator_id = ? LIMIT 1", [allocator["allocator_id"]]).fetchone() \
            or con.execute("SELECT 1 FROM plan_facts WHERE allocator_id = ? AND fact_kind = 'INVESTMENT_CONSULTANT' LIMIT 1",
                           [allocator["allocator_id"]]).fetchone()
        if not has:
            reasons.append("investment consultant not identified in fetched documents")
    return reasons


# ---------------- gap categories (one primary cause per plan, in the order they should be fixed) ----------------
GAP_CATEGORIES = [
    ("MANUAL_SOURCE", "Blocked to automated clients (bot wall, robots.txt, or documents only on a do-not-crawl host)",
     "Drop downloaded files into inbox/manual/<plan id>/ or track a Chapter 119 records request."),
    ("NEEDS_HUMAN_DOWNLOAD", "The plan's listing pages answered, but its documents could not be fetched automatically",
     "Download the files in a browser (see data/manual_downloads.md) and drop them into inbox/manual/<plan id>/."),
    ("NO_DOCS", "The plan's approved listing pages were crawled but list no documents the crawler can read",
     "Find the plan's real board-document page (agenda portal, minutes archive) and approve it on Registry."),
    ("UNREADABLE", "Documents were fetched but are scanned images with no readable text, even after OCR",
     "Look for text versions on the plan's site, or request recent minutes as searchable PDFs (Chapter 119)."),
    ("PROCESSING", "Documents with candidate pages are waiting for extraction (transient; `radar extract` clears it)",
     "Run `radar extract --only <plan id>`."),
    ("NO_LISTING_PAGES", "Discovery found no document listing pages on the plan's site",
     "Deeper discovery (JS render, sitemap) or a better starting URL for the plan."),
    ("THIN", "Fewer than 3 documents fetched after crawling",
     "Deeper discovery on the plan's site; check for an agenda portal (CivicClerk, Granicus, Legistar)."),
    ("NOT_POSTING_RECENT", "The plan's posted documents are all older than 6 months",
     "Check its agenda portal for newer meetings; otherwise a records request for recent minutes."),
    ("CONSULTANT_UNIDENTIFIED", "Documents are current but no investment consultant was cited yet",
     "Consultant scan (targeted extraction of the pages that name the consultant)."),
]
_CAT = {c[0]: c for c in GAP_CATEGORIES}


def gap_category(con, a: dict) -> tuple[str, str, str]:
    """(category, description, next action) for a plan below the main-table bar."""
    from radar.extract.extractor import EXTRACTOR_VERSION

    aid = a["allocator_id"]
    reasons = " | ".join(a.get("bar_reasons") or [])
    n_docs = a.get("n_docs") or 0
    if a.get("access") == "MANUAL" and n_docs == 0:
        cat = "MANUAL_SOURCE"
    else:
        pages = con.execute("SELECT count(*) FROM allocator_pages WHERE allocator_id = ?", [aid]).fetchone()[0]
        crawled = con.execute("SELECT count(*) FROM document_links WHERE allocator_id = ?", [aid]).fetchone()[0]
        unextracted = con.execute(  # parsed documents that have a candidate page and no run for this version
            """SELECT count(DISTINCT du.sha256) FROM document_urls du JOIN documents d USING (sha256)
               WHERE du.allocator_id = ? AND d.parsed_at IS NOT NULL
                 AND EXISTS (SELECT 1 FROM pages p WHERE p.sha256 = du.sha256 AND p.prefilter_terms IS NOT NULL)
                 AND NOT EXISTS (SELECT 1 FROM extraction_runs r
                     WHERE r.sha256 = du.sha256 AND r.extractor_version = ?)""", [aid, EXTRACTOR_VERSION]).fetchone()[0]
        page_hosts = [r[0] for r in con.execute(
            "SELECT DISTINCT regexp_extract(url, '^https?://([^/]+)', 1) FROM allocator_pages WHERE allocator_id = ?",
            [aid]).fetchall()]
        attempted = any(con.execute("SELECT 1 FROM audit_log WHERE action = 'crawl_host' AND subject = ? LIMIT 1",
                                    [h]).fetchone() for h in page_hosts)
        unreadable, readable = _readability(con, aid)
        last = _last_crawls(con, page_hosts)
        if pages == 0:
            cat = "NO_LISTING_PAGES"
        elif n_docs == 0 and (not attempted or any(d.get("errors") for d in last)):
            cat = "NEEDS_HUMAN_DOWNLOAD"
            detail = "; ".join(f"{d['host']}: {d.get('errors', 0)} of {d.get('targets', 0)} fetches failed" for d in last) \
                or "listing hosts not crawled automatically"
            _, desc, action = _CAT[cat]
            return cat, f"{desc} ({detail})", action
        elif n_docs == 0:
            cat = "NO_DOCS"
            listed = sum(d.get("listed", 0) for d in last)
            _, desc, action = _CAT[cat]
            return cat, f"{desc} ({len(page_hosts)} host(s) crawled, {listed} document links listed)", action
        elif unextracted:
            cat = "PROCESSING"
        elif unreadable and readable < 3:
            _, desc, action = _CAT["UNREADABLE"]
            return "UNREADABLE", f"{desc} ({unreadable} of {unreadable + readable} documents have no readable text)", action
        elif "documents fetched" in reasons:
            cat = "THIN"
        elif "no document dated" in reasons:
            cat = "NOT_POSTING_RECENT"
        else:
            cat = "CONSULTANT_UNIDENTIFIED"
    _, desc, action = _CAT[cat]
    return cat, desc, action


def _readability(con, aid: str) -> tuple[int, int]:
    """(unreadable, readable) document counts: unreadable = parsed, but under 200 characters of text per page."""
    r = con.execute(
        """SELECT count(*) FILTER (WHERE chars < 200 * greatest(n, 1)), count(*) FILTER (WHERE chars >= 200 * greatest(n, 1))
           FROM (SELECT du.sha256, count(p.page_no) AS n, coalesce(sum(length(p.text_scrubbed)), 0) AS chars
                 FROM (SELECT DISTINCT sha256 FROM document_urls WHERE allocator_id = ?) du
                 JOIN documents d USING (sha256) LEFT JOIN pages p USING (sha256)
                 WHERE d.parsed_at IS NOT NULL GROUP BY du.sha256)""", [aid]).fetchone()
    return int(r[0] or 0), int(r[1] or 0)


def _last_crawls(con, hosts: list[str]) -> list[dict]:
    """The latest crawl_host audit detail for each host (empty for hosts never crawled)."""
    import json

    out = []
    for h in hosts:
        row = con.execute("""SELECT detail FROM audit_log WHERE action = 'crawl_host' AND subject = ?
                             ORDER BY ts DESC LIMIT 1""", [h]).fetchone()
        if row and row[0]:
            d = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            out.append({"host": h, **d})
    return out
