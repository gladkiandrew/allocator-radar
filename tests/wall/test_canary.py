"""Wall layer 3: the canary packet goes through real ingest + parse; nothing survives."""
from radar import db, ingest
from radar.parse import pdf
from radar.wall import live
from radar.wall.canary import canary_pdf


def test_canary_pdf_parses():
    pages = pdf.extract_pages(canary_pdf(), "application/pdf")
    assert "Request for Proposals" in pages[0]
    assert "canary.trustee@example.invalid" in pages[0]  # raw has it; derived layers must not


def test_canary_leaves_zero_contact_tokens():
    sha, _ = ingest.store_document(canary_pdf(), url="https://canary.invalid/packet.pdf", allocator_id="canary",
                                   content_type="application/pdf")
    pdf.parse_document(sha)
    with db.connect() as con:
        assert live.check_sweep(con, live.CANARY_TOKENS) == []
        text = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ?", [sha]).fetchone()[0]
    assert "Request for Proposals" in text  # the fact survives
    assert "[REDACTED-CONTACT]" in text


def test_raw_lake_is_encrypted():
    from radar import lake
    from radar.settings import get_settings

    sha = lake.put(canary_pdf())
    raw = (get_settings().lake_dir / "raw" / sha[:2] / sha).read_bytes()
    assert b"canary.trustee" not in raw and b"%PDF" not in raw
    assert lake.get(sha) == canary_pdf()


def test_run_canary_helper():
    assert live.run_canary() == []


import os

import pytest


@pytest.mark.skipif(os.environ.get("RADAR_TEST_GLINER") != "1", reason="slow (embedding model); nightly runs it")
def test_canary_never_reaches_vector_store():
    from radar import search
    with db.connect() as con:
        con.execute("""INSERT INTO allocators (allocator_id, legal_name, display_name, allocator_type, home_url, template,
                       access, updated_at) VALUES ('canary','C','C','POLICE_FIRE','https://c.invalid','T-WP','AUTO',now())""")
    sha, _ = ingest.store_document(canary_pdf(), url="https://canary.invalid/p.pdf", allocator_id="canary",
                                   content_type="application/pdf")
    pdf.parse_document(sha)
    assert search.index_new() >= 1
    assert live.check_lance(live.CANARY_TOKENS) == []
    assert search.search("Request for Proposals hedge fund", "canary")
