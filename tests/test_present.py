"""Public-page presentation: tooltip cleanup, coverage note, consultant map, hedge fund strip."""

import json
from datetime import date, timedelta

from radar.web import present


def test_clean_quote_fixes_pdf_artifacts():
    garbled = ("MOTION: To direct the Investment Consultant to invite the firms of O’Connell X\nBroomfield, Bracco and "
               "Constitution Capital to interview at the October 19, Rossi X\n2026 meeting")
    assert present.clean_quote(garbled) == ("MOTION: To direct the Investment Consultant to invite the firms of Broomfield, "
                                            "Bracco and Constitution Capital to interview at the October 19, 2026 meeting")
    assert present.clean_quote("hire MFS as the New Ac(cid:415)ve Interna(cid:415)onal Growth Equity manager.") == \
        "hire MFS as the New Active International Growth Equity manager."
    assert present.clean_quote("Deputy Director­") == "Deputy Director"
    assert present.clean_quote("a b � c\n\nd") == "a b c d"
    assert present.clean_quote(None) == ""
    assert present.clean_quote("Approved by vote of 5-0.") == "Approved by vote of 5-0."


def test_coverage_note_six_month_rule():
    today = date(2026, 11, 10)
    assert present.coverage_note(date(2026, 6, 1), today) is None
    assert present.coverage_note(date(2026, 4, 1), today) == "last document: April 2026"
    assert present.coverage_note(None, today) == "no dated document on file"


def test_consultant_map_optional(tmp_path, monkeypatch):
    from radar import settings as S

    present.consultant_map.cache_clear()
    assert present.consultant_name("AndCo Consulting") == "AndCo Consulting"  # no map file: raw name
    data_dir = S.get_settings().data_dir
    (data_dir / "consultant_map.json").write_text(json.dumps({"Mariner": ["AndCo Consulting", "AndCo Consulting, LLC"]}))
    present.consultant_map.cache_clear()
    try:
        assert present.consultant_name("ANDCO CONSULTING, LLC") == "Mariner"
        assert present.consultant_name("AndCo Consulting | Graystone") == "Mariner, Graystone"
    finally:
        (data_dir / "consultant_map.json").unlink()
        present.consultant_map.cache_clear()
    (data_dir / "consultant_map.csv").write_text("raw,canonical\nBogdahn Group,Mariner\n")
    present.consultant_map.cache_clear()
    try:
        assert present.consultant_name("The Bogdahn Group") == "The Bogdahn Group"  # exact key only, no guessing
        assert present.consultant_name("Bogdahn Group") == "Mariner"
    finally:
        (data_dir / "consultant_map.csv").unlink()
        present.consultant_map.cache_clear()


def test_hf_strip_picks_recent_hedge_fund_hires_and_searches():
    from radar import db
    from radar.extract import extractor as X
    from tests.test_extractor import FakeProvider, _setup

    sha = _setup()
    recent = (date.today() - timedelta(days=30)).isoformat()
    sigs = [X.XSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date=recent,
                      summary="The Board voted to issue an RFP for a hedge fund of funds manager.", page=1,
                      quote="The Board voted to issue a Request for Proposals for a hedge fund of funds manager.", confidence=0.9)]
    X.extract_document(sha, "canary", provider=FakeProvider(X.XResult(signals=sigs, mandates=[], officers=[], consultants=[])))
    with db.connect() as con:
        con.execute("UPDATE signals SET review_status='auto_approved'")
        plans = [{"allocator_id": "canary", "display_name": "Canary P&F", "consultant": {"consultant": "AndCo Consulting"}}]
        rows = present.hf_highlights(con, plans)
        assert len(rows) == 1 and rows[0]["kind"] == "Search" and rows[0]["consultant"] == "AndCo Consulting"
        con.execute("UPDATE signals SET review_status='new'")
        assert present.hf_highlights(con, plans) == []  # pending signals never show
        con.execute(f"UPDATE signals SET review_status='approved', event_date = DATE '{date.today() - timedelta(days=400)}'")
        assert present.hf_highlights(con, plans) == []  # older than 12 months


def test_consultant_aliases_file_from_session_a():
    """data/consultant_aliases.yaml (D-B18): canonical -> aliases; names under unsure stay as printed."""
    from radar import settings as S

    d = S.get_settings().data_dir
    (d / "consultant_aliases.yaml").write_text(
        "canonical:\n  Mariner Institutional:\n    - AndCo Consulting\n    - The Bogdahn Group\n"
        "unsure:\n  - Cambridge\n")
    present.consultant_map.cache_clear()
    try:
        assert present.consultant_name("The Bogdahn Group") == "Mariner Institutional"
        assert present.consultant_name("Mariner Institutional") == "Mariner Institutional"
        assert present.consultant_name("Cambridge") == "Cambridge"
    finally:
        (d / "consultant_aliases.yaml").unlink()
        present.consultant_map.cache_clear()


