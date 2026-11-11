"""D-B17: a document with no candidate pages is recorded as done, so coverage never shows it as PROCESSING."""

from radar import coverage, db
from radar.extract.extractor import EXTRACTOR_VERSION, pending_documents


def _seed(text: str):
    with db.connect() as con:
        con.execute("""INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, jurisdiction_city,
                       jurisdiction_state, home_url, template, access, updated_at)
                       VALUES ('q', 'Q P&F', 'Q P&F', 'POLICE_FIRE', 'Miami', 'FL', 'https://q.invalid', 'T-WP', 'AUTO', now())""")
        con.execute("INSERT INTO allocator_pages (allocator_id, url) VALUES ('q', 'https://q.invalid/docs')")
        con.execute("""INSERT INTO documents (sha256, content_type, n_bytes, page_count, provenance, first_seen, parsed_at)
                       VALUES ('s1', 'application/pdf', 1, 1, 'CRAWL', now(), now())""")
        con.execute("""INSERT INTO document_urls (sha256, url, allocator_id, doc_type, doc_date, first_seen, last_fetched)
                       VALUES ('s1', 'https://q.invalid/a.pdf', 'q', 'MINUTES', DATE '2026-10-01', now(), now())""")
        con.execute("INSERT INTO pages VALUES ('s1', 1, ?, 0, NULL)", [text])


def test_no_candidate_document_marked_done():
    _seed("The meeting was called to order. Roll call was taken. The minutes were read. " * 5)
    with db.connect() as con:
        assert pending_documents(con) == []
        runs = con.execute("SELECT status FROM extraction_runs WHERE sha256 = 's1' AND extractor_version = ?",
                           [EXTRACTOR_VERSION]).fetchall()
        assert runs == [("none",)]
        cat, _, _ = coverage.gap_category(con, {"allocator_id": "q", "access": "AUTO", "n_docs": 1,
                                                "bar_reasons": ["fewer than 3 documents fetched (1)"]})
        assert cat != "PROCESSING"
