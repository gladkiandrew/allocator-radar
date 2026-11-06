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