def test_is_hf_needs_flag_bucket_or_summary_wording_not_a_passing_quote():
    lcv = {"summary": "The Board approved a search for a large cap value manager.", "bucket": "LONG_ONLY_EQUITY",
           "evidence": [{"quote": "... the hedge fund allocation was discussed earlier; then a large cap value search ..."}]}
    assert not present.is_hf(lcv)  # a passing mention of hedge funds in the quote is not enough
    assert present.is_hf({"summary": "The Board approved a $50 million risk-mitigation strategies mandate."})
    assert present.is_hf({"summary": "Commitment to Fund X.", "hf_relevant": True})
    assert present.is_hf({"summary": "x", "bucket": "GLOBAL_MACRO"})
    assert not present.is_hf({"summary": "The Board hired Holland & Knight LLP as outside counsel."})


def test_ef_strip_drops_contractor_listings_that_are_not_hedge_fund_services():
    from radar.sources.endowments.views import strip_qualifies

    assert strip_qualifies({"signal_type": "EF_ALT_HOLDING_ADDED",
                            "summary": '"HEDGE FUNDS" ($1,012,985) appears on the TY2023 return and not on the TY2022 return.'})
    for firm in ("HOLLAND & KNIGHT LLP (investment management)", "PNC BANK NATIONAL ASSOCIATION (investment services)",
                 "LORD ABBETT & CO LLC (investment services)", "SEI INVESTMENTS (investment management)",
                 "STIFEL NICOLAUS & COMPANY (investment consulting)"):
        assert not strip_qualifies({"signal_type": "EF_MANAGER_ADDED",
                                    "summary": f"{firm} is listed among the five highest-paid independent contractors."})


def test_doc_when_marks_future_documents_as_upcoming_meetings():
    today = date(2026, 11, 11)
    assert present.doc_when(date(2026, 11, 12), today) == "upcoming meeting 2026-11-12"
    assert present.doc_when(date(2026, 11, 11), today) == "2026-11-11"
    assert present.doc_when(None, today) == ""


def test_approval_labels_show_the_real_state():
    crawled = {"review_status": "auto_approved", "evidence": [{"source_url": "https://x.gov/a.pdf", "provenance": "CRAWL"}]}
    manual = {"review_status": "auto_approved", "evidence": [{"source_url": "manual://fl-sba-frs/a.pdf"}]}
    assert present.approval({"review_status": "approved"})["label"] == "reviewed"
    assert present.approval(manual)["label"] == "auto-approved (manual source)"
    assert present.approval(crawled)["label"] == "awaiting review"
    assert present.approval({"review_status": "new"})["label"] == "pending review"


def test_future_dated_document_is_not_newest_and_does_not_make_coverage_recent():
    from radar import coverage, db, queries
    from tests.test_extractor import _setup

    sha = _setup()
    soon, old = date.today() + timedelta(days=5), date.today() - timedelta(days=400)
    with db.connect() as con:
        con.execute("UPDATE document_urls SET doc_date = ? WHERE sha256 = ?", [old, sha])
        con.execute("""INSERT INTO document_urls SELECT * REPLACE ('https://canary.invalid/next.pdf' AS url, ?::DATE AS doc_date)
                       FROM document_urls WHERE sha256 = ?""", [soon, sha])
        row = [a for a in queries.allocator_table(con, scoped=False) if a["allocator_id"] == "canary"][0]
        assert row["newest_doc_date"] == old and row["upcoming_doc_date"] == soon
        assert present.coverage_note(row["newest_doc_date"]) is not None  # not "current"
        reasons = coverage.bar(con, {"allocator_id": "canary", "access": "AUTO"})
        assert any("no document dated in the last 6 months" in r for r in reasons)


def test_spot_check_parsing_takes_the_latest_and_ignores_bad_lines():
    from radar.scorecard import latest_spot_check

    text = """### 2026-11-10 DATA SPOT-CHECK
SPOT-CHECK 2026-11-10: 40 signals checked, 37 correct, accuracy 92.5%, method x
### 2026-11-12 DATA SPOT-CHECK
SPOT-CHECK 2026-11-12: 50 signals checked, 48 correct, accuracy 96%, method y
SPOT-CHECK 2026-11-13: 5 signals checked, 9 correct"""
    assert latest_spot_check(text) == {"date": "2026-11-12", "checked": 50, "correct": 48, "pct": 96.0}
    assert latest_spot_check("nothing here") is None


def test_how_page_shows_scorecard(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from radar.web.app import create_app

    h = tmp_path / "HANDOFF.md"
    h.write_text("SPOT-CHECK 2026-11-11: 20 signals checked, 19 correct, accuracy 95%, method spot\n")
    monkeypatch.setenv("RADAR_HANDOFF_PATH", str(h))
    body = TestClient(create_app()).get("/how").text
    assert "Data quality" in body and "95.0%" in body and "19 of 20 signals correct" in body


def test_strategy_label_prefers_stored_bucket_then_type_then_inferred_wording():
    sl = present.strategy_label
    assert sl({"bucket": "REAL_ESTATE", "bucket_label": "Real Estate"}) == {"text": "real estate", "inferred": False}
    assert sl({"summary": "The Board voted to retain Eagle Capital as its large cap value domestic equity manager."}) \
        == {"text": "long only equity", "inferred": True}
    assert sl({"summary": "Custodian finalist presentations for the fixed income portfolio."})["text"] == "service provider"
    assert sl({"signal_type": "IPS_AMENDED", "summary": "The Board amended the IPS."}) == {"text": "total plan", "inferred": False}
    assert sl({"signal_type": "COMMITMENT_MADE", "summary": "Approved $5M to Affiliated Fund II."})["text"] == "not classified"
