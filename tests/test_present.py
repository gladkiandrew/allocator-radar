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
