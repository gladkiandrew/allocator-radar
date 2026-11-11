"""Store a fetched (or manually dropped) document: lake + documents + document_urls."""

from __future__ import annotations

from datetime import date

from radar import db, lake


def store_document(
    data: bytes,
    *,
    url: str,
    allocator_id: str,
    content_type: str | None,
    provenance: str = "CRAWL",
    title: str | None = None,
    doc_type: str | None = None,
    doc_date: date | None = None,
    listed_on: str | None = None,
) -> tuple[str, bool]:
    """Returns (sha256, is_new_document)."""
    if title:  # D-B33: titles are scrubbed like page text
        from radar.wall.scrubber import scrub_text
        title = scrub_text(title, use_gliner=False)
    sha = lake.put(data)
    with db.connect() as con:
        existing = con.execute("SELECT 1 FROM documents WHERE sha256 = ?", [sha]).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO documents (sha256, content_type, n_bytes, provenance, first_seen) VALUES (?, ?, ?, ?, now())",
                [sha, content_type, len(data), provenance],
            )
        con.execute(
            """INSERT INTO document_urls (sha256, url, allocator_id, title, doc_type, doc_date, listed_on, first_seen, last_fetched)
               VALUES (?, ?, ?, ?, ?, ?, ?, now(), now())
               ON CONFLICT (sha256, url, allocator_id) DO UPDATE SET last_fetched = now(),
                 title = coalesce(excluded.title, document_urls.title),
                 doc_type = coalesce(excluded.doc_type, document_urls.doc_type),
                 doc_date = coalesce(excluded.doc_date, document_urls.doc_date)""",
            [sha, url, allocator_id, title, doc_type, doc_date, listed_on],
        )
    return sha, not existing
