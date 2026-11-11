"""Curated extraction (D-B24): a signal read off a page by the data session when the extraction pass missed it.

It is held to the extractor's own validators: the quote must be on the cited page, the quote must share the
summary's specific names or numbers (supports()), the summary must pass the lint and the contact detector, and the date must fall in
the extractor's window (SEARCH_SCHEDULED may be up to 90 days ahead, D-B10). The row is stored pending
(`review_status = 'new'`) with an evidence row whose extractor is `curated-extraction`, so it is distinguishable
from model output, and the tiered auto-approval rules decide it like any other row.
"""

from __future__ import annotations

from datetime import date, timedelta

from radar import db
from typing import get_args

from radar.extract.extractor import SignalTypeLit, _dedupe_key, quote_on_page, supports
from radar.models import new_id
from radar.wall.lint import lint
from radar.wall.scrubber import detect

SIGNAL_TYPES = set(get_args(SignalTypeLit))
CURATED = "curated-extraction"
CURATED_VERSION = "1"


class CuratedError(ValueError):
    pass


def add_signal(sha_prefix: str, allocator_id: str, page: int, quote: str, signal_type: str, qualifier: str | None,
               event_date: date, summary: str, bucket: str | None = None, actor: str = "data-session") -> str:
    today = date.today()
    if signal_type not in SIGNAL_TYPES:
        raise CuratedError(f"unknown signal type {signal_type}")
    limit = today + timedelta(days=90) if signal_type == "SEARCH_SCHEDULED" else today
    if not (date(2000, 1, 1) <= event_date <= limit):
        raise CuratedError(f"event date {event_date} outside the extractor window")
    lr = lint(summary)
    if not lr.ok:
        raise CuratedError(f"summary lint: {lr.banned_found}")
    if detect(summary) or detect(quote):
        raise CuratedError("contact-shaped text in summary or quote")
    with db.connect() as con:
        row = con.execute("""SELECT du.sha256, du.url, a.display_name FROM document_urls du JOIN allocators a USING (allocator_id)
                             WHERE du.sha256 LIKE ? AND du.allocator_id = ? LIMIT 1""",
                          [sha_prefix + "%", allocator_id]).fetchone()
        if not row:
            raise CuratedError("document not listed for that plan")
        sha, url, name = row
        text = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?", [sha, page]).fetchone()
        if not text or not quote_on_page(quote, text[0]):
            raise CuratedError("quote not on the cited page")
        if not supports(summary, quote, name):
            raise CuratedError("quote does not support the summary (no shared name or number)")
        key = _dedupe_key(allocator_id, signal_type, qualifier, event_date)
        con.execute("BEGIN")
        eid = new_id()
        con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, NULL, now(), 1.0, 'CRAWL')",
                    [eid, sha, url, page, quote[:500], CURATED, CURATED_VERSION])
        existing = con.execute("SELECT signal_id FROM signals WHERE dedupe_key = ? AND review_status != 'rejected'",
                               [key]).fetchone()
        if existing:
            sid = existing[0]
        else:
            from radar.buckets import hf_relevant
            sid = new_id()
            con.execute(
                "INSERT INTO signals (signal_id, allocator_id, signal_type, qualifier, event_date, observed_at, summary, "
                "review_status, origin, dedupe_key, hf_relevant, bucket, review_note) "
                "VALUES (?, ?, ?, ?, ?, now(), ?, 'new', 'MACHINE', ?, ?, ?, ?)",
                [sid, allocator_id, signal_type, qualifier, event_date, summary, key, hf_relevant(bucket, summary),
                 bucket, f"{CURATED} by {actor}: extraction pass missed this item"])
        con.execute("INSERT INTO row_evidence VALUES ('signal', ?, ?) ON CONFLICT DO NOTHING", [sid, eid])
        db.audit(con, actor, "curated_signal", sid, detail={"sha256": sha, "page": page, "linked_existing": bool(existing)})
        con.execute("COMMIT")
    return sid
